"""Workload profile manager for Alienware m17 (0DG9D5)."""

from __future__ import annotations

import os
from pathlib import Path

from aw_m17_diag.collectors._utils import find_hwmon_by_name
from aw_m17_diag.models import WorkloadProfileName, WorkloadProfileSpec

PROFILES: dict[WorkloadProfileName, WorkloadProfileSpec] = {
    WorkloadProfileName.OLLAMA_HEAVY: WorkloadProfileSpec(
        name=WorkloadProfileName.OLLAMA_HEAVY,
        description=(
            "Optimized for sustained multi-hour local LLM inference (AVX2 + CUDA). "
            "Clamps CPU PL1 to 65W / PL2 to 85W to prevent VRM/thermal runaway, "
            "sets swappiness=10 & dirty_ratio=10, and pre-stages fan boost."
        ),
        platform_profile="balanced-performance",
        epp="balance_performance",
        msr_pl1_w=65,
        msr_pl2_w=85,
        mmio_pl1_w=65,
        mmio_pl2_w=85,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=25,
        gpu_fan_boost=25,
    ),
    WorkloadProfileName.GAMING_PRIME: WorkloadProfileSpec(
        name=WorkloadProfileName.GAMING_PRIME,
        description=(
            "Optimized for RTX 2070 Max-Q 90W gaming & Steam PRIME offload. "
            "Prioritizes GPU thermal budget by capping sustained CPU PL1 at 55W "
            "(75W short burst) and disabling DRM KMS connector polling."
        ),
        platform_profile="balanced-performance",
        epp="balance_performance",
        msr_pl1_w=55,
        msr_pl2_w=75,
        mmio_pl1_w=55,
        mmio_pl2_w=75,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=35,
        gpu_fan_boost=35,
    ),
    WorkloadProfileName.BALANCED_DAILY: WorkloadProfileSpec(
        name=WorkloadProfileName.BALANCED_DAILY,
        description=(
            "Quiet, rock-solid daily development workstation profile. "
            "Locks CPU PL1 to Intel nominal 45W TDP (70W burst) with automatic fan curve."
        ),
        platform_profile="balanced",
        epp="balance_performance",
        msr_pl1_w=45,
        msr_pl2_w=70,
        mmio_pl1_w=45,
        mmio_pl2_w=70,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=0,
        gpu_fan_boost=0,
    ),
    WorkloadProfileName.POWERSAVE: WorkloadProfileSpec(
        name=WorkloadProfileName.POWERSAVE,
        description=(
            "Cool & quiet low-power profile. Caps CPU PL1 at 25W (35W burst) "
            "and sets ACPI platform_profile to quiet."
        ),
        platform_profile="quiet",
        epp="power",
        msr_pl1_w=25,
        msr_pl2_w=35,
        mmio_pl1_w=25,
        mmio_pl2_w=35,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=0,
        gpu_fan_boost=0,
    ),
}


def get_workload_profile(name: WorkloadProfileName | str) -> WorkloadProfileSpec:
    """Return the WorkloadProfileSpec for the given profile name."""
    key = WorkloadProfileName(name)
    return PROFILES[key]


def _write_sysfs(
    path: Path, value: str, applied: list[str], errors: list[str], dry_run: bool
) -> None:
    """Write a value to a sysfs/procfs file or record the dry-run operation."""
    if dry_run:
        applied.append(f"[DRY-RUN] {path} <- {value}")
        return
    try:
        path.write_text(f"{value}\n", encoding="utf-8")
        applied.append(f"{path} <- {value}")
    except OSError as exc:
        errors.append(f"{path}: {exc}")


def apply_workload_profile(
    spec: WorkloadProfileSpec,
    dry_run: bool = False,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Apply the specified WorkloadProfileSpec to live sysfs/procfs interfaces."""
    if not dry_run and os.geteuid() != 0:
        return (), ("Root privileges required to write sysfs/procfs profile settings (use sudo).",)

    applied: list[str] = []
    errors: list[str] = []

    # 1. Always enforce NVMe APST = 0 & DRM KMS Poll = N for stability
    nvme_apst = sys_root / "module" / "nvme_core" / "parameters" / "default_ps_max_latency_us"
    if nvme_apst.exists() or dry_run:
        _write_sysfs(nvme_apst, "0", applied, errors, dry_run)

    drm_poll = sys_root / "module" / "drm_kms_helper" / "parameters" / "poll"
    if drm_poll.exists() or dry_run:
        _write_sysfs(drm_poll, "N", applied, errors, dry_run)

    # 2. ACPI platform_profile
    acpi_prof = sys_root / "firmware" / "acpi" / "platform_profile"
    if acpi_prof.exists() or dry_run:
        _write_sysfs(acpi_prof, spec.platform_profile, applied, errors, dry_run)

    # 3. CPU Energy Performance Preference (EPP) across all cores
    cpu_root = sys_root / "devices" / "system" / "cpu"
    if cpu_root.is_dir():
        for epp_file in sorted(cpu_root.glob("cpu[0-9]*/cpufreq/energy_performance_preference")):
            _write_sysfs(epp_file, spec.epp, applied, errors, dry_run)

    # 4. MSR & MMIO RAPL Power Limits (micro-Watts)
    msr_rapl = sys_root / "class" / "powercap" / "intel-rapl:0"
    if msr_rapl.is_dir() or dry_run:
        _write_sysfs(
            msr_rapl / "constraint_0_power_limit_uw",
            str(spec.msr_pl1_w * 1_000_000),
            applied,
            errors,
            dry_run,
        )
        _write_sysfs(
            msr_rapl / "constraint_1_power_limit_uw",
            str(spec.msr_pl2_w * 1_000_000),
            applied,
            errors,
            dry_run,
        )

    mmio_rapl = sys_root / "class" / "powercap" / "intel-rapl-mmio:0"
    if mmio_rapl.is_dir() or dry_run:
        _write_sysfs(
            mmio_rapl / "constraint_0_power_limit_uw",
            str(spec.mmio_pl1_w * 1_000_000),
            applied,
            errors,
            dry_run,
        )
        _write_sysfs(
            mmio_rapl / "constraint_1_power_limit_uw",
            str(spec.mmio_pl2_w * 1_000_000),
            applied,
            errors,
            dry_run,
        )

    # 5. VM Sysctls
    vm_dir = proc_root / "sys" / "vm"
    if vm_dir.is_dir() or dry_run:
        _write_sysfs(vm_dir / "swappiness", str(spec.swappiness), applied, errors, dry_run)
        _write_sysfs(vm_dir / "dirty_ratio", str(spec.dirty_ratio), applied, errors, dry_run)
        _write_sysfs(
            vm_dir / "dirty_background_ratio",
            str(spec.dirty_background_ratio),
            applied,
            errors,
            dry_run,
        )

    # 6. Alienware WMI Fan Boost (hwmon5)
    aw_hwmon = find_hwmon_by_name("alienware_wmi", sys_root / "class" / "hwmon")
    if aw_hwmon is not None:
        _write_sysfs(aw_hwmon / "fan1_boost", str(spec.cpu_fan_boost), applied, errors, dry_run)
        _write_sysfs(aw_hwmon / "fan2_boost", str(spec.gpu_fan_boost), applied, errors, dry_run)

    return tuple(applied), tuple(errors)
