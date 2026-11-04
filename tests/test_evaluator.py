"""Unit tests for rules evaluator, preflight memory budget checks, and profile dry-run."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

from aw_m17_diag.engine.evaluator import evaluate_preflight, evaluate_snapshots
from aw_m17_diag.engine.profiles import (
    apply_workload_profile,
    configure_startup_profile,
    get_startup_profile_status,
    get_workload_profile,
    write_fan_boost,
)
from aw_m17_diag.models import (
    MemorySnapshot,
    Severity,
    SystemAuditReport,
    WorkloadProfileName,
)
from tests.test_driver_advisor import make_sample_snapshots


def test_evaluate_snapshots_flags_critical_minefields() -> None:
    """Verify APST, DMAR floods, and 2 GiB swapfile trigger CRITICAL findings."""
    storage, pcie, gpu, tp = make_sample_snapshots()
    mem = MemorySnapshot(
        ram_total_gib=62.6,
        ram_available_gib=58.0,
        ram_used_percent=7.3,
        swap_total_gib=2.0,
        swap_free_gib=2.0,
        swap_used_percent=0.0,
        swappiness=60,
        vfs_cache_pressure=100,
        dirty_ratio=20,
        dirty_background_ratio=10,
        dirty_pages_mib=1.2,
    )
    findings = evaluate_snapshots(
        storage=storage,
        memory=mem,
        pcie_iommu=pcie,
        gpu_display=gpu,
        thermal_power=tp,
    )
    by_id = {f.check_id: f for f in findings}
    assert by_id["NVME-APST-01"].severity == Severity.CRITICAL
    assert by_id["IOMMU-DMAR-01"].severity == Severity.CRITICAL
    assert by_id["MEM-SWAP-01"].severity == Severity.CRITICAL
    assert by_id["POWER-RAPL-01"].severity == Severity.WARN


def test_evaluate_preflight_blocks_75gb_model_on_2gb_swap() -> None:
    """Verify preflight blocks a 75 GB LLM model when swap is only 2 GiB."""
    storage, pcie, gpu, tp = make_sample_snapshots()
    mem = MemorySnapshot(
        ram_total_gib=62.6,
        ram_available_gib=58.0,
        ram_used_percent=7.3,
        swap_total_gib=2.0,
        swap_free_gib=2.0,
        swap_used_percent=0.0,
        swappiness=60,
        vfs_cache_pressure=100,
        dirty_ratio=20,
        dirty_background_ratio=10,
        dirty_pages_mib=1.2,
    )
    report = SystemAuditReport(
        kernel_release="7.0.0-42-generic",
        cmdline="ro quiet splash",
        storage=storage,
        memory=mem,
        pcie_iommu=pcie,
        gpu_display=gpu,
        thermal_power=tp,
    )
    ready, checks = evaluate_preflight(
        report=report,
        workload="ollama",
        model_size_gb=75.0,
    )
    assert not ready
    by_id = {c.check_id: c for c in checks}
    assert by_id["PRE-LLM-MEM"].severity == Severity.CRITICAL


def test_apply_workload_profile_dry_run(tmp_path: Path) -> None:
    """Verify dry-run profile application records expected sysfs writes without root."""
    spec = get_workload_profile(WorkloadProfileName.OLLAMA_HEAVY)
    applied, errors = apply_workload_profile(
        spec=spec,
        dry_run=True,
        sys_root=tmp_path / "sys",
        proc_root=tmp_path / "proc",
    )
    assert not errors
    assert any("default_ps_max_latency_us <- 0" in item for item in applied)
    assert any("platform_profile <- balanced-performance" in item for item in applied)
    assert any("constraint_0_power_limit_uw <- 65000000" in item for item in applied)


def test_evaluate_snapshots_and_preflight_detect_30w_gpu_clamp() -> None:
    """Verify the 30W / P5 (810 MHz VRAM) EC thermal slowdown clamp triggers CRITICAL."""
    from dataclasses import replace

    storage, pcie, gpu, tp = make_sample_snapshots()
    clamped_gpu = replace(
        gpu,
        gpu_temp_c=51,
        gpu_power_w=27.2,
        gpu_power_cap_w=30.0,
        gpu_pstate="P5",
        gpu_clock_graphics_mhz=1185,
        gpu_clock_mem_mhz=810,
        gpu_sw_thermal_slowdown=True,
    )
    clamped_tp = replace(tp, platform_profile="balanced-performance")
    mem = MemorySnapshot(
        ram_total_gib=62.6,
        ram_available_gib=45.0,
        ram_used_percent=28.0,
        swap_total_gib=32.0,
        swap_free_gib=32.0,
        swap_used_percent=0.0,
        swappiness=10,
        vfs_cache_pressure=50,
        dirty_ratio=10,
        dirty_background_ratio=5,
        dirty_pages_mib=4.0,
    )
    findings = evaluate_snapshots(
        storage=storage,
        memory=mem,
        pcie_iommu=pcie,
        gpu_display=clamped_gpu,
        thermal_power=clamped_tp,
    )
    by_id = {f.check_id: f for f in findings}
    assert by_id["GPU-POWER-03"].severity == Severity.CRITICAL

    report = SystemAuditReport(
        kernel_release="7.0.0-42-generic",
        cmdline="ro quiet splash",
        storage=storage,
        memory=mem,
        pcie_iommu=pcie,
        gpu_display=clamped_gpu,
        thermal_power=clamped_tp,
    )
    ready, checks = evaluate_preflight(report=report, workload="gaming")
    assert not ready
    chk_by_id = {c.check_id: c for c in checks}
    assert chk_by_id["PRE-GAME-GPU"].severity == Severity.CRITICAL


def test_write_fan_boost_handles_ebadrqc_under_preset_profile(tmp_path: Path) -> None:
    """Verify Errno 56 (EBADRQC) on fan*_boost is handled cleanly when platform_profile != custom."""
    fan_path = tmp_path / "fan1_boost"
    fan_path.write_text("0\n", encoding="utf-8")

    applied: list[str] = []
    errors: list[str] = []

    with patch.object(
        Path,
        "write_text",
        side_effect=OSError(errno.EBADRQC, "Invalid request code"),
    ):
        write_fan_boost(
            path=fan_path,
            boost=35,
            platform_profile="balanced-performance",
            applied=applied,
            errors=errors,
            dry_run=False,
        )

    assert not errors
    assert len(applied) == 1
    assert "EC auto-curve managed by 'balanced-performance' profile" in applied[0]


def test_gaming_prime_unbinds_unplugged_alx_and_preserves_active_carrier(tmp_path: Path) -> None:
    """Verify gaming-prime unbinds alx when carrier=0 and keeps it bound when carrier=1."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"
    alx_drv = sys_root / "bus" / "pci" / "drivers" / "alx"
    iface_dir = alx_drv / "0000:6f:00.0" / "net" / "enp111s0"
    iface_dir.mkdir(parents=True)
    (sys_root / "bus" / "pci" / "devices" / "0000:6f:00.0").mkdir(parents=True)

    # Case 1: carrier = 0 (unplugged) -> unbind
    (iface_dir / "carrier").write_text("0\n", encoding="utf-8")
    spec = get_workload_profile(WorkloadProfileName.GAMING_PRIME)
    applied, errors = apply_workload_profile(
        spec=spec,
        dry_run=True,
        sys_root=sys_root,
        proc_root=proc_root,
    )
    assert not errors
    assert any("drivers/alx/unbind <- 0000:6f:00.0" in item for item in applied)

    # Case 2: carrier = 1 (wired Ethernet connected) -> keep bound
    (iface_dir / "carrier").write_text("1\n", encoding="utf-8")
    applied_connected, errors_connected = apply_workload_profile(
        spec=spec,
        dry_run=True,
        sys_root=sys_root,
        proc_root=proc_root,
    )
    assert not errors_connected
    assert any("carrier=1 (wired link active, kept bound)" in item for item in applied_connected)


