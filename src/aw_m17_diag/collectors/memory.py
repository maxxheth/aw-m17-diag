"""Memory, swap, and VM writeback collector."""

from __future__ import annotations

from pathlib import Path

import psutil

from aw_m17_diag.collectors._utils import read_int_safe, read_text_safe
from aw_m17_diag.models import MemorySnapshot

_GIB = float(1024**3)


def collect_memory(proc_root: Path = Path("/proc")) -> MemorySnapshot:
    """Collect RAM, Swap headroom, VM sysctls, and dirty page writeback backlog."""
    vmem = psutil.virtual_memory()
    smem = psutil.swap_memory()

    vm_sys = proc_root / "sys" / "vm"
    swappiness = read_int_safe(vm_sys / "swappiness", default=60) or 60
    vfs_pressure = read_int_safe(vm_sys / "vfs_cache_pressure", default=100) or 100
    dirty_ratio = read_int_safe(vm_sys / "dirty_ratio", default=20) or 20
    dirty_bg_ratio = read_int_safe(vm_sys / "dirty_background_ratio", default=10) or 10

    dirty_kib = 0
    meminfo = read_text_safe(proc_root / "meminfo")
    for line in meminfo.splitlines():
        if line.startswith("Dirty:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                dirty_kib = int(parts[1])
            break

    return MemorySnapshot(
        ram_total_gib=round(vmem.total / _GIB, 2),
        ram_available_gib=round(vmem.available / _GIB, 2),
        ram_used_percent=round(vmem.percent, 1),
        swap_total_gib=round(smem.total / _GIB, 2),
        swap_free_gib=round(smem.free / _GIB, 2),
        swap_used_percent=round(smem.percent, 1),
        swappiness=swappiness,
        vfs_cache_pressure=vfs_pressure,
        dirty_ratio=dirty_ratio,
        dirty_background_ratio=dirty_bg_ratio,
        dirty_pages_mib=round(dirty_kib / 1024.0, 2),
    )
