"""Strictly typed domain models for Alienware m17 (0DG9D5) hardware diagnostics."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class Severity(StrEnum):
    """Health check severity levels."""

    PASS = "PASS"
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class RemediationTier(StrEnum):
    """Classification of how a hardware/kernel issue should be solved."""

    TIER_1_USERSPACE_SYSFS = "Tier 1: Userspace / Sysfs / Modprobe"
    TIER_2_BOOT_PARAM = "Tier 2: Kernel Boot Parameter"
    TIER_3_CUSTOM_DRIVER_DKMS = "Tier 3: Custom DKMS Driver / Kernel Quirk"


class WorkloadProfileName(StrEnum):
    """Supported Alienware m17 tuning profiles."""

    OLLAMA_HEAVY = "ollama-heavy"
    GAMING_PRIME = "gaming-prime"
    BALANCED_DAILY = "balanced-daily"
    POWERSAVE = "powersave"


@dataclass(frozen=True, slots=True)
class HealthFinding:
    """Single evaluated diagnostic check and its remediation guidance."""

    check_id: str
    subsystem: str
    title: str
    severity: Severity
    current_value: str
    expected_value: str
    explanation: str
    remediation_tier: RemediationTier
    remediation_cmd: str


@dataclass(frozen=True, slots=True)
class StorageSnapshot:
    """Inland QN450 (Silicon Motion SM2268XT [126f:2268]) NVMe telemetry."""

    pci_id: str
    model: str
    firmware_rev: str
    apst_max_latency_us: int | None
    cmdline_apst_disabled: bool
    composite_temp_c: float | None
    sensor1_nand_temp_c: float | None
    sensor2_ctrl_temp_c: float | None
    bogus_hwmon_max_detected: bool
    dmesg_bad_crto_detected: bool
    dmesg_unchecked_buffer_detected: bool
    dmesg_old_nguid_detected: bool


@dataclass(frozen=True, slots=True)
class MemorySnapshot:
    """RAM, Swap, and VM writeback telemetry."""

    ram_total_gib: float
    ram_available_gib: float
    ram_used_percent: float
    swap_total_gib: float
    swap_free_gib: float
    swap_used_percent: float
    swappiness: int
    vfs_cache_pressure: int
    dirty_ratio: int
    dirty_background_ratio: int
    dirty_pages_mib: float


@dataclass(frozen=True, slots=True)
class PcieIommuSnapshot:
    """PCIe bus, IOMMU domain, and Qualcomm Atheros QCA6174 / Killer E2500 telemetry."""

    iommu_domain_type: str
    cmdline_iommu_pt: bool
    ath10k_pci_present: bool
    ath10k_firmware: str
    dmar_fault_count_current_boot: int
    dmar_recent_fault_addrs: tuple[str, ...]
    killer_e2500_vpd_bug_logged: bool


@dataclass(frozen=True, slots=True)
class GpuDisplaySnapshot:
    """NVIDIA RTX 2070 Max-Q + Intel UHD 630 hybrid display telemetry."""

    nvidia_driver_version: str
    gpu_temp_c: int | None
    gpu_power_w: float | None
    gpu_power_cap_w: float | None
    gpu_vram_used_mib: int | None
    gpu_vram_total_mib: int | None
    gpu_pstate: str
    ucsi_ccg_blacklisted: bool
    ucsi_timeout_in_dmesg: bool
    nv_drm_gem_error_in_dmesg: bool
    drm_kms_poll_disabled: bool
    i915_workqueue_hog_count: int
    flatpak_nvidia_gl_versions: tuple[str, ...]
    flatpak_driver_mismatch: bool
    gpu_clock_graphics_mhz: int | None = None
    gpu_clock_mem_mhz: int | None = None
    gpu_sw_thermal_slowdown: bool = False


@dataclass(frozen=True, slots=True)
class ThermalPowerSnapshot:
    """CPU temperatures, Alienware WMI fans, ACPI platform profile, and RAPL limits."""

    cpu_package_temp_c: float | None
    cpu_core_max_temp_c: float | None
    aw_cpu_temp_c: float | None
    aw_gpu_temp_c: float | None
    cpu_fan_rpm: int | None
    gpu_fan_rpm: int | None
    cpu_fan_boost: int | None
    gpu_fan_boost: int | None
    platform_profile: str
    available_profiles: tuple[str, ...]
    scaling_driver: str
    scaling_governor: str
    epp: str
    msr_pl1_w: float | None
    msr_pl2_w: float | None
    mmio_pl1_w: float | None
    mmio_pl2_w: float | None
    acpi_invalid_crit_temp_bug: bool
    wmi_missing_methods_bug: bool


@dataclass(frozen=True, slots=True)
class BootCrashRecord:
    """Forensic summary of a single boot session from journalctl."""

    boot_offset: int
    boot_id: str
    first_entry: str
    last_entry: str
    clean_shutdown: bool
    abrupt_freeze_suspected: bool
    dmar_fault_count: int
    oom_or_sigkill_events: tuple[str, ...]
    last_active_processes: tuple[str, ...]
    summary: str


@dataclass(frozen=True, slots=True)
class DriverCandidateAdvisory:
    """Detailed assessment of a custom DKMS driver / kernel quirk candidate."""

    candidate_id: str
    title: str
    target_device: str
    pci_id: str
    active_driver: str
    issue_summary: str
    boot_param_workaround: str
    boot_param_tradeoff: str
    custom_driver_solution: str
    kernel_source_target: str
    recommended_tier: RemediationTier
    currently_needed: bool
    status_detail: str


@dataclass(frozen=True, slots=True)
class WorkloadProfileSpec:
    """Target hardware and kernel parameters for a specific workload profile."""

    name: WorkloadProfileName
    description: str
    platform_profile: str
    epp: str
    msr_pl1_w: int
    msr_pl2_w: int
    mmio_pl1_w: int
    mmio_pl2_w: int
    swappiness: int
    dirty_ratio: int
    dirty_background_ratio: int
    cpu_fan_boost: int
    gpu_fan_boost: int
    cpu_max_freq_khz: int = 4_500_000
    gpu_max_clock_mhz: int | None = None
    quiesce_unplugged_alx: bool = False


@dataclass(frozen=True, slots=True)
class StartupProfileStatus:
    """Current boot persistence and S3 resume hook configuration for workload profiles."""

    startup_profile: WorkloadProfileName | None
    session_profile: WorkloadProfileName | None
    systemd_enabled: bool
    systemd_unit_path: Path
    sleep_hook_installed: bool
    sleep_hook_path: Path


@dataclass(frozen=True, slots=True)
class SystemAuditReport:
    """Aggregated full-system snapshot and evaluated findings."""

    kernel_release: str
    cmdline: str
    storage: StorageSnapshot
    memory: MemorySnapshot
    pcie_iommu: PcieIommuSnapshot
    gpu_display: GpuDisplaySnapshot
    thermal_power: ThermalPowerSnapshot
    findings: tuple[HealthFinding, ...] = field(default_factory=tuple)
    driver_advisories: tuple[DriverCandidateAdvisory, ...] = field(default_factory=tuple)
