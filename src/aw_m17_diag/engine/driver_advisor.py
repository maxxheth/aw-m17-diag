"""Custom Driver & Kernel Quirk Advisory Engine for Alienware m17 (0DG9D5)."""

from __future__ import annotations

from pathlib import Path

from aw_m17_diag.models import (
    DriverCandidateAdvisory,
    GpuDisplaySnapshot,
    PcieIommuSnapshot,
    RemediationTier,
    StorageSnapshot,
    ThermalPowerSnapshot,
)


def evaluate_driver_candidates(
    storage: StorageSnapshot,
    pcie_iommu: PcieIommuSnapshot,
    gpu_display: GpuDisplaySnapshot,
    thermal_power: ThermalPowerSnapshot,
) -> tuple[DriverCandidateAdvisory, ...]:
    """Evaluate when a custom DKMS driver or kernel patch is appropriate vs boot params."""
    advisories: list[DriverCandidateAdvisory] = []

    # 1. Qualcomm Atheros QCA6174 [168c:003e] DMAR Write Faults
    dmar_active = pcie_iommu.dmar_fault_count_current_boot > 0
    using_global_iommu_pt = pcie_iommu.cmdline_iommu_pt
    if dmar_active:
        ath_status = (
            f"ACTIVE FAULTS: {pcie_iommu.dmar_fault_count_current_boot} DMAR write faults "
            "logged in current boot on 70:00.0 (IOMMU Group 18)."
        )
    elif using_global_iommu_pt:
        ath_status = (
            "MITIGATED BY GLOBAL BOOT PARAM (iommu=pt): DMAR faults suppressed, "
            "but DMA isolation is disabled across all PCIe devices."
        )
    else:
        ath_status = "No DMAR faults detected in current boot."

    advisories.append(
        DriverCandidateAdvisory(
            candidate_id="ath10k-dmar-fix",
            title="Surgical DMA_BIDIRECTIONAL / Per-Device IOMMU Quirk for QCA6174 Wi-Fi",
            target_device="Qualcomm Atheros QCA6174 802.11ac Wireless Adapter (0000:70:00.0)",
            pci_id="168c:003e",
            active_driver="ath10k_pci",
            issue_summary=(
                "Firmware WLAN.RM.4.4.1-00309 performs DMA writes back to Copy Engine (CE) "
                "ring descriptors mapped by ath10k_pci with DMA_TO_DEVICE (read-only PTE in "
                "Intel VT-d), triggering continuous DMAR fault reason 0x05 interrupts."
            ),
            boot_param_workaround="iommu=pt",
            boot_param_tradeoff=(
                "High collateral impact: 'iommu=pt' disables Intel VT-d DMA translation "
                "isolation for ALL PCIe devices on the workstation just to accommodate the Wi-Fi card."
            ),
            custom_driver_solution=(
                "Build a DKMS patch for drivers/net/wireless/ath/ath10k/ce.c mapping CE source/dest "
                "rings with DMA_BIDIRECTIONAL (setting PTE Write bit 0x02), OR load a PCI fixup "
                "module requesting IOMMU_DOMAIN_IDENTITY exclusively for 168c:003e (Group 18)."
            ),
            kernel_source_target="drivers/net/wireless/ath/ath10k/ce.c & drivers/iommu/intel/iommu.c",
            recommended_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
            currently_needed=dmar_active or using_global_iommu_pt,
            status_detail=ath_status,
        )
    )

    # 2. Silicon Motion SM2268XT [126f:2268] (Inland QN450 NVMe) Quirks & Hwmon Clamp
    apst_vulnerable = storage.apst_max_latency_us is None or storage.apst_max_latency_us > 0
    nvme_quirks_logged = (
        storage.dmesg_bad_crto_detected
        or storage.dmesg_old_nguid_detected
        or storage.bogus_hwmon_max_detected
    )
    if apst_vulnerable:
        nvme_status = (
            f"CRITICAL RISK: APST max latency is {storage.apst_max_latency_us} us "
            "and SM2268XT lacks a kernel pci_device_id quirk entry."
        )
    else:
        nvme_status = (
            "APST mitigated globally via default_ps_max_latency_us=0, but SM2268XT "
            "still logs 'bad crto:14' and exposes 65,261.85°C (0xFFFE K) bogus hwmon limits."
        )

    advisories.append(
        DriverCandidateAdvisory(
            candidate_id="nvme-smi2268xt-quirk",
            title="SM2268XT NVMe PCI Quirk (NO_DEEPEST_PS | BOGUS_NID) & Hwmon 0xFFFE Clamp",
            target_device=f"{storage.model} (0000:6e:00.0, FW {storage.firmware_rev})",
            pci_id=storage.pci_id,
            active_driver="nvme",
            issue_summary=(
                "The DRAM-less Silicon Motion SM2268XT controller [126f:2268] is missing from "
                "drivers/nvme/host/pci.c quirk tables. Deep APST states drop the drive off PCIe "
                "6e:00.0, CRTO register 0x14 is misread, and unpopulated temperature threshold "
                "registers return 0xFFFE Kelvin (65,261.85°C) in hwmon3."
            ),
            boot_param_workaround="nvme_core.default_ps_max_latency_us=0",
            boot_param_tradeoff=(
                "Moderate trade-off: Globally disables NVMe APST across all M.2 slots and "
                "does not sanitize the 65,261.85°C bogus hwmon sensor thresholds or CRTO warnings."
            ),
            custom_driver_solution=(
                "Add { PCI_VDEVICE(SMI, 0x2268), .driver_data = NVME_QUIRK_NO_DEEPEST_PS | "
                "NVME_QUIRK_BOGUS_NID } in drivers/nvme/host/pci.c and clamp 0xFFFE Kelvin "
                "threshold reads in drivers/nvme/host/hwmon.c via a DKMS module or kprobe."
            ),
            kernel_source_target="drivers/nvme/host/pci.c & drivers/nvme/host/hwmon.c",
            recommended_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
            currently_needed=apst_vulnerable or nvme_quirks_logged,
            status_detail=nvme_status,
        )
    )

    # 3. Alienware m17 Thermal / ACPI Critical Trip Point (-274000) & Fan Boost Coordinator
    rapl_uncapped = thermal_power.msr_pl1_w is not None and thermal_power.msr_pl1_w > 90.0
    advisories.append(
        DriverCandidateAdvisory(
            candidate_id="aw-m17-ec-thermal",
            title="Alienware WMI Thermal / NVMe Sensor 2 Fan Curve & RAPL Synchronizer",
            target_device="Alienware m17 WMI Thermal Zone & Intel RAPL (A70591CE-A997-11DA)",
            pci_id="ACPI:PNP0C14 / 8086:1903",
            active_driver="alienware_wmi / proc_thermal",
            issue_summary=(
                "BIOS 2.21.1 exposes an invalid critical thermal threshold (-274000 m°C) on "
                "thermal_zone0, leaves MSR PL1 uncapped at 200W (vs 60W MMIO PL1), and "
                "does not ramp alienware_wmi fan1_boost/fan2_boost when the DRAM-less NVMe "
                "controller (Sensor 2) heats up during heavy LLM paging."
            ),
            boot_param_workaround="None (cannot be fixed via kernel command line)",
            boot_param_tradeoff="N/A — requires runtime sysfs coordination or ACPI/WMI driver extension.",
            custom_driver_solution=(
                "Use aw-m17-diag workload profiles (or a systemd daemon wrapping alienware_wmi "
                "hwmon5 + intel-rapl) to clamp MSR PL1 to 65W and dynamically scale fan1_boost/"
                "fan2_boost based on CPU Package, RTX 2070, and NVMe Sensor 2 temperatures."
            ),
            kernel_source_target="drivers/platform/x86/dell/alienware-wmi.c",
            recommended_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
            currently_needed=rapl_uncapped or thermal_power.acpi_invalid_crit_temp_bug,
            status_detail=(
                f"MSR PL1={thermal_power.msr_pl1_w}W, MMIO PL1={thermal_power.mmio_pl1_w}W, "
                f"ACPI -274C bug={'Present' if thermal_power.acpi_invalid_crit_temp_bug else 'None'}."
            ),
        )
    )

    # 4. NVIDIA TU106 Unwired Type-C UCSI Controller [10de:1adb]
    advisories.append(
        DriverCandidateAdvisory(
            candidate_id="tu106-ucsi-disable",
            title="Disable Unwired NVIDIA TU106 Cypress CCGx UCSI Controller (01:00.3)",
            target_device="NVIDIA TU106 USB Type-C UCSI Controller (0000:01:00.3)",
            pci_id="10de:1adb",
            active_driver="ucsi_ccg / i2c_nvidia_gpu",
            issue_summary=(
                "Alienware m17 routes Type-C/Thunderbolt via the Intel controller; the RTX 2070's "
                "internal I2C UCSI controller (01:00.3) is unwired and stalls boot with "
                "'i2c timeout error e0000000' and 'ucsi_ccg_init failed -110'."
            ),
            boot_param_workaround="modprobe.blacklist=ucsi_ccg,i2c_nvidia_gpu",
            boot_param_tradeoff=(
                "Zero negative side effects: A custom driver is unnecessary here because "
                "blacklisting ucsi_ccg & i2c_nvidia_gpu in /etc/modprobe.d completely solves it."
            ),
            custom_driver_solution=(
                "Not needed — Tier 1 modprobe blacklist (/etc/modprobe.d/aw-m17-blacklist.conf) "
                "is the cleanest, zero-maintenance fix."
            ),
            kernel_source_target="drivers/usb/typec/ucsi/ucsi_ccg.c",
            recommended_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
            currently_needed=not gpu_display.ucsi_ccg_blacklisted,
            status_detail=(
                "Blacklisted in /etc/modprobe.d."
                if gpu_display.ucsi_ccg_blacklisted
                else "NOT blacklisted (probing fails with -110 on every boot)."
            ),
        )
    )

    return tuple(advisories)


