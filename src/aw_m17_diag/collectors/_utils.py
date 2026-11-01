"""Safe file-reading and command-execution utilities for hardware collectors."""

from __future__ import annotations

import subprocess
from pathlib import Path


def read_text_safe(path: Path | str, default: str = "") -> str:
    """Read a sysfs/procfs file stripped of trailing whitespace, returning default on error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def read_int_safe(path: Path | str, default: int | None = None) -> int | None:
    """Read an integer from a sysfs/procfs file, returning default on error."""
    raw = read_text_safe(path)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def find_hwmon_by_name(
    target_name: str, hwmon_root: Path = Path("/sys/class/hwmon")
) -> Path | None:
    """Locate the /sys/class/hwmon/hwmonX directory matching the given driver name."""
    if not hwmon_root.is_dir():
        return None
    for entry in sorted(hwmon_root.iterdir()):
        name = read_text_safe(entry / "name")
        if name == target_name:
            return entry
    return None


def run_cmd_safe(args: list[str], timeout_s: float = 8.0) -> tuple[int, str]:
    """Execute a read-only system command and return (returncode, stdout)."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.returncode, proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def get_current_boot_kernel_logs() -> str:
    """Retrieve current boot kernel messages via dmesg or journalctl -k -b 0."""
    rc, out = run_cmd_safe(["dmesg"])
    if rc == 0 and out:
        return out
    rc2, out2 = run_cmd_safe(["journalctl", "-k", "-b", "0", "--no-pager"])
    if rc2 == 0 and out2:
        return out2
    return ""
