"""Workload profile manager for Alienware m17 (0DG9D5)."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
from pathlib import Path

from aw_m17_diag.collectors._utils import (
    find_hwmon_by_name,
    read_int_safe,
    read_text_safe,
    run_cmd_safe,
)
from aw_m17_diag.collectors.gpu_display import collect_gpu_display
from aw_m17_diag.collectors.thermal_power import collect_thermal_power
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

# Core configuration invariants for the gaming-prime workload profile (Options A + B)
GAMING_PRIME_PLATFORM_PROFILE = "custom"
GAMING_PRIME_FAN_BOOST_PCT = 100
GAMING_PRIME_PL1_W = 38
GAMING_PRIME_PL2_W = 60
GAMING_PRIME_CPU_MAX_FREQ_KHZ = 4_500_000
GAMING_PRIME_GPU_MAX_CLOCK_MHZ = 1560
GAMING_PRIME_GPU_VF_OFFSET_MHZ = 135

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
            "Cool & sustained 60Hz PRIME gaming enforcing core thermal invariants: AWCC 'custom' "
            "profile with +100% CPU/GPU exhaust fan boost, native NVML GPU undervolt (+135MHz V/F "
            "offset @ 1560MHz ceiling), and 38W/60W shared-heatpipe RAPL relief (4.50GHz "
            "single-core boost preserved, unplugged alx unbound, S3 30W clamp cleared)."
        ),
        platform_profile=GAMING_PRIME_PLATFORM_PROFILE,
        epp="balance_performance",
        msr_pl1_w=GAMING_PRIME_PL1_W,
        msr_pl2_w=GAMING_PRIME_PL2_W,
        mmio_pl1_w=GAMING_PRIME_PL1_W,
        mmio_pl2_w=GAMING_PRIME_PL2_W,
        swappiness=10,
        dirty_ratio=10,
        dirty_background_ratio=5,
        cpu_fan_boost=GAMING_PRIME_FAN_BOOST_PCT,
        gpu_fan_boost=GAMING_PRIME_FAN_BOOST_PCT,
        cpu_max_freq_khz=GAMING_PRIME_CPU_MAX_FREQ_KHZ,
        gpu_max_clock_mhz=GAMING_PRIME_GPU_MAX_CLOCK_MHZ,
        gpu_vf_offset_mhz=GAMING_PRIME_GPU_VF_OFFSET_MHZ,
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


def _find_alienware_platform_profile_file(sys_root: Path) -> Path | None:
    """Locate the per-driver class attribute /sys/class/platform-profile/platform-profile-*/profile.

    In Linux 6.14+ / 7.0 (drivers/acpi/platform_profile.c), legacy
    /sys/firmware/acpi/platform_profile explicitly rejects userspace writes of
    "custom" (PLATFORM_PROFILE_CUSTOM) with -EINVAL, whereas the per-driver class
    interface /sys/class/platform-profile/platform-profile-0/profile exported by
    alienware-wmi natively accepts "custom" under profile_lock.
    """
    pprof_class = sys_root / "class" / "platform-profile"
    if not pprof_class.is_dir():
        return None
    for dev_dir in sorted(pprof_class.glob("platform-profile-*")):
        if read_text_safe(dev_dir / "name") == "alienware-wmi":
            prof_file = dev_dir / "profile"
            if prof_file.exists():
                return prof_file
    return None


def write_fan_boost(
    path: Path,
    boost: int,
    platform_profile: str,
    applied: list[str],
    errors: list[str],
    dry_run: bool,
) -> None:
    """Write Alienware WMI fan*_boost (clamped to 0..100%), handling EBADRQC under presets.

    On Alienware WMAX/AWCC firmware (0DG9D5 BIOS 2.21.1), WMI Thermal_Control (0x15)
    op 0x02 implements AWCC SetAddonSpeedPercent (0..100%). Invoking it while a preset
    profile ('balanced-performance', 'balanced', 'quiet', 'cool') is active returns
    AWCC_FAILURE_CODE (0xFFFFFFFF -> -EBADRQC / Errno 56); manual boost requires
    platform_profile == 'custom' (0x00) and values strictly in 0..100.
    """
    clamped = max(0, min(100, boost))
    if dry_run:
        applied.append(f"[DRY-RUN] {path} <- {clamped}")
        return

    current = read_int_safe(path)
    if clamped == 0 and current == 0:
        applied.append(f"{path} == 0 (EC auto-curve active)")
        return

    try:
        path.write_text(f"{clamped}\n", encoding="utf-8")
        applied.append(f"{path} <- {clamped}")
    except OSError as exc:
        if exc.errno == errno.EBADRQC:
            applied.append(
                f"{path}: EC auto-curve managed by '{platform_profile}' profile "
                "(WMI 0x15 op 0x02 locks manual fan boost under preset profiles)"
            )
        else:
            errors.append(f"{path}: {exc}")


def _apply_platform_profile_and_fans(
    spec: WorkloadProfileSpec,
    sys_root: Path,
    applied: list[str],
    errors: list[str],
    dry_run: bool,
) -> None:
    """Safely sequence AWCC platform_profile and fan1_boost/fan2_boost transitions.

    - Entering 'custom': switches /sys/class/platform-profile/platform-profile-0/profile
      to 'custom' first, then applies clamped 0..100% fan1_boost / fan2_boost.
    - Leaving 'custom' for a preset profile: zeroes fan1_boost / fan2_boost FIRST while
      still in 'custom' so no non-zero SetAddonSpeedPercent remains latched in EC RAM,
      then activates the target preset profile.
    """
    aw_pprof = _find_alienware_platform_profile_file(sys_root)
    acpi_prof = sys_root / "firmware" / "acpi" / "platform_profile"
    aw_hwmon = find_hwmon_by_name("alienware_wmi", sys_root / "class" / "hwmon")

    live_profile = read_text_safe(aw_pprof if aw_pprof is not None else acpi_prof)

    if spec.platform_profile == "custom":
        target_prof_path = aw_pprof if aw_pprof is not None else acpi_prof
        if target_prof_path.exists() or dry_run:
            _write_sysfs(target_prof_path, "custom", applied, errors, dry_run)
        if aw_hwmon is not None:
            write_fan_boost(
                aw_hwmon / "fan1_boost",
                spec.cpu_fan_boost,
                "custom",
                applied,
                errors,
                dry_run,
            )
            write_fan_boost(
                aw_hwmon / "fan2_boost",
                spec.gpu_fan_boost,
                "custom",
                applied,
                errors,
                dry_run,
            )
        elif dry_run:
            applied.append(f"[DRY-RUN] alienware_wmi fan1_boost <- {spec.cpu_fan_boost}")
            applied.append(f"[DRY-RUN] alienware_wmi fan2_boost <- {spec.gpu_fan_boost}")
        return

    # Transitioning to a preset profile ('balanced-performance', 'balanced', 'quiet', 'cool')
    zeroed_before_switch = False
    if aw_hwmon is not None and live_profile == "custom":
        write_fan_boost(aw_hwmon / "fan1_boost", 0, "custom", applied, errors, dry_run)
        write_fan_boost(aw_hwmon / "fan2_boost", 0, "custom", applied, errors, dry_run)
        zeroed_before_switch = True

    target_prof_path = aw_pprof if aw_pprof is not None else acpi_prof
    if target_prof_path.exists() or dry_run:
        _write_sysfs(target_prof_path, spec.platform_profile, applied, errors, dry_run)

    if aw_hwmon is not None and not (zeroed_before_switch and spec.cpu_fan_boost == 0 and spec.gpu_fan_boost == 0):
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


def _apply_nvml_vf_offset(
    offset_mhz: int,
    applied: list[str],
    errors: list[str],
    dry_run: bool,
    sys_root: Path,
) -> None:
    """Apply or reset NVIDIA GPC V/F clock offset (undervolt when > 0 with -lgc) via NVML."""
    sign_str = f"{offset_mhz:+d}" if offset_mhz != 0 else "0"
    if dry_run:
        applied.append(f"[DRY-RUN] NVML GPC V/F offset <- {sign_str} MHz")
        return
    if sys_root != Path("/sys"):
        return

    try:
        nvml = ctypes.CDLL("libnvidia-ml.so.1")
    except OSError as exc:
        if offset_mhz != 0:
            errors.append(f"libnvidia-ml.so.1 unavailable for GPU V/F offset: {exc}")
        return

    rc = nvml.nvmlInit_v2()
    if rc != 0:
        if offset_mhz != 0:
            errors.append(f"nvmlInit_v2 failed (rc={rc})")
        return

    try:
        handle = ctypes.c_void_p()
        rc = nvml.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(handle))
        if rc != 0:
            if offset_mhz != 0:
                errors.append(f"nvmlDeviceGetHandleByIndex_v2 failed (rc={rc})")
            return

        set_fn = getattr(nvml, "nvmlDeviceSetGpcClkVfOffset", None)
        if set_fn is None:
            if offset_mhz != 0:
                errors.append("nvmlDeviceSetGpcClkVfOffset symbol missing in libnvidia-ml.so.1")
            return

        rc = set_fn(handle, ctypes.c_int(offset_mhz))
        if rc != 0:
            errors.append(f"nvmlDeviceSetGpcClkVfOffset({offset_mhz}) failed (rc={rc})")
            return

        note = "GPU V/F undervolt active" if offset_mhz > 0 else "stock V/F curve"
        applied.append(f"NVML GPC V/F offset <- {sign_str} MHz ({note})")
    finally:
        nvml.nvmlShutdown()


def _ko_matches_running_kernel(ko_path: Path) -> bool:
    """Return True if ko_path exists and its vermagic matches platform.release()."""
    if not ko_path.is_file():
        return False
    rc, out = run_cmd_safe(["modinfo", "-F", "vermagic", str(ko_path)])
    if rc != 0:
        return False
    return out.strip().startswith(f"{platform.release()} ")


def _unclamp_nvidia_s3_power_state(applied: list[str], errors: list[str]) -> None:
    """Clear the NVIDIA RTX 2070 Max-Q post-S3-resume 30W / P5 (810 MHz) battery clamp."""
    gpu = collect_gpu_display(klog="")
    is_clamped = (gpu.gpu_power_cap_w is not None and gpu.gpu_power_cap_w < 60.0) or (
        gpu.gpu_sw_thermal_slowdown and (gpu.gpu_temp_c is not None and gpu.gpu_temp_c < 75)
    )
    if not is_clamped:
        return

    if not _ko_matches_running_kernel(_UNCLAMP_KO) and (_UNCLAMP_KO.parent / "Makefile").is_file():
        run_cmd_safe(["make", "-C", str(_UNCLAMP_KO.parent), "clean", "all"])

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

    # 2. AWCC Platform Profile & Fan Boost (sequenced safely via class interface)
    _apply_platform_profile_and_fans(spec, sys_root, applied, errors, dry_run)

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

    # 6. Unbind unplugged Killer E2500 (alx) in gaming-prime to prevent PCIe MMIO stats stalls
    _manage_alx_nic(spec, sys_root, applied, errors, dry_run)

    # 7. Clear post-S3-resume / custom-profile NVIDIA 30W / 810MHz VRAM clamp if active
    if (
        not dry_run
        and sys_root == Path("/sys")
        and spec.name
        in {
            WorkloadProfileName.EXTREME_PERFORMANCE,
            WorkloadProfileName.GAMING_PRIME,
            WorkloadProfileName.OLLAMA_HEAVY,
        }
    ):
        _unclamp_nvidia_s3_power_state(applied, errors)

    # 8. Apply NVIDIA GPU V/F undervolt offset & boost clock ceiling (-lgc) or reset (-rgc)
    _apply_nvml_vf_offset(spec.gpu_vf_offset_mhz, applied, errors, dry_run, sys_root)
    if dry_run:
        if spec.gpu_max_clock_mhz is not None:
            applied.append(f"[DRY-RUN] nvidia-smi -lgc 300,{spec.gpu_max_clock_mhz}")
        else:
            applied.append("[DRY-RUN] nvidia-smi -rgc")
    elif sys_root == Path("/sys"):
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

    # 9. Verify live gaming-prime core configuration invariants post-apply
    if spec.name == WorkloadProfileName.GAMING_PRIME and not dry_run and sys_root == Path("/sys"):
        inv_ok, inv_violations = verify_gaming_prime_invariants(
            sys_root=sys_root, proc_root=proc_root
        )
        if inv_ok:
            applied.append(
                "gaming-prime core invariants verified: "
                f"profile={GAMING_PRIME_PLATFORM_PROFILE}, "
                f"fan_boost=+{GAMING_PRIME_FAN_BOOST_PCT}%/+{GAMING_PRIME_FAN_BOOST_PCT}%, "
                f"RAPL={GAMING_PRIME_PL1_W}W/{GAMING_PRIME_PL2_W}W, "
                f"GPU=+{GAMING_PRIME_GPU_VF_OFFSET_MHZ}MHz @ <={GAMING_PRIME_GPU_MAX_CLOCK_MHZ}MHz"
            )
        else:
            for violation in inv_violations:
                errors.append(f"gaming-prime invariant violation: {violation}")

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


def verify_gaming_prime_invariants(
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
) -> tuple[bool, tuple[str, ...]]:
    """Verify all core configuration invariants of gaming-prime against live hardware state."""
    violations: list[str] = []
    tp = collect_thermal_power(sys_root=sys_root, klog="")

    if tp.platform_profile != GAMING_PRIME_PLATFORM_PROFILE:
        violations.append(
            f"platform_profile={tp.platform_profile!r} (expected {GAMING_PRIME_PLATFORM_PROFILE!r})"
        )
    if tp.cpu_fan_boost != GAMING_PRIME_FAN_BOOST_PCT:
        violations.append(
            f"cpu_fan_boost={tp.cpu_fan_boost} (expected {GAMING_PRIME_FAN_BOOST_PCT})"
        )
    if tp.gpu_fan_boost != GAMING_PRIME_FAN_BOOST_PCT:
        violations.append(
            f"gpu_fan_boost={tp.gpu_fan_boost} (expected {GAMING_PRIME_FAN_BOOST_PCT})"
        )
    if tp.msr_pl1_w is None or abs(tp.msr_pl1_w - GAMING_PRIME_PL1_W) > 0.5:
        violations.append(f"msr_pl1_w={tp.msr_pl1_w}W (expected {GAMING_PRIME_PL1_W}W)")
    if tp.msr_pl2_w is None or abs(tp.msr_pl2_w - GAMING_PRIME_PL2_W) > 0.5:
        violations.append(f"msr_pl2_w={tp.msr_pl2_w}W (expected {GAMING_PRIME_PL2_W}W)")
    if tp.mmio_pl1_w is None or abs(tp.mmio_pl1_w - GAMING_PRIME_PL1_W) > 0.5:
        violations.append(f"mmio_pl1_w={tp.mmio_pl1_w}W (expected {GAMING_PRIME_PL1_W}W)")
    if tp.mmio_pl2_w is None or abs(tp.mmio_pl2_w - GAMING_PRIME_PL2_W) > 0.5:
        violations.append(f"mmio_pl2_w={tp.mmio_pl2_w}W (expected {GAMING_PRIME_PL2_W}W)")

    max_freq = read_int_safe(
        sys_root / "devices" / "system" / "cpu" / "cpu0" / "cpufreq" / "scaling_max_freq"
    )
    if max_freq is not None and max_freq != GAMING_PRIME_CPU_MAX_FREQ_KHZ:
        violations.append(
            f"cpu0 scaling_max_freq={max_freq} (expected {GAMING_PRIME_CPU_MAX_FREQ_KHZ})"
        )

    alx_bound = sorted((sys_root / "bus" / "pci" / "drivers" / "alx").glob("0000:*"))
    for dev_dir in alx_bound:
        net_dir = dev_dir / "net"
        ifaces = sorted(net_dir.glob("*")) if net_dir.is_dir() else []
        if not any(read_int_safe(iface / "carrier") == 1 for iface in ifaces):
            violations.append(f"unplugged alx NIC {dev_dir.name} still bound")

    if sys_root == Path("/sys"):
        gpu = collect_gpu_display(sys_root=sys_root, proc_root=proc_root, klog="")
        if gpu.gpu_vf_offset_mhz != GAMING_PRIME_GPU_VF_OFFSET_MHZ:
            violations.append(
                f"gpu_vf_offset_mhz={gpu.gpu_vf_offset_mhz} "
                f"(expected +{GAMING_PRIME_GPU_VF_OFFSET_MHZ} MHz)"
            )
        if gpu.gpu_power_cap_w is not None and gpu.gpu_power_cap_w < 60.0:
            violations.append(f"gpu_power_cap_w={gpu.gpu_power_cap_w}W (clamped below 90W)")

    return len(violations) == 0, tuple(violations)
