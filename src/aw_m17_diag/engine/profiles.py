"""Workload profile manager for Alienware m17 (0DG9D5)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

from aw_m17_diag.collectors._utils import find_hwmon_by_name, read_int_safe, run_cmd_safe
from aw_m17_diag.collectors.gpu_display import collect_gpu_display
from aw_m17_diag.models import (
    StartupProfileStatus,
    WorkloadProfileName,
    WorkloadProfileSpec,
)

_UNCLAMP_KO = Path(
    "/var/www/aw-m17-diag/dkms-candidates/nvidia-s3-power-unclamp/nvidia_power_unclamp.ko"
)
_DEFAULT_ETC_DIR = Path("/etc/aw-m17-diag")
_DEFAULT_RUN_DIR = Path("/run/aw-m17-diag")
_DEFAULT_SYSTEMD_DIR = Path("/etc/systemd/system")
_DEFAULT_SLEEP_DIR = Path("/lib/systemd/system-sleep")
_CLI_BIN = Path("/var/www/aw-m17-diag/.venv/bin/aw-m17-diag")

PROFILES: dict[WorkloadProfileName, WorkloadProfileSpec] = {
    WorkloadProfileName.EXTREME_PERFORMANCE: WorkloadProfileSpec(
        name=WorkloadProfileName.EXTREME_PERFORMANCE,
        description=(
            "Maximum zero-compromise CPU + GPU performance. Pins all CPU cores to peak turbo "
            "with performance EPP (0x00) and 70W/90W RAPL, unlocks full 4.50GHz CPU & 90W GPU "
            "boost clocks, unbinds unplugged alx NIC, and clears post-S3 30W NVIDIA clamp."
        ),
        platform_profile="balanced-performance",
        epp="performance",
        msr_pl1_w=70,
        msr_pl2_w=90,
        mmio_pl1_w=70,
        mmio_pl2_w=90,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=50,
        gpu_fan_boost=50,
        cpu_max_freq_khz=4_500_000,
        gpu_max_clock_mhz=None,
        quiesce_unplugged_alx=True,
    ),
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
            "unlocks full 4.50GHz CPU & 90W GPU boost clocks for <16.6ms 60Hz PRIME vblank "
            "pacing, unbinds unplugged alx NIC to prevent PCIe MMIO stats stalls, "
            "and clears post-S3-resume 30W / 810MHz NVIDIA clamp."
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
        cpu_max_freq_khz=4_500_000,
        gpu_max_clock_mhz=None,
        quiesce_unplugged_alx=True,
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


def _manage_alx_nic(
    spec: WorkloadProfileSpec,
    sys_root: Path,
    applied: list[str],
    errors: list[str],
    dry_run: bool,
) -> None:
    """Unbind unplugged Killer E2500 (alx) in gaming-prime to prevent PCIe MMIO stats stalls.

    In drivers/net/ethernet/atheros/alx/main.c, alx_get_stats64() unconditionally calls
    alx_update_hw_stats(), which performs 49 synchronous PCIe MMIO reads (ioread32) across
    the bus on every Netlink RTM_GETLINK / /proc/net/dev query—even when no Ethernet cable
    is connected (carrier=0) or the interface is DOWN. Under Wine/Proton + Ubisoft Connect
    (wine_sechost_service polling NSI adapter stats in a tight loop), this burns ~73% of a
    CPU core in kernel space and tanks framerates to ~14 FPS unless alx is unbound while
    unplugged.
    """
    alx_drv = sys_root / "bus" / "pci" / "drivers" / "alx"
    pci_devs = sys_root / "bus" / "pci" / "devices"
    if not alx_drv.is_dir():
        return

    bound_devs = sorted(alx_drv.glob("0000:*"))
    if spec.quiesce_unplugged_alx:
        if not bound_devs:
            if (pci_devs / "0000:6f:00.0").exists():
                applied.append(
                    "alx (0000:6f:00.0): already unbound (alx_update_hw_stats MMIO stall prevented)"
                )
            return

        for dev_dir in bound_devs:
            net_dir = dev_dir / "net"
            ifaces = sorted(net_dir.glob("*")) if net_dir.is_dir() else []
            has_carrier = any(read_int_safe(iface / "carrier") == 1 for iface in ifaces)
            iface_label = ifaces[0].name if ifaces else dev_dir.name
            if has_carrier:
                applied.append(
                    f"alx ({dev_dir.name}/{iface_label}): carrier=1 (wired link active, kept bound)"
                )
            else:
                _write_sysfs(alx_drv / "unbind", dev_dir.name, applied, errors, dry_run)
    else:
        if not bound_devs and (pci_devs / "0000:6f:00.0").exists():
            _write_sysfs(alx_drv / "bind", "0000:6f:00.0", applied, errors, dry_run)


def _parse_profile_file(path: Path) -> WorkloadProfileName | None:
    """Read and validate a WorkloadProfileName from a state file."""
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
        return WorkloadProfileName(raw)
    except (OSError, ValueError):
        return None


def get_startup_profile_status(
    etc_dir: Path = _DEFAULT_ETC_DIR,
    run_dir: Path = _DEFAULT_RUN_DIR,
    systemd_dir: Path = _DEFAULT_SYSTEMD_DIR,
    sleep_dir: Path = _DEFAULT_SLEEP_DIR,
) -> StartupProfileStatus:
    """Return current startup boot persistence and S3 resume hook configuration."""
    startup_prof = _parse_profile_file(etc_dir / "startup-profile")
    if startup_prof is None:
        startup_prof = _parse_profile_file(etc_dir / "active-profile")

    session_prof = _parse_profile_file(run_dir / "current-profile") or startup_prof

    unit_path = systemd_dir / "aw-m17-profile.service"
    legacy_unit_path = systemd_dir / "aw-m17-rapl.service"
    active_unit_path = unit_path if unit_path.exists() else legacy_unit_path

    wants_dir = systemd_dir / "multi-user.target.wants"
    systemd_enabled = (
        (wants_dir / "aw-m17-profile.service").exists()
        or (wants_dir / "aw-m17-rapl.service").exists()
    )
    if not systemd_enabled and systemd_dir == _DEFAULT_SYSTEMD_DIR and active_unit_path.exists():
        rc, _ = run_cmd_safe(["systemctl", "is-enabled", "--quiet", active_unit_path.name])
        systemd_enabled = rc == 0

    sleep_hook = sleep_dir / "zz-aw-m17-profile"
    sleep_installed = sleep_hook.is_file() and os.access(sleep_hook, os.X_OK)

    return StartupProfileStatus(
        startup_profile=startup_prof if systemd_enabled else None,
        session_profile=session_prof,
        systemd_enabled=systemd_enabled,
        systemd_unit_path=unit_path,
        sleep_hook_installed=sleep_installed,
        sleep_hook_path=sleep_hook,
    )


def _record_session_profile(
    spec: WorkloadProfileSpec,
    run_dir: Path,
    etc_dir: Path,
    sleep_dir: Path,
    applied: list[str],
    errors: list[str],
) -> None:
    """Record the active session profile in /run and ensure the S3 sleep hook restores it."""
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        session_file = run_dir / "current-profile"
        session_file.write_text(f"{spec.name.value}\n", encoding="utf-8")

        sleep_hook = sleep_dir / "zz-aw-m17-profile"
        if sleep_dir.is_dir() and _CLI_BIN.is_file():
            startup_file = etc_dir / "startup-profile"
            legacy_file = etc_dir / "active-profile"
            sleep_hook.write_text(
                f"""#!/bin/sh
