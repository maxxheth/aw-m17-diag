"""Live real-time TUI monitor for Alienware m17 hardware telemetry and minefields."""

from __future__ import annotations

import time
from datetime import datetime

from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from aw_m17_diag.engine.evaluator import run_full_audit
from aw_m17_diag.models import Severity, SystemAuditReport
from aw_m17_diag.ui.console import console


def _build_dashboard_view(report: SystemAuditReport) -> Group:
    """Build a renderable Rich Group for a single refresh frame of the live dashboard."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = Panel(
        f"[bold cyan]Alienware m17 (0DG9D5) Live Telemetry & Minefield Watch[/bold cyan]  —  "
        f"[dim]{now_str}[/dim]  (Press Ctrl+C to exit)",
        border_style="cyan",
    )

    grid = Table(show_lines=True, expand=True)
    grid.add_column("Subsystem", style="bold cyan", width=22)
    grid.add_column("Primary Telemetry", width=44)
    grid.add_column("Minefield / Stability Indicators", width=44)

    st = report.storage
    ctrl_temp = st.sensor2_ctrl_temp_c or 0.0
    ctrl_color = "red" if ctrl_temp >= 75.0 else ("yellow" if ctrl_temp >= 65.0 else "green")
    apst_str = (
        "[bold green]0 us (Safe)[/bold green]"
        if st.apst_max_latency_us == 0
        else f"[bold red]{st.apst_max_latency_us} us (APST Crash Risk!)[/bold red]"
    )
    grid.add_row(
        "NVMe (SM2268XT)",
        f"Composite: {st.composite_temp_c}°C | NAND(S1): {st.sensor1_nand_temp_c}°C\n"
        f"Controller(S2): [{ctrl_color}][bold]{st.sensor2_ctrl_temp_c}°C[/bold][/{ctrl_color}]",
        f"APST Latency: {apst_str}\nCmdline Disabled: {st.cmdline_apst_disabled}",
    )

    tp = report.thermal_power
    pl1_color = "red" if (tp.msr_pl1_w or 0.0) > 90.0 else "green"
    grid.add_row(
        "CPU & Alienware WMI",
        f"Pkg Temp: [bold]{tp.cpu_package_temp_c}°C[/bold] (Max Core: {tp.cpu_core_max_temp_c}°C)\n"
        f"CPU Fan: {tp.cpu_fan_rpm} RPM (Boost: {tp.cpu_fan_boost}) | "
        f"GPU Fan: {tp.gpu_fan_rpm} RPM (Boost: {tp.gpu_fan_boost})",
        f"Profile: [bold]{tp.platform_profile}[/bold] (EPP: {tp.epp})\n"
        f"MSR PL1: [{pl1_color}]{tp.msr_pl1_w}W[/{pl1_color}] / PL2: {tp.msr_pl2_w}W | "
        f"MMIO PL1: {tp.mmio_pl1_w}W",
    )

    gpu = report.gpu_display
    grid.add_row(
        "RTX 2070 Max-Q + i915",
        f"GPU Temp: [bold]{gpu.gpu_temp_c}°C[/bold] | Power: {gpu.gpu_power_w}W / {gpu.gpu_power_cap_w}W\n"
        f"VRAM: {gpu.gpu_vram_used_mib} / {gpu.gpu_vram_total_mib} MiB ({gpu.gpu_pstate})",
        f"DRM KMS Poll Disabled: {gpu.drm_kms_poll_disabled} (Hogs: {gpu.i915_workqueue_hog_count}x)\n"
        f"Flatpak GL Mismatch: {gpu.flatpak_driver_mismatch}",
    )

    mem = report.memory
    swap_color = "red" if mem.swap_free_gib < 4.0 else "green"
    grid.add_row(
        "Memory, Swap & Writeback",
        f"RAM Avail: [bold]{mem.ram_available_gib} GiB[/bold] / {mem.ram_total_gib} GiB ({mem.ram_used_percent}%)\n"
        f"Swap Free: [{swap_color}][bold]{mem.swap_free_gib} GiB[/bold][/{swap_color}] / {mem.swap_total_gib} GiB",
        f"Dirty Pages Backlog: [bold]{mem.dirty_pages_mib} MiB[/bold]\n"
        f"swappiness={mem.swappiness}, dirty_ratio={mem.dirty_ratio}%",
    )

    iommu = report.pcie_iommu
    dmar_color = "red" if iommu.dmar_fault_count_current_boot > 0 else "green"
    grid.add_row(
        "PCIe / QCA6174 IOMMU",
        f"IOMMU Domain: {iommu.iommu_domain_type}\nWi-Fi FW: {iommu.ath10k_firmware}",
        f"DMAR Write Faults (Boot): [{dmar_color}][bold]{iommu.dmar_fault_count_current_boot}[/bold][/{dmar_color}]\n"
        f"Recent: {', '.join(iommu.dmar_recent_fault_addrs[-2:]) or 'none'}",
    )

    crit_Count = sum(1 for f in report.findings if f.severity == Severity.CRITICAL)
    warn_Count = sum(1 for f in report.findings if f.severity == Severity.WARN)
    footer = Panel(
        f"Active Alerts: [bold red]{crit_Count} CRITICAL[/bold red], "
        f"[bold yellow]{warn_Count} WARN[/bold yellow]",
        border_style="red" if crit_Count > 0 else ("yellow" if warn_Count > 0 else "green"),
    )

    return Group(header, grid, footer)


def run_live_dashboard(interval_s: float = 2.0, max_iterations: int = 0) -> None:
    """Run the live Rich TUI telemetry loop."""
    iteration = 0
    try:
        with Live(
            _build_dashboard_view(run_full_audit()),
            console=console,
            refresh_per_second=2,
        ) as live:
            while True:
                iteration += 1
                if max_iterations > 0 and iteration >= max_iterations:
                    break
                time.sleep(interval_s)
                live.update(_build_dashboard_view(run_full_audit()))
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped live telemetry watch.[/dim]")