def test_startup_profile_enable_session_decoupling_and_disable(tmp_path: Path) -> None:
    """Verify startup profile persistence is decoupled from ephemeral session profile changes."""
    etc_dir = tmp_path / "etc"
    run_dir = tmp_path / "run"
    systemd_dir = tmp_path / "systemd"
    sleep_dir = tmp_path / "sleep"
    sleep_dir.mkdir(parents=True)

    # 1. Enable gaming-prime as the startup profile
    applied, errors = configure_startup_profile(
        name=WorkloadProfileName.GAMING_PRIME,
        etc_dir=etc_dir,
        run_dir=run_dir,
        systemd_dir=systemd_dir,
        sleep_dir=sleep_dir,
    )
    assert not errors
    assert any("Startup profile 'gaming-prime' enabled on boot" in item for item in applied)

    status = get_startup_profile_status(
        etc_dir=etc_dir,
        run_dir=run_dir,
        systemd_dir=systemd_dir,
        sleep_dir=sleep_dir,
    )
    assert status.startup_profile == WorkloadProfileName.GAMING_PRIME
    assert status.session_profile == WorkloadProfileName.GAMING_PRIME
    assert status.systemd_enabled
    assert status.sleep_hook_installed

    # 2. Switching session profile without persist_startup updates session_profile only
    with patch("os.geteuid", return_value=0):
        daily_spec = get_workload_profile(WorkloadProfileName.BALANCED_DAILY)
        apply_workload_profile(
            spec=daily_spec,
            dry_run=False,
            sys_root=tmp_path / "sys",
            proc_root=tmp_path / "proc",
            persist_startup=False,
            etc_dir=etc_dir,
            run_dir=run_dir,
            systemd_dir=systemd_dir,
            sleep_dir=sleep_dir,
        )

    status_after_switch = get_startup_profile_status(
        etc_dir=etc_dir,
        run_dir=run_dir,
        systemd_dir=systemd_dir,
        sleep_dir=sleep_dir,
    )
    assert status_after_switch.startup_profile == WorkloadProfileName.GAMING_PRIME
    assert status_after_switch.session_profile == WorkloadProfileName.BALANCED_DAILY

    # 3. Disable startup profile
    d_applied, d_errors = configure_startup_profile(
        name=None,
        etc_dir=etc_dir,
        run_dir=run_dir,
        systemd_dir=systemd_dir,
        sleep_dir=sleep_dir,
    )
    assert not d_errors
    assert any("Disabled automatic startup profile loading" in item for item in d_applied)

    status_disabled = get_startup_profile_status(
        etc_dir=etc_dir,
        run_dir=run_dir,
        systemd_dir=systemd_dir,
        sleep_dir=sleep_dir,
    )
    assert status_disabled.startup_profile is None
    assert not status_disabled.systemd_enabled


def test_extreme_performance_profile_applies_performance_epp_and_70w_rapl(
    tmp_path: Path,
) -> None:
    """Verify extreme-performance sets epp=performance, 70W/90W RAPL, and unbinds unplugged alx."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"
    cpu0 = sys_root / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
    cpu0.mkdir(parents=True)
    (cpu0 / "energy_performance_preference").write_text("balance_performance\n", encoding="utf-8")
    (cpu0 / "scaling_max_freq").write_text("4500000\n", encoding="utf-8")

    spec = get_workload_profile(WorkloadProfileName.EXTREME_PERFORMANCE)
    applied, errors = apply_workload_profile(
        spec=spec,
        dry_run=True,
        sys_root=sys_root,
        proc_root=proc_root,
    )
    assert not errors
    assert any("energy_performance_preference <- performance" in item for item in applied)
    assert any("constraint_0_power_limit_uw <- 70000000" in item for item in applied)
    assert any("constraint_1_power_limit_uw <- 90000000" in item for item in applied)



