"""Workload profile manager for Alienware m17 (0DG9D5)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from aw_m17_diag.collectors._utils import find_hwmon_by_name, read_int_safe, run_cmd_safe
from aw_m17_diag.collectors.gpu_display import collect_gpu_display
from aw_m17_diag.models import WorkloadProfileName, WorkloadProfileSpec

_UNCLAMP_KO = Path(
    "/var/www/aw-m17-diag/dkms-candidates/nvidia-s3-power-unclamp/nvidia_power_unclamp.ko"
)

PROFILES: dict[WorkloadProfileName, WorkloadProfileSpec] = {
    WorkloadProfileName.OLLAMA_HEAVY: WorkloadProfileSpec(
        name=WorkloadProfileName.OLLAMA_HEAVY,
        description=(
            "Optimized for sustained multi-hour local LLM inference (AVX2 + CUDA). "
            "Clamps CPU PL1 to 65W / PL2 to 85W to prevent VRM/thermal runaway, "
            "sets swappiness=10 & dirty_ratio=10, and unclamps post-S3 30W GPU cap."
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
            "Balances shared CPU/GPU heatpipe with 55W/75W CPU RAPL & balance_performance EPP, "
            "disables DRM KMS polling, and clears post-S3-resume 30W / 810MHz NVIDIA clamp."
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


def write_fan_boost(
    path: Path,
    boost: int,
    platform_profile: str,
    applied: list[str],
    errors: list[str],
    dry_run: bool,
) -> None:
    """Write Alienware WMI fan*_boost, handling firmware EBADRQC (Errno 56) under preset profiles.

    On Alienware WMAX/AWCC firmware (such as 0DG9D5 BIOS 2.21.1), invoking WMI
    Thermal_Control (0x15) op 0x02 (AWCC_OP_SET_FAN_BOOST) while a preset ACPI
    platform_profile ('balanced-performance', 'balanced', 'quiet', 'cool') is active
    causes the EC to return AWCC_FAILURE_CODE (0xFFFFFFFF), which alienware-wmi-wmax
    translates to -EBADRQC ([Errno 56] Invalid request code) because manual fan boost
    overrides are locked unless platform_profile is 'custom' (0x00).
    """
    if dry_run:
        applied.append(f"[DRY-RUN] {path} <- {boost}")
        return

    current = read_int_safe(path)
    if boost == 0 and current == 0:
        applied.append(f"{path} == 0 (EC auto-curve active)")
        return

    try:
        path.write_text(f"{boost}\n", encoding="utf-8")
        applied.append(f"{path} <- {boost}")
    except OSError as exc:
        if exc.errno == errno.EBADRQC:
            applied.append(
                f"{path}: EC auto-curve managed by '{platform_profile}' profile "
                "(WMI 0x15 op 0x02 locks manual fan boost under preset profiles)"
            )
        else:
            errors.append(f"{path}: {exc}")


def _unclamp_nvidia_s3_power_state(applied: list[str], errors: list[str]) -> None:
    """Clear the NVIDIA RTX 2070 Max-Q post-S3-resume 30W / P5 (810 MHz) battery clamp."""
    gpu = collect_gpu_display(klog="")
    is_clamped = (gpu.gpu_power_cap_w is not None and gpu.gpu_power_cap_w < 60.0) or (
        gpu.gpu_sw_thermal_slowdown and (gpu.gpu_temp_c is not None and gpu.gpu_temp_c < 75)
    )
    if not is_clamped:
        return

    if not _UNCLAMP_KO.is_file() and (_UNCLAMP_KO.parent / "Makefile").is_file():
        run_cmd_safe(["make", "-C", str(_UNCLAMP_KO.parent)])

    if not _UNCLAMP_KO.is_file():
        errors.append(
            f"RTX 2070 Max-Q is clamped at {gpu.gpu_power_cap_w}W ({gpu.gpu_pstate}), "
            f"but {_UNCLAMP_KO} is not built."
        )
        return

    run_cmd_safe(["rmmod", "nvidia_power_unclamp"])
    rc, out = run_cmd_safe(["insmod", str(_UNCLAMP_KO)])
    run_cmd_safe(["rmmod", "nvidia_power_unclamp"])
    if rc != 0:
        errors.append(f"nvidia_power_unclamp failed: {out.strip()}")
        return

    after = collect_gpu_display(klog="")
    applied.append(
        f"nvidia_power_unclamp: RTX 2070 Max-Q AC power & D-Notifier 0xD1 synced "
        f"({gpu.gpu_power_cap_w}W/{gpu.gpu_pstate} -> {after.gpu_power_cap_w}W/{after.gpu_pstate})"
    )


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
        write_fan_boost(
            aw_hwmon / "fan1_boost",
            spec.cpu_fan_boost,
            spec.platform_profile,
            applied,
            errors,
            dry_run,
        )
        write_fan_boost(
            aw_hwmon / "fan2_boost",
            spec.gpu_fan_boost,
            spec.platform_profile,
            applied,
            errors,
            dry_run,
        )

    # 7. Clear post-S3-resume NVIDIA 30W / 810MHz VRAM battery clamp if active
    if not dry_run and spec.name in {
        WorkloadProfileName.GAMING_PRIME,
        WorkloadProfileName.OLLAMA_HEAVY,
    }:
        _unclamp_nvidia_s3_power_state(applied, errors)

    return tuple(applied), tuple(errors)