case "$1" in
    post)
        if [ -f "{session_file}" ] && [ -x "{_CLI_BIN}" ]; then
            "{_CLI_BIN}" profile set "$(cat "{session_file}")" >/dev/null 2>&1 || true
        elif [ -f "{startup_file}" ] && [ -x "{_CLI_BIN}" ]; then
            "{_CLI_BIN}" profile set "$(cat "{startup_file}")" >/dev/null 2>&1 || true
        elif [ -f "{legacy_file}" ] && [ -x "{_CLI_BIN}" ]; then
            "{_CLI_BIN}" profile set "$(cat "{legacy_file}")" >/dev/null 2>&1 || true
        fi
        ;;
esac
""",
                encoding="utf-8",
            )
            sleep_hook.chmod(0o755)
        applied.append(f"Session profile recorded -> {session_file} ({spec.name.value})")
    except OSError as exc:
        errors.append(f"session profile tracking: {exc}")


def configure_startup_profile(
    name: WorkloadProfileName | str | None,
    dry_run: bool = False,
    etc_dir: Path = _DEFAULT_ETC_DIR,
    run_dir: Path = _DEFAULT_RUN_DIR,
    systemd_dir: Path = _DEFAULT_SYSTEMD_DIR,
    sleep_dir: Path = _DEFAULT_SLEEP_DIR,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Enable, update, or disable automatic workload profile loading on system startup."""
    if not dry_run and os.geteuid() != 0 and systemd_dir == _DEFAULT_SYSTEMD_DIR:
        return (), ("Root privileges required to configure startup profile (use sudo).",)

    applied: list[str] = []
    errors: list[str] = []

    startup_file = etc_dir / "startup-profile"
    legacy_file = etc_dir / "active-profile"
    unit_file = systemd_dir / "aw-m17-profile.service"
    legacy_unit = systemd_dir / "aw-m17-rapl.service"
    wants_dir = systemd_dir / "multi-user.target.wants"
    sleep_hook = sleep_dir / "zz-aw-m17-profile"

    if name is None:
        if dry_run:
            applied.append(f"[DRY-RUN] Disable {unit_file} and remove {startup_file}")
            return tuple(applied), tuple(errors)
        try:
            if systemd_dir == _DEFAULT_SYSTEMD_DIR and not os.environ.get("INVOCATION_ID"):
                run_cmd_safe(["systemctl", "disable", "aw-m17-profile.service"])
                run_cmd_safe(["systemctl", "disable", "aw-m17-rapl.service"])
            for p in (
                startup_file,
                legacy_file,
                unit_file,
                legacy_unit,
                wants_dir / "aw-m17-profile.service",
                wants_dir / "aw-m17-rapl.service",
            ):
                if p.exists() or p.is_symlink():
                    p.unlink()
            if systemd_dir == _DEFAULT_SYSTEMD_DIR and not os.environ.get("INVOCATION_ID"):
                run_cmd_safe(["systemctl", "daemon-reload"])
            applied.append("Disabled automatic startup profile loading (boot service removed)")
        except OSError as exc:
            errors.append(f"startup profile disable: {exc}")
        return tuple(applied), tuple(errors)

    spec = get_workload_profile(name)
    if dry_run:
        applied.append(f"[DRY-RUN] {startup_file} <- {spec.name.value}")
        applied.append(f"[DRY-RUN] {unit_file} <- enable '{spec.name.value}' on boot")
        return tuple(applied), tuple(errors)

    try:
        etc_dir.mkdir(parents=True, exist_ok=True)
        startup_file.write_text(f"{spec.name.value}\n", encoding="utf-8")
        legacy_file.write_text(f"{spec.name.value}\n", encoding="utf-8")

        systemd_dir.mkdir(parents=True, exist_ok=True)
        if legacy_unit.exists() or legacy_unit.is_symlink():
            legacy_unit.unlink()

        unit_file.write_text(
            f"""[Unit]
Description=Apply Alienware m17 (0DG9D5) Startup Workload Profile ({spec.name.value})
After=multi-user.target network.target nvidia-persistenced.service

[Service]
Type=oneshot
ExecStart={_CLI_BIN} profile set {spec.name.value}
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
Alias=aw-m17-rapl.service
""",
            encoding="utf-8",
        )
        legacy_unit.symlink_to(unit_file.name)

        wants_dir.mkdir(parents=True, exist_ok=True)
        for want_name in ("aw-m17-profile.service", "aw-m17-rapl.service"):
            want_link = wants_dir / want_name
            if want_link.exists() or want_link.is_symlink():
                want_link.unlink()
            want_link.symlink_to(unit_file)

        _record_session_profile(spec, run_dir, etc_dir, sleep_dir, applied, errors)

        if systemd_dir == _DEFAULT_SYSTEMD_DIR and not os.environ.get("INVOCATION_ID"):
            run_cmd_safe(["systemctl", "daemon-reload"])
            run_cmd_safe(["systemctl", "enable", "aw-m17-profile.service"])

        applied.append(
            f"Startup profile '{spec.name.value}' enabled on boot ({unit_file}) & S3 resume ({sleep_hook})"
        )
    except OSError as exc:
        errors.append(f"startup profile configuration: {exc}")

    return tuple(applied), tuple(errors)


