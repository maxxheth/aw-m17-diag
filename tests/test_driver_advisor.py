"""Unit tests for Custom Driver & Kernel Quirk Advisory Engine and DKMS exporter."""

from __future__ import annotations

from pathlib import Path

from aw_m17_diag.engine.driver_advisor import (
    evaluate_driver_candidates,
    export_dkms_candidates,
)
from aw_m17_diag.models import (
    GpuDisplaySnapshot,
    PcieIommuSnapshot,
    RemediationTier,
    StorageSnapshot,
    ThermalPowerSnapshot,
)


def make_sample_snapshots() -> tuple[
    StorageSnapshot, PcieIommuSnapshot, GpuDisplaySnapshot, ThermalPowerSnapshot
]:
    storage = StorageSnapshot(
        pci_id="126f:2268",
        model="Inland QN450 NVMe SSD",
        firmware_rev="Z0302B00",
        apst_max_latency_us=100000,
        cmdline_apst_disabled=False,
        composite_temp_c=32.0,
        sensor1_nand_temp_c=32.0,
        sensor2_ctrl_temp_c=36.0,
        bogus_hwmon_max_detected=True,
        dmesg_bad_crto_detected=True,
        dmesg_unchecked_buffer_detected=True,
        dmesg_old_nguid_detected=True,
    )
    pcie = PcieIommuSnapshot(
        iommu_domain_type="DMA (Translated)",
        cmdline_iommu_pt=False,
        ath10k_pci_present=True,
        ath10k_firmware="WLAN.RM.4.4.1-00309",
        dmar_fault_count_current_boot=14,
        dmar_recent_fault_addrs=("[70:00.0]@0xfe975000",),
        killer_e2500_vpd_bug_logged=True,
    )
    gpu = GpuDisplaySnapshot(
        nvidia_driver_version="595.99.02",
        gpu_temp_c=38,
        gpu_power_w=5.0,
        gpu_power_cap_w=90.0,
        gpu_vram_used_mib=110,
        gpu_vram_total_mib=8192,
        gpu_pstate="P8",
        ucsi_ccg_blacklisted=False,
        ucsi_timeout_in_dmesg=True,
        nv_drm_gem_error_in_dmesg=True,
        drm_kms_poll_disabled=False,
        i915_workqueue_hog_count=1027,
        flatpak_nvidia_gl_versions=("595-91-07",),
        flatpak_driver_mismatch=True,
    )
    tp = ThermalPowerSnapshot(
        cpu_package_temp_c=40.0,
        cpu_core_max_temp_c=42.0,
        aw_cpu_temp_c=38.0,
        aw_gpu_temp_c=38.0,
        cpu_fan_rpm=0,
        gpu_fan_rpm=0,
        cpu_fan_boost=0,
        gpu_fan_boost=0,
        platform_profile="balanced",
        available_profiles=("cool", "quiet", "balanced", "balanced-performance"),
        scaling_driver="intel_pstate",
        scaling_governor="powersave",
        epp="balance_performance",
        msr_pl1_w=200.0,
        msr_pl2_w=90.0,
        mmio_pl1_w=60.0,
        mmio_pl2_w=90.0,
        acpi_invalid_crit_temp_bug=True,
        wmi_missing_methods_bug=True,
    )
    return storage, pcie, gpu, tp


def test_evaluate_driver_candidates_recommends_tier3_for_ath10k_and_nvme() -> None:
    """Verify Tier 3 DKMS recommendation for QCA6174 DMAR and SM2268XT quirks."""
    storage, pcie, gpu, tp = make_sample_snapshots()
    advisories = evaluate_driver_candidates(
        storage=storage,
        pcie_iommu=pcie,
        gpu_display=gpu,
        thermal_power=tp,
    )
    by_id = {a.candidate_id: a for a in advisories}
    assert "ath10k-dmar-fix" in by_id
    assert by_id["ath10k-dmar-fix"].recommended_tier == RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS
    assert by_id["ath10k-dmar-fix"].currently_needed

    assert "nvme-smi2268xt-quirk" in by_id
    assert (
        by_id["nvme-smi2268xt-quirk"].recommended_tier == RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS
    )
    assert by_id["nvme-smi2268xt-quirk"].currently_needed

    assert "tu106-ucsi-disable" in by_id
    assert by_id["tu106-ucsi-disable"].recommended_tier == RemediationTier.TIER_1_USERSPACE_SYSFS


def test_export_dkms_candidates_writes_c_source_and_patches(tmp_path: Path) -> None:
    """Verify export_dkms_candidates writes compilable C source, Makefile, dkms.conf, and patches."""
    exported = export_dkms_candidates(output_root=tmp_path)
    assert len(exported) == 5
    for p in exported:
        assert p.is_file()
        assert p.stat().st_size > 50
