"""Typer CLI entry point for Alienware m17 (0DG9D5) diagnostic & tuning suite."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from aw_m17_diag.collectors.crash_forensics import (
    analyze_boot_history,
    check_syslog_null_bytes,
)
from aw_m17_diag.collectors.thermal_power import collect_thermal_power
from aw_m17_diag.engine.driver_advisor import export_dkms_candidates
from aw_m17_diag.engine.evaluator import evaluate_preflight, run_full_audit
from aw_m17_diag.engine.profiles import (
    apply_workload_profile,
    configure_startup_profile,
    get_startup_profile_status,
    get_workload_profile,
)
from aw_m17_diag.models import WorkloadProfileName
from aw_m17_diag.ui.console import (
    console,
    render_audit_report,
    render_crash_report,
    render_driver_advisories,
    render_preflight_report,
    render_profiles_table,
    render_startup_profile_status,
)
from aw_m17_diag.ui.dashboard import run_live_dashboard

app = typer.Typer(
    name="aw-m17-diag",
    help="Alienware m17 (0DG9D5) Hardware Diagnostic, Crash Forensics, Workload Tuning & Custom Driver Advisory Suite.",
    no_args_is_help=True,
)
profile_app = typer.Typer(
    name="profile",
    help=(
        "Inspect or apply Alienware m17 workload tuning profiles "
        "(extreme-performance, ollama-heavy, gaming-prime, balanced-daily, powersave)."
    ),
    no_args_is_help=True,
)
app.add_typer(profile_app, name="profile")


@app.command("audit")
def cmd_audit() -> None:
    """Run a full 360-degree hardware, firmware, and kernel minefield audit."""
    report = run_full_audit()
    render_audit_report(report)


@app.command("crash-report")
def cmd_crash_report(
    boots: Annotated[
        int,
        typer.Option("--boots", "-b", help="Number of recent boots to inspect."),
    ] = 5,
) -> None:
    """Analyze recent boot journals for abrupt freezes, NULL-byte syslog cutoffs, DMAR bursts, and OOM/SIGKILLs."""
    null_bytes = check_syslog_null_bytes()
    records = analyze_boot_history(max_boots=boots)
    render_crash_report(records=records, syslog_null_bytes=null_bytes)


@app.command("preflight")
def cmd_preflight(
    workload: Annotated[
        str,
        typer.Option("--workload", "-w", help="Target workload: 'ollama' or 'gaming'."),
    ] = "ollama",
    model_size_gb: Annotated[
        float,
        typer.Option(
            "--model-size-gb",
            "-m",
            help="Target LLM model size in GB (used when workload=ollama).",
        ),
    ] = 22.0,
) -> None:
    """Verify workstation stability, NVMe APST, and RAM+Swap headroom before starting a heavy workload."""
    report = run_full_audit()
    ready, checks = evaluate_preflight(
        report=report,
        workload=workload,
        model_size_gb=model_size_gb,
    )
    render_preflight_report(
        workload=workload,
        model_size_gb=model_size_gb,
        ready=ready,
        checks=checks,
    )
    if not ready:
        raise typer.Exit(code=1)


@app.command("driver-advisor")
def cmd_driver_advisor(
    export_dkms: Annotated[
        bool,
        typer.Option(
            "--export-dkms",
            help="Export ready-to-build DKMS C modules & kernel patches into dkms-candidates/.",
        ),
    ] = False,
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Directory to write DKMS source skeletons and patches.",
        ),
    ] = Path("/var/www/aw-m17-diag/dkms-candidates"),
) -> None:
    """Evaluate when a custom DKMS driver or kernel quirk patch is superior to boot parameters."""
    report = run_full_audit()
    exported: tuple[Path, ...] = ()
    if export_dkms:
        exported = export_dkms_candidates(output_root=output_dir)
    render_driver_advisories(
        advisories=report.driver_advisories,
        exported_files=exported,
    )


@profile_app.command("show")
def cmd_profile_show() -> None:
    """Display all available Alienware m17 workload profiles and live thermal/power settings."""
    tp = collect_thermal_power()
    startup_status = get_startup_profile_status()
    render_profiles_table(tp, startup_status=startup_status)


@profile_app.command("set")
def cmd_profile_set(
    name: Annotated[
        WorkloadProfileName,
        typer.Argument(help="Profile name to apply."),
    ],
    startup: Annotated[
        bool,
        typer.Option(
            "--startup/--no-startup",
            "-s",
            help="Also configure this profile to load automatically on system startup.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview sysfs/procfs writes without mutating system."),
    ] = False,
) -> None:
    """Apply a hardware workload tuning profile (requires root unless --dry-run)."""
    spec = get_workload_profile(name)
    applied, errors = apply_workload_profile(
        spec=spec,
        dry_run=dry_run,
        persist_startup=startup,
    )
    for item in applied:
        console.print(f"[green]✓[/green] {item}")
    for err in errors:
        console.print(f"[bold red]✗[/bold red] {err}")
    if errors:
        raise typer.Exit(code=1)


@profile_app.command("startup")
def cmd_profile_startup(
    name: Annotated[
        WorkloadProfileName | None,
        typer.Argument(help="Profile name to load automatically on startup."),
    ] = None,
    disable: Annotated[
        bool,
        typer.Option(
            "--disable",
            "-d",
            help="Disable automatic startup profile loading on boot.",
        ),
    ] = False,
    status: Annotated[
        bool,
        typer.Option(
            "--status",
            help="Show current startup profile and systemd service state.",
        ),
    ] = False,
    apply_now: Annotated[
        bool,
        typer.Option(
            "--apply-now/--no-apply-now",
            help="Also apply the selected startup profile immediately to the live system.",
        ),
    ] = True,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview startup configuration changes without mutating system."),
    ] = False,
) -> None:
    """Inspect, enable, or disable automatic workload profile loading on system startup."""
    if status or (name is None and not disable):
        render_startup_profile_status(get_startup_profile_status())
        return

    if disable and name is not None:
        console.print("[bold red]✗[/bold red] Cannot specify both a profile name and --disable.")
        raise typer.Exit(code=1)

    if disable:
        applied, errors = configure_startup_profile(name=None, dry_run=dry_run)
    elif name is not None and apply_now:
        spec = get_workload_profile(name)
        applied, errors = apply_workload_profile(
            spec=spec,
            dry_run=dry_run,
            persist_startup=True,
        )
    else:
        assert name is not None
        applied, errors = configure_startup_profile(name=name, dry_run=dry_run)

    for item in applied:
        console.print(f"[green]✓[/green] {item}")
    for err in errors:
        console.print(f"[bold red]✗[/bold red] {err}")
    if errors:
        raise typer.Exit(code=1)


@app.command("watch")
def cmd_watch(
    interval: Annotated[
        float,
        typer.Option("--interval", "-i", help="Refresh interval in seconds."),
    ] = 2.0,
    iterations: Annotated[
        int,
        typer.Option(
            "--iterations",
            "-n",
            help="Stop after N iterations (0 = run until Ctrl+C).",
        ),
    ] = 0,
) -> None:
    """Launch live real-time TUI monitoring NVMe Controller Temp (S2), RAPL, Fans, Swap & DMAR faults."""
    run_live_dashboard(interval_s=interval, max_iterations=iterations)


if __name__ == "__main__":
    app()