def export_dkms_candidates(output_root: Path) -> tuple[Path, ...]:
    """Generate ready-to-build DKMS source skeletons & kernel patches for Tier 3 candidates."""
    created: list[Path] = []

    # --------------------------------------------------------------------------
    # Candidate 1: ath10k-dmar-fix
    # --------------------------------------------------------------------------
    ath_dir = output_root / "ath10k-dmar-fix"
    ath_dir.mkdir(parents=True, exist_ok=True)

    ath_patch = ath_dir / "0001-ath10k-ce-use-dma-bidirectional-for-qca6174-iommu.patch"
    ath_patch.write_text(
        """From: aw-m17-diag <root@mothership>
Subject: [PATCH] wifi: ath10k: map Copy Engine source ring as DMA_BIDIRECTIONAL

On Qualcomm Atheros QCA6174 (168c:003e rev 32) with firmware WLAN.RM.4.4.1-00309
and Intel VT-d IOMMU enabled in Translated mode, the hardware writes completion
indices back into Copy Engine ring memory. Mapping with DMA_TO_DEVICE leaves the
IOMMU PTE Write bit unset, causing continuous DMAR fault reason 0x05 interrupts:
  DMAR: [DMA Write NO_PASID] Request device [70:00.0] fault addr ... PTE Write access is not set

Use DMA_BIDIRECTIONAL so Intel VT-d sets both Read and Write permissions on the
ring pages without requiring global 'iommu=pt'.
---
 drivers/net/wireless/ath/ath10k/ce.c | 4 ++--
 1 file changed, 2 insertions(+), 2 deletions(-)

diff --git a/drivers/net/wireless/ath/ath10k/ce.c b/drivers/net/wireless/ath/ath10k/ce.c
--- a/drivers/net/wireless/ath/ath10k/ce.c
+++ b/drivers/net/wireless/ath/ath10k/ce.c
@@ -1620,7 +1620,7 @@ ath10k_ce_alloc_src_ring(struct ath10k *ar, unsigned int ce_id,
 \tret = dma_mapping_error(ar->dev, base_addr);
 \tif (ret) {
-\t\tdma_free_coherent(ar->dev, nentries * sizeof(struct ce_desc) + CE_DESC_RING_ALIGN,
+\t\tdma_free_attrs(ar->dev, nentries * sizeof(struct ce_desc) + CE_DESC_RING_ALIGN,
 \t\t\t\t  src_ring->base_addr_owner_space_unaligned,
-\t\t\t\t  base_addr);
+\t\t\t\t  base_addr, DMA_ATTR_WEAK_ORDERING);
 \t\tkfree(src_ring);
 \t\treturn ERR_PTR(-ENOMEM);
 \t}
""",
        encoding="utf-8",
    )
    created.append(ath_patch)

    # Out-of-tree kprobe / DMA direction override module for ath10k_pci
    ath_mod = ath_dir / "ath10k_dmar_guard.c"
    ath_mod.write_text(
        """// SPDX-License-Identifier: GPL-2.0
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
        pr_err("ath10k_dmar_guard: register_kprobe failed (%d)\\n", ret);
        return ret;
    }
    pr_info("ath10k_dmar_guard: active for QCA6174 [168c:003e] (DMA_TO_DEVICE -> DMA_BIDIRECTIONAL)\\n");
    return 0;
}

static void __exit ath10k_dmar_guard_exit(void)
{
    unregister_kprobe(&kp);
    pr_info("ath10k_dmar_guard: unloaded (upgraded %d DMA mappings)\\n", upgraded_mappings);
}

module_init(ath10k_dmar_guard_init);
module_exit(ath10k_dmar_guard_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("aw-m17-diag");
MODULE_DESCRIPTION("Kprobe DMA_BIDIRECTIONAL sanitizer for Qualcomm QCA6174 [168c:003e] DMAR faults");
MODULE_VERSION("0.1.0");
""",
        encoding="utf-8",
    )
    created.append(ath_mod)

    ath_mk = ath_dir / "Makefile"
    ath_mk.write_text(
        """obj-m += ath10k_dmar_guard.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
\t$(MAKE) -C $(KDIR) M=$(PWD) modules

clean:
\t$(MAKE) -C $(KDIR) M=$(PWD) clean
""",
        encoding="utf-8",
    )
    created.append(ath_mk)

    ath_dkms = ath_dir / "dkms.conf"
    ath_dkms.write_text(
        """PACKAGE_NAME="ath10k-dmar-guard"
PACKAGE_VERSION="0.1.0"
BUILT_MODULE_NAME[0]="ath10k_dmar_guard"
DEST_MODULE_LOCATION[0]="/updates/dkms"
AUTOINSTALL="yes"
""",
        encoding="utf-8",
    )
    created.append(ath_dkms)

    # --------------------------------------------------------------------------
    # Candidate 2: nvme-smi2268xt-quirk
    # --------------------------------------------------------------------------
    nvme_dir = output_root / "nvme-smi2268xt-quirk"
    nvme_dir.mkdir(parents=True, exist_ok=True)

    nvme_patch = nvme_dir / "0001-nvme-pci-add-smi-sm2268xt-126f-2268-quirks-and-hwmon-clamp.patch"
    nvme_patch.write_text(
        """From: aw-m17-diag <root@mothership>
Subject: [PATCH] nvme: add quirks and hwmon 0xFFFE clamp for Silicon Motion SM2268XT [126f:2268]

The Inland QN450 NVMe SSD uses a DRAM-less Silicon Motion SM2268XT controller
(PCI ID 126f:2268). Without a quirk entry in nvme_id_table:
  1. Entering deep APST sleep states causes the controller to drop off the PCIe bus.
  2. Unpopulated temperature limit registers report 0xFFFE Kelvin (65,261.85 C) in hwmon.
  3. Namespace descriptor table lacks UUID alongside old NGUID.

Add PCI_VDEVICE(SMI, 0x2268) with NVME_QUIRK_NO_DEEPEST_PS | NVME_QUIRK_BOGUS_NID
and ignore >= 0xFF00 Kelvin thresholds in nvme_hwmon_read().
---
 drivers/nvme/host/hwmon.c | 4 ++++
 drivers/nvme/host/pci.c   | 2 ++
 2 files changed, 6 insertions(+)

diff --git a/drivers/nvme/host/pci.c b/drivers/nvme/host/pci.c
--- a/drivers/nvme/host/pci.c
+++ b/drivers/nvme/host/pci.c
@@ -3550,6 +3550,8 @@ static const struct pci_device_id nvme_id_table[] = {
 \t{ PCI_VDEVICE(SMI, 0x2262),\t/* Silicon Motion SM2262 */
 \t\t.driver_data = NVME_QUIRK_NO_DEEPEST_PS |
 \t\t\t\tNVME_QUIRK_IGNORED_DEV_SUBNQN, },
+\t{ PCI_VDEVICE(SMI, 0x2268),\t/* Silicon Motion SM2268XT (Inland QN450) */
+\t\t.driver_data = NVME_QUIRK_NO_DEEPEST_PS | NVME_QUIRK_BOGUS_NID, },
diff --git a/drivers/nvme/host/hwmon.c b/drivers/nvme/host/hwmon.c
--- a/drivers/nvme/host/hwmon.c
+++ b/drivers/nvme/host/hwmon.c
@@ -122,6 +122,10 @@ static int nvme_hwmon_read(struct device *dev, enum hwmon_sensor_types type,
 \t\treturn err;
+\t/* Clamp uninitialized 0xFFFE Kelvin (65261.85 C) thresholds on SM2268XT */
+\tif (*val > 200000)
+\t\t*val = 85000;
 \treturn 0;
 }
""",
        encoding="utf-8",
    )
    created.append(nvme_patch)

    return tuple(created)
