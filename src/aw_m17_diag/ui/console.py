"""Rich terminal report renderers for aw-m17-diag."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from aw_m17_diag.engine.profiles import PROFILES
from aw_m17_diag.models import (
    BootCrashRecord,
    DriverCandidateAdvisory,
    HealthFinding,
    Severity,
    SystemAuditReport,
    ThermalPowerSnapshot,
)

console = Console()


def _severity_badge(sev: Severity) -> str:
    """Format a Severity enum into a colored Rich markup badge."""
    if sev == Severity.PASS:
        return "[bold green]PASS[/bold green]"
    if sev == Severity.INFO:
        return "[bold cyan]INFO[/bold cyan]"
    if sev == Severity.WARN:
        return "[bold yellow]WARN[/bold yellow]"
    return "[bold white on red]CRITICAL[/bold white on red]"


def render_audit_report(report: SystemAuditReport) -> None:
    """Render the comprehensive 360-degree Alienware m17 hardware & minefield audit."""
    console.print(
        Panel.fit(
            f"[bold cyan]Alienware m17 (0DG9D5) — Hardware & Minefield Diagnostic Audit[/bold cyan]\n"
            f"[dim]Kernel:[/dim] {report.kernel_release}  |  [dim]Cmdline:[/dim] {report.cmdline}",
            border_style="cyan",
        )
    )

    # 1. Hardware Telemetry Summary Table
    hw_table = Table(title="Live Hardware & Subsystem Telemetry", show_lines=True, expand=True)
    hw_table.add_column("Subsystem", style="bold cyan", ratio=18)
    hw_table.add_column("Device / Driver", ratio=28)
    hw_table.add_column("Live Metrics & Configuration", ratio=54)

    st = report.storage
    hw_table.add_row(
        "NVMe Storage",
        f"{st.model}\n[{st.pci_id}] FW: {st.firmware_rev}",
        f"APST Max Latency: {st.apst_max_latency_us} us (Cmdline disabled: {st.cmdline_apst_disabled})\n"
        f"Temps: Composite={st.composite_temp_c}°C, NAND={st.sensor1_nand_temp_c}°C, "
        f"[bold]Ctrl(S2)={st.sensor2_ctrl_temp_c}°C[/bold]",
    )

    mem = report.memory
    hw_table.add_row(
        "Memory & Swap",
        f"RAM: {mem.ram_total_gib} GiB\nSwap: {mem.swap_total_gib} GiB",
        f"Avail RAM: {mem.ram_available_gib} GiB ({mem.ram_used_percent}% used) | "
        f"Free Swap: {mem.swap_free_gib} GiB\n"
        f"swappiness={mem.swappiness}, dirty_ratio={mem.dirty_ratio}%, "
        f"dirty_bg={mem.dirty_background_ratio}%, Dirty={mem.dirty_pages_mib} MiB",
    )

    iommu = report.pcie_iommu
    hw_table.add_row(
        "PCIe / Wi-Fi / IOMMU",
        f"QCA6174 [168c:003e] (ath10k_pci)\nFW: {iommu.ath10k_firmware}",
        f"IOMMU Domain: {iommu.iommu_domain_type}\n"
        f"DMAR Faults (this boot): [bold]{iommu.dmar_fault_count_current_boot}[/bold] "
        f"({', '.join(iommu.dmar_recent_fault_addrs[-2:]) or 'none'})",
    )

    gpu = report.gpu_display
    clk_str = (
        f" | Clocks: Core={gpu.gpu_clock_graphics_mhz}MHz, VRAM={gpu.gpu_clock_mem_mhz}MHz"
        if gpu.gpu_clock_graphics_mhz is not None
        else ""
    )
    therm_clamp_str = (
        " [bold red](SW Thermal Slowdown ACTIVE!)[/bold red]" if gpu.gpu_sw_thermal_slowdown else ""
    )
    hw_table.add_row(
        "GPU & Hybrid Display",
        f"RTX 2070 Max-Q [10de:1f10] + i915\nNVIDIA Driver: {gpu.nvidia_driver_version}",
        f"Temp: {gpu.gpu_temp_c}°C | Power: {gpu.gpu_power_w}W / {gpu.gpu_power_cap_w}W | "
        f"VRAM: {gpu.gpu_vram_used_mib}/{gpu.gpu_vram_total_mib} MiB ({gpu.gpu_pstate}){clk_str}{therm_clamp_str}\n"
        f"KMS Poll Disabled: {gpu.drm_kms_poll_disabled} (Hogs: {gpu.i915_workqueue_hog_count}x) | "
        f"UCSI Blacklisted: {gpu.ucsi_ccg_blacklisted}",
    )

    tp = report.thermal_power
    hw_table.add_row(
        "Thermal, Fans & RAPL",
        f"i7-9750H ({tp.scaling_driver}/{tp.scaling_governor})\nProfile: {tp.platform_profile} (EPP: {tp.epp})",
        f"CPU Pkg: {tp.cpu_package_temp_c}°C (Max Core: {tp.cpu_core_max_temp_c}°C)\n"
        f"Fans: CPU={tp.cpu_fan_rpm} RPM (Boost {tp.cpu_fan_boost}), GPU={tp.gpu_fan_rpm} RPM (Boost {tp.gpu_fan_boost})\n"
        f"MSR RAPL: PL1={tp.msr_pl1_w}W / PL2={tp.msr_pl2_w}W | MMIO RAPL: PL1={tp.mmio_pl1_w}W / PL2={tp.mmio_pl2_w}W",
    )

    console.print(hw_table)
    console.print()

    # 2. Evaluated Minefield Findings Table
    find_table = Table(
        title="Evaluated Hardware Minefields & Stability Checks",
        show_lines=True,
        expand=True,
    )
    find_table.add_column("ID", style="dim", min_width=12, ratio=12)
    find_table.add_column("Severity", min_width=9, ratio=10)
    find_table.add_column("Check / Explanation", ratio=40)
    find_table.add_column("Current vs. Target", ratio=20)
    find_table.add_column("Remediation Tier", ratio=18)

    for f in report.findings:
        find_table.add_row(
            f.check_id,
            _severity_badge(f.severity),
            f"[bold]{f.title}[/bold]\n[dim]{f.explanation}[/dim]",
            f"[yellow]Cur:[/yellow] {f.current_value}\n[green]Exp:[/green] {f.expected_value}",
            f"{f.remediation_tier}\n[cyan]{f.remediation_cmd}[/cyan]",
        )

    console.print(find_table)

    crit_count = sum(1 for f in report.findings if f.severity == Severity.CRITICAL)
    warn_count = sum(1 for f in report.findings if f.severity == Severity.WARN)
    if crit_count > 0 or warn_count > 0:
        console.print(
            Panel(
                f"[bold yellow]Summary:[/bold yellow] {crit_count} CRITICAL and {warn_count} WARN items detected.\n"
                f"Run [bold green]sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply[/bold green] "
                f"to apply immediate system-wide stabilizations, or run "
                f"[bold cyan]uv run aw-m17-diag driver-advisor[/bold cyan] for custom DKMS driver analysis.",
                border_style="yellow" if crit_count == 0 else "red",
            )
        )
    else:
        console.print(
            Panel(
                "[bold green]All hardware minefields and kernel parameters are optimally configured![/bold green]",
                border_style="green",
            )
        )


def render_crash_report(
    records: tuple[BootCrashRecord, ...],
    syslog_null_bytes: int,
) -> None:
    """Render forensic boot crash analysis."""
    console.print(
        Panel.fit(
            "[bold magenta]Alienware m17 — Post-Mortem Boot & Freeze Forensics[/bold magenta]",
            border_style="magenta",
        )
    )
    if syslog_null_bytes > 0:
        console.print(
            Panel(
                f"[bold red]Smoking Gun Detected in /var/log/syslog:[/bold red] Found [bold]{syslog_null_bytes}[/bold] "
                "raw NULL bytes (0x00). This confirms an abrupt hardware/NVMe controller freeze "
                "or hard power cutoff occurred while dirty log pages were buffered in RAM.",
                border_style="red",
            )
        )

    table = Table(title="Recent Boot Session Forensics", show_lines=True, expand=True)
    table.add_column("Boot", style="bold", min_width=5, ratio=6)
    table.add_column("Time Window", ratio=26)
    table.add_column("Status", min_width=12, ratio=12)
    table.add_column("DMAR", justify="right", min_width=6, ratio=8)
    table.add_column("Forensic Summary & Final Active Processes", ratio=48)

    for rec in records:
        if rec.boot_offset == 0:
            status_str = "[bold cyan]CURRENT[/bold cyan]"
        elif rec.clean_shutdown:
            status_str = "[bold green]CLEAN EXIT[/bold green]"
        else:
            status_str = "[bold white on red]HARD FREEZE[/bold white on red]"

        detail = rec.summary
        if rec.oom_or_sigkill_events:
            detail += "\n[red]Kill/OOM Events:[/red]\n  • " + "\n  • ".join(
                rec.oom_or_sigkill_events
            )

        table.add_row(
            str(rec.boot_offset),
            f"Start: {rec.first_entry}\nEnd:   {rec.last_entry}",
            status_str,
            str(rec.dmar_fault_count),
            detail,
        )

    console.print(table)


def render_preflight_report(
    workload: str,
    model_size_gb: float,
    ready: bool,
    checks: tuple[HealthFinding, ...],
) -> None:
    """Render pre-flight workload readiness report."""
    status_text = (
        "[bold green]READY FOR WORKLOAD[/bold green]"
        if ready
        else "[bold white on red]NOT SAFE — CRITICAL RISKS DETECTED[/bold white on red]"
    )
    console.print(
        Panel.fit(
            f"[bold cyan]Pre-Flight Workload Check:[/bold cyan] [bold]{workload.upper()}[/bold] "
            f"(Target Model Size: {model_size_gb:.1f} GB) — {status_text}",
            border_style="green" if ready else "red",
        )
    )
    table = Table(show_lines=True)
    table.add_column("Check ID", width=16)
    table.add_column("Status", width=10)
    table.add_column("Assessment", width=48)
    table.add_column("Current vs. Required", width=36)

    for chk in checks:
        table.add_row(
            chk.check_id,
            _severity_badge(chk.severity),
            f"[bold]{chk.title}[/bold]\n[dim]{chk.explanation}[/dim]",
            f"Cur: {chk.current_value}\nReq: {chk.expected_value}",
        )
    console.print(table)


def render_driver_advisories(
    advisories: tuple[DriverCandidateAdvisory, ...],
    exported_files: tuple[Path, ...] = (),
) -> None:
    """Render the Custom Driver & Kernel Quirk Advisory Engine report."""
    console.print(
        Panel.fit(
            "[bold cyan]Custom Driver & Kernel Quirk Advisory Engine[/bold cyan]\n"
            "[dim]Evaluates when a custom DKMS module or kernel source patch is superior to boot parameters.[/dim]",
            border_style="cyan",
        )
    )

    for adv in advisories:
        status_badge = (
            "[bold yellow]ACTION / CANDIDATE APPLICABLE[/bold yellow]"
            if adv.currently_needed
            else "[bold green]MITIGATED / OPTIMAL[/bold green]"
        )
        body = (
            f"[bold]Target Hardware:[/bold] {adv.target_device} ([cyan]{adv.pci_id}[/cyan], driver: [cyan]{adv.active_driver}[/cyan])\n"
            f"[bold]Recommended Approach:[/bold] [bold magenta]{adv.recommended_tier}[/bold magenta]  |  {status_badge}\n"
            f"[bold]Current Status:[/bold] {adv.status_detail}\n\n"
            f"[bold yellow]Root Hardware/Driver Issue:[/bold yellow]\n  {adv.issue_summary}\n\n"
            f"[bold cyan]Boot Parameter Workaround ({adv.boot_param_workaround}):[/bold cyan]\n  {adv.boot_param_tradeoff}\n\n"
            f"[bold green]Surgical Custom Driver / Kernel Solution ({adv.kernel_source_target}):[/bold green]\n  {adv.custom_driver_solution}"
        )
        console.print(
            Panel(
                body,
                title=f"[bold]{adv.candidate_id}[/bold] — {adv.title}",
                border_style="yellow" if adv.currently_needed else "green",
            )
        )

    if exported_files:
        files_list = "\n".join(f"  • [cyan]{p}[/cyan]" for p in exported_files)
        console.print(
            Panel(
                f"[bold green]Exported Ready-to-Build DKMS & Kernel Patch Candidates:[/bold green]\n{files_list}",
                border_style="green",
            )
        )


def render_profiles_table(tp: ThermalPowerSnapshot) -> None:
    """Render available Alienware m17 workload profiles and current live state."""
    console.print(
        Panel.fit(
            f"[bold cyan]Alienware m17 Workload Tuning Profiles[/bold cyan]\n"
            f"Live ACPI Profile: [bold]{tp.platform_profile}[/bold] | "
            f"EPP: [bold]{tp.epp}[/bold] | "
            f"MSR PL1/PL2: [bold]{tp.msr_pl1_w}W / {tp.msr_pl2_w}W[/bold] | "
            f"Fan Boost: [bold]CPU={tp.cpu_fan_boost}, GPU={tp.gpu_fan_boost}[/bold]",
            border_style="cyan",
        )
    )
    table = Table(show_lines=True)
    table.add_column("Profile Name", style="bold cyan", width=18)
    table.add_column("ACPI / EPP", width=24)
    table.add_column("CPU RAPL (PL1/PL2)", width=18)
    table.add_column("Fan Boost", width=14)
    table.add_column("Description", width=46)

    for spec in PROFILES.values():
        table.add_row(
            spec.name.value,
            f"ACPI: {spec.platform_profile}\nEPP:  {spec.epp}",
            f"PL1: {spec.msr_pl1_w}W\nPL2: {spec.msr_pl2_w}W",
            f"CPU: +{spec.cpu_fan_boost}%\nGPU: +{spec.gpu_fan_boost}%",
            spec.description,
        )
    console.print(table)
