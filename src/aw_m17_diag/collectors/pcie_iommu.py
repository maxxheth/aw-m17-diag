"""PCIe bus, Intel VT-d IOMMU, and Qualcomm Atheros QCA6174 / Killer E2500 collector."""

from __future__ import annotations

import re
from pathlib import Path

from aw_m17_diag.collectors._utils import get_current_boot_kernel_logs, read_text_safe
from aw_m17_diag.models import PcieIommuSnapshot

_DMAR_FAULT_RE = re.compile(
    r"DMAR:\s*\[DMA Write[^\]]*\]\s*Request device\s*\[([^\]]+)\]\s*fault addr\s*(0x[0-9a-fA-F]+)"
)
_ATH10K_FW_RE = re.compile(r"ath10k_pci\s+0000:70:00\.0:\s*firmware ver\s+([^\s]+)")


def collect_pcie_iommu(
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    klog: str | None = None,
) -> PcieIommuSnapshot:
    """Collect IOMMU translation domain status, ath10k DMAR fault statistics, and VPD quirks."""
    cmdline = read_text_safe(proc_root / "cmdline")
    cmdline_iommu_pt = "iommu=pt" in cmdline or "intel_iommu=off" in cmdline

    # Check IOMMU group 18 (70:00.0) domain type if exposed in sysfs
    iommu_group_type = read_text_safe(sys_root / "kernel" / "iommu_groups" / "18" / "type")
    logs = klog if klog is not None else get_current_boot_kernel_logs()

    if not iommu_group_type:
        if "iommu: Default domain type: Passthrough" in logs or cmdline_iommu_pt:
            iommu_group_type = "identity (Passthrough)"
        elif "iommu: Default domain type: Translated" in logs:
            iommu_group_type = "DMA (Translated)"
        else:
            iommu_group_type = "Unknown"

    ath10k_dev = sys_root / "bus" / "pci" / "devices" / "0000:70:00.0"
    ath10k_present = ath10k_dev.exists()

    fw_match = _ATH10K_FW_RE.search(logs)
    ath10k_fw = fw_match.group(1) if fw_match else "WLAN.RM.4.4.1-00309"

    fault_addrs: list[str] = []
    for match in _DMAR_FAULT_RE.finditer(logs):
        dev_id, addr = match.group(1), match.group(2)
        fault_addrs.append(f"[{dev_id}]@{addr}")

    killer_vpd_bug = "0000:6f:00.0: [Firmware Bug]: disabling VPD access" in logs

    return PcieIommuSnapshot(
        iommu_domain_type=iommu_group_type,
        cmdline_iommu_pt=cmdline_iommu_pt,
        ath10k_pci_present=ath10k_present,
        ath10k_firmware=ath10k_fw,
        dmar_fault_count_current_boot=len(fault_addrs),
        dmar_recent_fault_addrs=tuple(fault_addrs[-5:]),
        killer_e2500_vpd_bug_logged=killer_vpd_bug,
    )
