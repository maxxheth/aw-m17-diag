"""Crash forensics collector analyzing previous boots for freezes, DMAR bursts, and OOMs."""

from __future__ import annotations

import re
from pathlib import Path

from aw_m17_diag.collectors._utils import run_cmd_safe
from aw_m17_diag.models import BootCrashRecord

_BOOT_LINE_RE = re.compile(
    r"^\s*(-?\d+)\s+([0-9a-f]{32})\s+(\w+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+)\s+(\w+\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\w+)"
)
_SYSLOG_PROC_RE = re.compile(r"^\w{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\S+\s+([^\[:]+)")


def check_syslog_null_bytes(syslog_path: Path = Path("/var/log/syslog")) -> int:
    """Count NULL bytes (0x00) in /var/log/syslog caused by abrupt power/NVMe freezes."""
    try:
        data = syslog_path.read_bytes()
        return data.count(b"\x00")
    except OSError:
        return 0


def _inspect_single_boot(
    boot_offset: int,
    boot_id: str,
    first_entry: str,
    last_entry: str,
) -> BootCrashRecord:
    """Inspect a specific boot offset for clean shutdown vs abrupt freeze."""
    is_current = boot_offset == 0

    _, tail_out = run_cmd_safe(
        ["journalctl", "-b", str(boot_offset), "-n", "80", "--no-pager"],
        timeout_s=10.0,
    )
    tail_lines = tail_out.strip().splitlines()

    clean_shutdown = is_current or any(
        marker in tail_out
        for marker in (
            "Reached target shutdown.target",
            "Reached target reboot.target",
            "Reached target poweroff.target",
            "Journal stopped",
            "System Reboot",
            "Shutting down.",
        )
    )
    abrupt_freeze = not is_current and not clean_shutdown

    # Count DMAR faults in that boot
    rc_dmar, dmar_out = run_cmd_safe(
        ["journalctl", "-b", str(boot_offset), "-k", "-g", "DMAR:.*fault addr", "--no-pager"],
        timeout_s=10.0,
    )
    dmar_count = (
        len([ln for ln in dmar_out.splitlines() if "fault addr" in ln]) if rc_dmar == 0 else 0
    )

    # Search for OOM / SIGKILL / llama-server terminated events
    rc_kill, kill_out = run_cmd_safe(
        [
            "journalctl",
            "-b",
            str(boot_offset),
            "-g",
            "Out of memory|Killed process|signal: killed|Hardware Error|nvme.*timeout",
            "--no-pager",
        ],
        timeout_s=10.0,
    )
    kill_events: list[str] = []
    if rc_kill == 0 and kill_out:
        for ln in kill_out.splitlines():
            if not ln.startswith("--"):
                kill_events.append(ln.strip()[-120:])

    # Identify active processes in the final log lines before cutoff
    active_procs: list[str] = []
    for ln in reversed(tail_lines[-25:]):
        m = _SYSLOG_PROC_RE.match(ln)
        if m:
            proc_name = m.group(1).strip()
            if proc_name not in active_procs:
                active_procs.append(proc_name)
        if len(active_procs) >= 5:
            break

    if is_current:
        summary = "Active current boot session."
    elif clean_shutdown:
        summary = "Clean systemd shutdown/reboot recorded."
    else:
        proc_str = ", ".join(active_procs[:3]) if active_procs else "unknown"
        summary = (
            f"ABRUPT FREEZE / CRASH: No shutdown sequence! "
            f"Last active: [{proc_str}], DMAR faults: {dmar_count}"
        )
        if kill_events:
            summary += f", Kill/OOM events: {len(kill_events)}"

    return BootCrashRecord(
        boot_offset=boot_offset,
        boot_id=boot_id,
        first_entry=first_entry,
        last_entry=last_entry,
        clean_shutdown=clean_shutdown,
        abrupt_freeze_suspected=abrupt_freeze,
        dmar_fault_count=dmar_count,
        oom_or_sigkill_events=tuple(kill_events[-5:]),
        last_active_processes=tuple(active_procs),
        summary=summary,
    )


def analyze_boot_history(max_boots: int = 5) -> tuple[BootCrashRecord, ...]:
    """Analyze the most recent N boots from journalctl --list-boots."""
    rc, out = run_cmd_safe(["journalctl", "--list-boots", "--no-pager"])
    if rc != 0 or not out:
        return ()

    parsed_boots: list[tuple[int, str, str, str]] = []
    for line in out.splitlines():
        m = _BOOT_LINE_RE.match(line)
        if m:
            offset = int(m.group(1))
            boot_id = m.group(2)
            first_ts = m.group(3)
            last_ts = m.group(4)
            parsed_boots.append((offset, boot_id, first_ts, last_ts))

    recent = parsed_boots[-max_boots:] if max_boots > 0 else parsed_boots
    records: list[BootCrashRecord] = []
    for offset, boot_id, first_ts, last_ts in reversed(recent):
        records.append(_inspect_single_boot(offset, boot_id, first_ts, last_ts))
    return tuple(records)