def apply_workload_profile(
    spec: WorkloadProfileSpec,
    dry_run: bool = False,
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    persist_startup: bool = False,
    etc_dir: Path = _DEFAULT_ETC_DIR,
    run_dir: Path = _DEFAULT_RUN_DIR,
    systemd_dir: Path = _DEFAULT_SYSTEMD_DIR,
    sleep_dir: Path = _DEFAULT_SLEEP_DIR,
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

    # 3. CPU Energy Performance Preference (EPP) & Max Turbo Frequency across all cores
    cpu_root = sys_root / "devices" / "system" / "cpu"
    if cpu_root.is_dir():
        for epp_file in sorted(cpu_root.glob("cpu[0-9]*/cpufreq/energy_performance_preference")):
            _write_sysfs(epp_file, spec.epp, applied, errors, dry_run)
        for freq_file in sorted(cpu_root.glob("cpu[0-9]*/cpufreq/scaling_max_freq")):
            _write_sysfs(freq_file, str(spec.cpu_max_freq_khz), applied, errors, dry_run)

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

    # 7. Unbind unplugged Killer E2500 (alx) in gaming-prime to prevent PCIe MMIO stats stalls
    _manage_alx_nic(spec, sys_root, applied, errors, dry_run)

    # 8. Clear post-S3-resume NVIDIA 30W / 810MHz VRAM battery clamp if active
    if not dry_run and spec.name in {
        WorkloadProfileName.EXTREME_PERFORMANCE,
        WorkloadProfileName.GAMING_PRIME,
        WorkloadProfileName.OLLAMA_HEAVY,
    }:
        _unclamp_nvidia_s3_power_state(applied, errors)

    # 9. Apply NVIDIA GPU boost clock sweet-spot ceiling (-lgc) or reset (-rgc)
    if not dry_run and sys_root == Path("/sys"):
        run_cmd_safe(["nvidia-smi", "-pm", "1"])
        if spec.gpu_max_clock_mhz is not None:
            rc, out = run_cmd_safe(["nvidia-smi", "-lgc", f"300,{spec.gpu_max_clock_mhz}"])
            if rc == 0:
                applied.append(
                    f"nvidia-smi -lgc 300,{spec.gpu_max_clock_mhz} (GPU boost ceiling locked)"
                )
            else:
                errors.append(f"nvidia-smi -lgc failed: {out.strip()}")
        else:
            rc, _ = run_cmd_safe(["nvidia-smi", "-rgc"])
            if rc == 0:
                applied.append("nvidia-smi -rgc (GPU boost clock range unlocked)")

    # 10. Persist startup profile on boot if requested, or record session profile for S3 resume
    if persist_startup:
        s_applied, s_errors = configure_startup_profile(
            name=spec.name,
            dry_run=dry_run,
            etc_dir=etc_dir,
            run_dir=run_dir,
            systemd_dir=systemd_dir,
            sleep_dir=sleep_dir,
        )
        applied.extend(s_applied)
        errors.extend(s_errors)
    elif not dry_run and (sys_root == Path("/sys") or run_dir != _DEFAULT_RUN_DIR):
        _record_session_profile(spec, run_dir, etc_dir, sleep_dir, applied, errors)

    return tuple(applied), tuple(errors)
