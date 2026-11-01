"""Unit tests for hardware collectors using synthetic sysfs/procfs trees."""

from __future__ import annotations

from pathlib import Path

from aw_m17_diag.collectors.pcie_iommu import collect_pcie_iommu
from aw_m17_diag.collectors.storage import collect_storage
from aw_m17_diag.collectors.thermal_power import collect_thermal_power


def test_collect_storage_detects_sm2268xt_quirks(tmp_path: Path) -> None:
    """Verify SM2268XT APST latency, bogus 0xFFFE Kelvin hwmon max, and dmesg CRTO detection."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"

    nvme_dir = sys_root / "class" / "nvme" / "nvme0"
    (nvme_dir / "device").mkdir(parents=True)
    (nvme_dir / "model").write_text("Inland QN450 NVMe SSD\n", encoding="utf-8")
    (nvme_dir / "firmware_rev").write_text("Z0302B00\n", encoding="utf-8")
    (nvme_dir / "device" / "vendor").write_text("0x126f\n", encoding="utf-8")
    (nvme_dir / "device" / "device").write_text("0x2268\n", encoding="utf-8")

    apst_dir = sys_root / "module" / "nvme_core" / "parameters"
    apst_dir.mkdir(parents=True)
    (apst_dir / "default_ps_max_latency_us").write_text("100000\n", encoding="utf-8")

    hwmon_dir = sys_root / "class" / "hwmon" / "hwmon3"
    hwmon_dir.mkdir(parents=True)
    (hwmon_dir / "name").write_text("nvme\n", encoding="utf-8")
    (hwmon_dir / "temp1_input").write_text("31850\n", encoding="utf-8")
    (hwmon_dir / "temp2_input").write_text("32850\n", encoding="utf-8")
    (hwmon_dir / "temp3_input").write_text("36850\n", encoding="utf-8")
    (hwmon_dir / "temp2_max").write_text("65261850\n", encoding="utf-8")

    proc_root.mkdir(parents=True)
    (proc_root / "cmdline").write_text(
        "BOOT_IMAGE=/boot/vmlinuz ro quiet splash\n", encoding="utf-8"
    )

    klog = (
        "nvme nvme0: bad crto:14 cap:8100030f00100ff\n"
        "nvme nvme0: using unchecked data buffer\n"
        "block nvme0n1: No UUID available providing old NGUID\n"
    )

    snap = collect_storage(sys_root=sys_root, proc_root=proc_root, klog=klog)
    assert snap.pci_id == "126f:2268"
    assert snap.model == "Inland QN450 NVMe SSD"
    assert snap.apst_max_latency_us == 100000
    assert not snap.cmdline_apst_disabled
    assert snap.composite_temp_c == 31.85
    assert snap.sensor2_ctrl_temp_c == 36.85
    assert snap.bogus_hwmon_max_detected
    assert snap.dmesg_bad_crto_detected
    assert snap.dmesg_unchecked_buffer_detected
    assert snap.dmesg_old_nguid_detected


def test_collect_pcie_iommu_counts_dmar_faults(tmp_path: Path) -> None:
    """Verify QCA6174 DMAR write fault parsing from kernel logs."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"
    (sys_root / "bus" / "pci" / "devices" / "0000:70:00.0").mkdir(parents=True)
    proc_root.mkdir(parents=True)
    (proc_root / "cmdline").write_text("ro quiet splash\n", encoding="utf-8")

    klog = (
        "iommu: Default domain type: Translated\n"
        "ath10k_pci 0000:70:00.0: firmware ver WLAN.RM.4.4.1-00309- api 6\n"
        "DMAR: [DMA Write NO_PASID] Request device [70:00.0] fault addr 0xfe975000 [fault reason 0x05] PTE Write access is not set\n"
        "DMAR: [DMA Write NO_PASID] Request device [70:00.0] fault addr 0xfc61a000 [fault reason 0x05] PTE Write access is not set\n"
        "pci 0000:6f:00.0: [Firmware Bug]: disabling VPD access (can't determine size of non-standard VPD format)\n"
    )

    snap = collect_pcie_iommu(sys_root=sys_root, proc_root=proc_root, klog=klog)
    assert snap.ath10k_pci_present
    assert snap.dmar_fault_count_current_boot == 2
    assert snap.dmar_recent_fault_addrs == ("[70:00.0]@0xfe975000", "[70:00.0]@0xfc61a000")
    assert snap.killer_e2500_vpd_bug_logged


def test_collect_thermal_power_reads_rapl_and_alienware_wmi(tmp_path: Path) -> None:
    """Verify MSR/MMIO RAPL and alienware_wmi hwmon collection."""
    sys_root = tmp_path / "sys"
    aw_hwmon = sys_root / "class" / "hwmon" / "hwmon5"
    aw_hwmon.mkdir(parents=True)
    (aw_hwmon / "name").write_text("alienware_wmi\n", encoding="utf-8")
    (aw_hwmon / "fan1_input").write_text("2100\n", encoding="utf-8")
    (aw_hwmon / "fan2_input").write_text("2200\n", encoding="utf-8")
    (aw_hwmon / "fan1_boost").write_text("0\n", encoding="utf-8")
    (aw_hwmon / "fan2_boost").write_text("0\n", encoding="utf-8")

    rapl_msr = sys_root / "class" / "powercap" / "intel-rapl:0"
    rapl_msr.mkdir(parents=True)
    (rapl_msr / "constraint_0_power_limit_uw").write_text("200000000\n", encoding="utf-8")
    (rapl_msr / "constraint_1_power_limit_uw").write_text("90000000\n", encoding="utf-8")

    klog = "ACPI: thermal: [Firmware Bug]: Invalid critical threshold (-274000)\n"
    snap = collect_thermal_power(sys_root=sys_root, klog=klog)
    assert snap.cpu_fan_rpm == 2100
    assert snap.gpu_fan_rpm == 2200
    assert snap.msr_pl1_w == 200.0
    assert snap.msr_pl2_w == 90.0
    assert snap.acpi_invalid_crit_temp_bug
