// SPDX-License-Identifier: GPL-2.0
/*
 * ath10k_dmar_guard.c - Out-of-tree Kprobe DMA direction sanitizer for QCA6174 (168c:003e)
 *
 * Intercepts dma_map_single_attrs / iommu_map calls originating from device 0000:70:00.0
 * (Qualcomm Atheros QCA6174) and upgrades DMA_TO_DEVICE (1) -> DMA_BIDIRECTIONAL (0)
 * so Intel VT-d IOMMU sets PTE Write access, eliminating DMAR fault reason 0x05
 * without disabling system-wide IOMMU via 'iommu=pt'.
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/kprobes.h>
#include <linux/pci.h>
#include <linux/dma-direction.h>

#define QCA_VENDOR_ID 0x168c
#define QCA6174_DEV_ID 0x003e

static int upgraded_mappings;

static int handler_pre(struct kprobe *p, struct pt_regs *regs)
{
    struct device *dev = (struct device *)regs->di;
    enum dma_data_direction dir = (enum dma_data_direction)regs->r8;

    if (!dev || !dev_is_pci(dev))
        return 0;

    struct pci_dev *pdev = to_pci_dev(dev);
    if (pdev->vendor == QCA_VENDOR_ID && pdev->device == QCA6174_DEV_ID) {
        if (dir == DMA_TO_DEVICE) {
            regs->r8 = (unsigned long)DMA_BIDIRECTIONAL;
            upgraded_mappings++;
        }
    }
    return 0;
}

static struct kprobe kp = {
    .symbol_name = "dma_map_page_attrs",
    .pre_handler = handler_pre,
};

static int __init ath10k_dmar_guard_init(void)
{
    int ret = register_kprobe(&kp);
    if (ret < 0) {
        pr_err("ath10k_dmar_guard: register_kprobe failed (%d)\n", ret);
        return ret;
    }
    pr_info("ath10k_dmar_guard: active for QCA6174 [168c:003e] (DMA_TO_DEVICE -> DMA_BIDIRECTIONAL)\n");
    return 0;
}

static void __exit ath10k_dmar_guard_exit(void)
{
    unregister_kprobe(&kp);
    pr_info("ath10k_dmar_guard: unloaded (upgraded %d DMA mappings)\n", upgraded_mappings);
}

module_init(ath10k_dmar_guard_init);
module_exit(ath10k_dmar_guard_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("aw-m17-diag");
MODULE_DESCRIPTION("Kprobe DMA_BIDIRECTIONAL sanitizer for Qualcomm QCA6174 [168c:003e] DMAR faults");
MODULE_VERSION("0.1.0");
