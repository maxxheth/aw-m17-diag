// SPDX-License-Identifier: GPL-2.0
/*
 * nvidia_power_unclamp.c - Clears post-S3-resume 30W / P5 (810 MHz VRAM) clamp
 * on Alienware m17 (0DG9D5) RTX 2070 Max-Q (10de:1f10).
 *
 * Root Cause:
 *   When resuming from S3 sleep, the Alienware EC briefly reports _PSR = 0
 *   (AC offline) while re-negotiating with the 240W adapter. nvidia.ko's
 *   rm_power_management(..., NV_PM_ACTION_RESUME) latches the 30.00W Battery /
 *   SW Thermal Slowdown state (P5, 810 MHz GDDR6) and nv_restore_user_channels()
 *   never re-invokes rm_power_source_change_event() or D-Notifier 0xD1.
 *   Furthermore, rm_power_source_change_event() early-returns if its cached
 *   static state (.bss+0x1fece0) already equals 0 (AC).
 *
 * Fix:
 *   1. Re-queries NVIF 0x12 via rm_request_dnotifier_state(sp, nv).
 *   2. Toggles rm_power_source_change_event(sp, 1) -> rm_power_source_change_event(sp, 0)
 *      to bypass the .bss+0x1fece0 cache and force RM control 0x2080205b (AC Power).
 *   3. Sends rm_acpi_notify(sp, nv, 0xc0) (RM control 0x00000122 GPS thermal/power refresh).
 *   4. Sends rm_acpi_notify(sp, nv, 0xd1) (RM control 0x20802092 D-Notifier Level 0 = 90W).
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/kprobes.h>
#include <linux/uaccess.h>

typedef unsigned long (*kallsyms_lookup_name_t)(const char *name);
typedef void (*rm_power_source_change_event_t)(void *sp, unsigned int on_battery);
typedef void (*rm_acpi_notify_t)(void *sp, void *nv, unsigned int event_type);
typedef void (*rm_request_dnotifier_state_t)(void *sp, void *nv);

struct nv_acpi_obj_view {
    void *sp;
    void *device;
    void *handle;
    void *notifier_data;
    int notify_handler_installed;
};

static struct kprobe kp_kallsyms = {
    .symbol_name = "kallsyms_lookup_name",
};

static struct nv_acpi_obj_view *find_validated_acpi_obj(void *nvl, size_t *found_offset)
{
    size_t offset;
    struct nv_acpi_obj_view *cand = NULL;
    struct nv_acpi_obj_view view;

    for (offset = 0x800; offset <= 0xa00; offset += sizeof(void *)) {
        if (copy_from_kernel_nofault(&cand, (char *)nvl + offset, sizeof(cand)) != 0)
            continue;
        if ((unsigned long)cand <= PAGE_OFFSET)
            continue;
        if (copy_from_kernel_nofault(&view, cand, sizeof(view)) != 0)
            continue;
        if (view.notifier_data == nvl && (unsigned long)view.sp > PAGE_OFFSET) {
            *found_offset = offset;
            return cand;
        }
    }
    return NULL;
}

static int __init nvidia_power_unclamp_init(void)
{
    kallsyms_lookup_name_t lookup;
    void **p_nv_linux_devices;
    void **p_psr_acpi_obj;
    void *nvl;
    struct nv_acpi_obj_view *acpi_obj = NULL;
    rm_power_source_change_event_t fn_pwr_src;
    rm_acpi_notify_t fn_acpi_notify;
    rm_request_dnotifier_state_t fn_req_dnotifier;
    size_t offset = 0;
    int ret;

    ret = register_kprobe(&kp_kallsyms);
    if (ret < 0) {
        pr_err("nvidia_power_unclamp: register_kprobe failed (%d)\n", ret);
        return ret;
    }
    lookup = (kallsyms_lookup_name_t)kp_kallsyms.addr;
    unregister_kprobe(&kp_kallsyms);

    if (!lookup)
        return -ENOENT;

    p_nv_linux_devices = (void **)lookup("nv_linux_devices");
    p_psr_acpi_obj = (void **)lookup("psr_nv_acpi_object");
    fn_pwr_src = (rm_power_source_change_event_t)lookup("rm_power_source_change_event");
    fn_acpi_notify = (rm_acpi_notify_t)lookup("rm_acpi_notify");
    fn_req_dnotifier = (rm_request_dnotifier_state_t)lookup("rm_request_dnotifier_state");

    if (!p_nv_linux_devices || !*p_nv_linux_devices || !fn_pwr_src || !fn_acpi_notify) {
        pr_err("nvidia_power_unclamp: required nvidia symbols not found\n");
        return -ENOENT;
    }

    nvl = *p_nv_linux_devices;
    acpi_obj = find_validated_acpi_obj(nvl, &offset);
    if (!acpi_obj) {
        pr_err("nvidia_power_unclamp: could not locate validated nv_acpi_object on nvl\n");
        return -EINVAL;
    }

    pr_info("nvidia_power_unclamp: nvl=%px nv_acpi_object=+0x%zx (sp=%px, psr_obj=%px)\n",
            nvl, offset, acpi_obj->sp, p_psr_acpi_obj ? *p_psr_acpi_obj : NULL);

    /* 1. Re-evaluate NVIF 0x12 D-Notifier state */
    if (fn_req_dnotifier)
        fn_req_dnotifier(acpi_obj->sp, nvl);

    /* 2. Toggle power source 1 -> 0 to bypass .bss+0x1fece0 cache and force 0x2080205b AC mode */
    fn_pwr_src(acpi_obj->sp, 1);
    fn_pwr_src(acpi_obj->sp, 0);

    /* 3. Refresh GPS (0xC0) and enforce D-Notifier Level 0 (0xD1 = 90W AC cap) */
    fn_acpi_notify(acpi_obj->sp, nvl, 0xc0);
    fn_acpi_notify(acpi_obj->sp, nvl, 0xd1);

    pr_info("nvidia_power_unclamp: AC power source (0x2080205b) & D-Notifier 0xD1 (90W) restored.\n");
    return 0;
}

static void __exit nvidia_power_unclamp_exit(void)
{
    pr_info("nvidia_power_unclamp: unloaded.\n");
}

module_init(nvidia_power_unclamp_init);
module_exit(nvidia_power_unclamp_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("aw-m17-diag");
MODULE_DESCRIPTION("Unclamps NVIDIA RTX 2070 Max-Q 30W/810MHz post-S3-resume state");
MODULE_VERSION("0.1.0");
