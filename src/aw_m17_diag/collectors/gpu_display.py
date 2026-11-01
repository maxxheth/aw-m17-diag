"""NVIDIA RTX 2070 Max-Q and Intel UHD 630 hybrid display collector."""

from __future__ import annotations

import re
from pathlib import Path

from aw_m17_diag.collectors._utils import (
    get_current_boot_kernel_logs,
    read_text_safe,
    run_cmd_safe,
)
from aw_m17_diag.models import GpuDisplaySnapshot

_WQ_HOG_RE = re.compile(
    r"workqueue:\s*(?:output_poll_execute|i915_hpd_poll_init_work).*?hogged CPU.*?(\d+)\s+times"
)


def _query_nvidia_smi() -> tuple[
    str, int | None, float | None, float | None, int | None, int | None, str
]:
    """Query nvidia-smi for driver version, temp, power, power cap, vram, and pstate."""
    rc, out = run_cmd_safe(
        [
            "nvidia-smi",
            "--query-gpu=driver_version,temperature.gpu,power.draw,power.max_limit,memory.used,memory.total,pstate",
            "--format=csv,noheader,nounits",
        ]
    )
    if rc != 0 or not out.strip():
        return "Unknown", None, None, None, None, None, "Unknown"

    line = out.strip().splitlines()[0]
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 7:
        return "Unknown", None, None, None, None, None, "Unknown"

    driver_ver = parts[0]
    temp_c = int(parts[1]) if parts[1].isdigit() else None
    try:
        power_w: float | None = round(float(parts[2]), 2)
    except ValueError:
        power_w = None
    try:
        power_cap_w: float | None = round(float(parts[3]), 2)
    except ValueError:
        power_cap_w = None
    vram_used = int(parts[4]) if parts[4].isdigit() else None
    vram_total = int(parts[5]) if parts[5].isdigit() else None
    pstate = parts[6]
    return driver_ver, temp_c, power_w, power_cap_w, vram_used, vram_total, pstate


def _get_flatpak_nvidia_gl_versions() -> tuple[str, ...]:
    """Return installed org.freedesktop.Platform.GL.nvidia-* runtime suffixes."""
    rc, out = run_cmd_safe(["flatpak", "list", "--runtime", "--columns=application"])
    if rc != 0 or not out:
        return ()
    versions: set[str] = set()
    prefix = "org.freedesktop.Platform.GL.nvidia-"
    for raw_line in out.splitlines():
        app_id = raw_line.strip()
        if app_id.startswith(prefix):
            versions.add(app_id.removeprefix(prefix))
    return tuple(sorted(versions))


def _is_ucsi_blacklisted(modprobe_dir: Path = Path("/etc/modprobe.d")) -> bool:
    """Check if ucsi_ccg and i2c_nvidia_gpu are blacklisted in /etc/modprobe.d."""
    if not modprobe_dir.is_dir():
        return False
    has_ucsi = False
    has_i2c_nv = False
    for conf in modprobe_dir.glob("*.conf"):
        txt = read_text_safe(conf)
        if "blacklist ucsi_ccg" in txt:
            has_ucsi = True
        if "blacklist i2c_nvidia_gpu" in txt:
            has_i2c_nv = True
    return has_ucsi and has_i2c_nv


def collect_gpu_display(
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    modprobe_dir: Path = Path("/etc/modprobe.d"),
    klog: str | None = None,
) -> GpuDisplaySnapshot:
    """Collect GPU, UCSI Type-C, i915 HPD workqueue, and Flatpak GL synchronization state."""
    (
        driver_ver,
        temp_c,
        power_w,
        power_cap_w,
        vram_used,
        vram_total,
        pstate,
    ) = _query_nvidia_smi()

    if driver_ver == "Unknown":
        driver_ver = read_text_safe(sys_root / "module" / "nvidia" / "version", default="Unknown")

    poll_sysfs = read_text_safe(sys_root / "module" / "drm_kms_helper" / "parameters" / "poll")
    cmdline = read_text_safe(proc_root / "cmdline")
    poll_disabled = poll_sysfs in {"N", "0"} or (
        "drm_kms_helper.poll=0" in cmdline or "drm_kms_helper.poll=N" in cmdline
    )

    logs = klog if klog is not None else get_current_boot_kernel_logs()
    ucsi_timeout = "0000:01:00.3: i2c timeout error" in logs or "ucsi_ccg_init failed" in logs
    nv_drm_gem_err = "Failed to get memory pages for NvKmsKapiMemory" in logs

    max_hog_count = 0
    for match in _WQ_HOG_RE.finditer(logs):
        count = int(match.group(1))
        if count > max_hog_count:
            max_hog_count = count

    flatpak_gl_vers = _get_flatpak_nvidia_gl_versions()
    expected_fp_ver = driver_ver.replace(".", "-")
    mismatch = bool(
        driver_ver != "Unknown" and flatpak_gl_vers and expected_fp_ver not in flatpak_gl_vers
    )

    return GpuDisplaySnapshot(
        nvidia_driver_version=driver_ver,
        gpu_temp_c=temp_c,
        gpu_power_w=power_w,
        gpu_power_cap_w=power_cap_w,
        gpu_vram_used_mib=vram_used,
        gpu_vram_total_mib=vram_total,
        gpu_pstate=pstate,
        ucsi_ccg_blacklisted=_is_ucsi_blacklisted(modprobe_dir),
        ucsi_timeout_in_dmesg=ucsi_timeout,
        nv_drm_gem_error_in_dmesg=nv_drm_gem_err,
        drm_kms_poll_disabled=poll_disabled,
        i915_workqueue_hog_count=max_hog_count,
        flatpak_nvidia_gl_versions=flatpak_gl_vers,
        flatpak_driver_mismatch=mismatch,
    )
