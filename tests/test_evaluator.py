"""Unit tests for rules evaluator, preflight memory budget checks, and profile dry-run."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

import pytest

from aw_m17_diag.engine.evaluator import evaluate_preflight, evaluate_snapshots
from aw_m17_diag.engine.profiles import (
    GAMING_PRIME_CPU_MAX_FREQ_KHZ,
    GAMING_PRIME_FAN_BOOST_PCT,
    GAMING_PRIME_GPU_MAX_CLOCK_MHZ,
    GAMING_PRIME_GPU_VF_OFFSET_MHZ,
    GAMING_PRIME_PL1_W,
    GAMING_PRIME_PL2_W,
    GAMING_PRIME_PLATFORM_PROFILE,
    apply_workload_profile,
    configure_startup_profile,
    get_startup_profile_status,
    get_workload_profile,
    verify_gaming_prime_invariants,
    write_fan_boost,
)
from aw_m17_diag.models import (
    MemorySnapshot,
    Severity,
    SystemAuditReport,
    WorkloadProfileName,
    WorkloadProfileSpec,
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
    """Verify gaming-prime enforces all core config invariants and carrier-aware alx unbind."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"
    alx_drv = sys_root / "bus" / "pci" / "drivers" / "alx"
    iface_dir = alx_drv / "0000:6f:00.0" / "net" / "enp111s0"
    iface_dir.mkdir(parents=True)
    (sys_root / "bus" / "pci" / "devices" / "0000:6f:00.0").mkdir(parents=True)

    pprof_dir = sys_root / "class" / "platform-profile" / "platform-profile-0"
    pprof_dir.mkdir(parents=True)
    (pprof_dir / "name").write_text("alienware-wmi\n", encoding="utf-8")
    (pprof_dir / "profile").write_text("balanced-performance\n", encoding="utf-8")

    aw_hwmon = sys_root / "class" / "hwmon" / "hwmon5"
    aw_hwmon.mkdir(parents=True)
    (aw_hwmon / "name").write_text("alienware_wmi\n", encoding="utf-8")
    (aw_hwmon / "fan1_boost").write_text("0\n", encoding="utf-8")
    (aw_hwmon / "fan2_boost").write_text("0\n", encoding="utf-8")

    # Case 1: carrier = 0 (unplugged) -> unbind + custom +100% fan boost + 38W/60W + +165MHz@1560MHz
    (iface_dir / "carrier").write_text("0\n", encoding="utf-8")
    spec = get_workload_profile(WorkloadProfileName.GAMING_PRIME)
    applied, errors = apply_workload_profile(
        spec=spec,
        dry_run=True,
        sys_root=sys_root,
        proc_root=proc_root,
    )
    assert not errors
    assert any(
        f"platform-profile-0/profile <- {GAMING_PRIME_PLATFORM_PROFILE}" in item
        for item in applied
    )
    assert any(f"fan1_boost <- {GAMING_PRIME_FAN_BOOST_PCT}" in item for item in applied)
    assert any(f"fan2_boost <- {GAMING_PRIME_FAN_BOOST_PCT}" in item for item in applied)
    assert any("drivers/alx/unbind <- 0000:6f:00.0" in item for item in applied)
    assert any(
        f"constraint_0_power_limit_uw <- {GAMING_PRIME_PL1_W * 1_000_000}" in item
        for item in applied
    )
    assert any(
        f"constraint_1_power_limit_uw <- {GAMING_PRIME_PL2_W * 1_000_000}" in item
        for item in applied
    )
    assert any(
        f"NVML GPC V/F offset <- +{GAMING_PRIME_GPU_VF_OFFSET_MHZ} MHz" in item
        for item in applied
    )
    assert any(f"nvidia-smi -lgc 300,{GAMING_PRIME_GPU_MAX_CLOCK_MHZ}" in item for item in applied)

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


def test_leaving_custom_profile_zeroes_fan_boost_first(tmp_path: Path) -> None:
    """Verify leaving 'custom' resets fan1_boost/fan2_boost to 0 before switching preset."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"

    pprof_dir = sys_root / "class" / "platform-profile" / "platform-profile-0"
    pprof_dir.mkdir(parents=True)
    (pprof_dir / "name").write_text("alienware-wmi\n", encoding="utf-8")
    (pprof_dir / "profile").write_text("custom\n", encoding="utf-8")

    aw_hwmon = sys_root / "class" / "hwmon" / "hwmon5"
    aw_hwmon.mkdir(parents=True)
    (aw_hwmon / "name").write_text("alienware_wmi\n", encoding="utf-8")
    (aw_hwmon / "fan1_boost").write_text("100\n", encoding="utf-8")
    (aw_hwmon / "fan2_boost").write_text("100\n", encoding="utf-8")

    daily = get_workload_profile(WorkloadProfileName.BALANCED_DAILY)
    applied, errors = apply_workload_profile(
        spec=daily,
        dry_run=True,
        sys_root=sys_root,
        proc_root=proc_root,
    )
    assert not errors
    fan1_idx = next(i for i, s in enumerate(applied) if "fan1_boost <- 0" in s)
    fan2_idx = next(i for i, s in enumerate(applied) if "fan2_boost <- 0" in s)
    prof_idx = next(i for i, s in enumerate(applied) if "platform-profile-0/profile <- balanced" in s)
    assert fan1_idx < prof_idx
    assert fan2_idx < prof_idx


def test_workload_profile_spec_enforces_domain_invariants() -> None:
    """Verify WorkloadProfileSpec rejects out-of-range AWCC fan boost or unsafe V/F offsets."""
    with pytest.raises(ValueError, match=r"0\.\.100%"):
        WorkloadProfileSpec(
            name=WorkloadProfileName.GAMING_PRIME,
            description="bad fan boost",
            platform_profile="custom",
            epp="balance_performance",
            msr_pl1_w=38,
            msr_pl2_w=60,
            mmio_pl1_w=38,
            mmio_pl2_w=60,
            swappiness=10,
            dirty_ratio=10,
            dirty_background_ratio=5,
            cpu_fan_boost=200,
            gpu_fan_boost=200,
            gpu_max_clock_mhz=1560,
            gpu_vf_offset_mhz=165,
        )

    with pytest.raises(ValueError, match="gpu_max_clock_mhz"):
        WorkloadProfileSpec(
            name=WorkloadProfileName.GAMING_PRIME,
            description="missing ceiling",
            platform_profile="custom",
            epp="balance_performance",
            msr_pl1_w=38,
            msr_pl2_w=60,
            mmio_pl1_w=38,
            mmio_pl2_w=60,
            swappiness=10,
            dirty_ratio=10,
            dirty_background_ratio=5,
            cpu_fan_boost=100,
            gpu_fan_boost=100,
            gpu_max_clock_mhz=None,
            gpu_vf_offset_mhz=165,
        )


def test_verify_gaming_prime_invariants_detects_drift(tmp_path: Path) -> None:
    """Verify verify_gaming_prime_invariants validates all sysfs invariants and catches drift."""
    sys_root = tmp_path / "sys"
    proc_root = tmp_path / "proc"

    acpi_dir = sys_root / "firmware" / "acpi"
    acpi_dir.mkdir(parents=True)
    (acpi_dir / "platform_profile").write_text("custom\n", encoding="utf-8")

    aw_hwmon = sys_root / "class" / "hwmon" / "hwmon5"
    aw_hwmon.mkdir(parents=True)
    (aw_hwmon / "name").write_text("alienware_wmi\n", encoding="utf-8")
    (aw_hwmon / "fan1_boost").write_text(f"{GAMING_PRIME_FAN_BOOST_PCT}\n", encoding="utf-8")
    (aw_hwmon / "fan2_boost").write_text(f"{GAMING_PRIME_FAN_BOOST_PCT}\n", encoding="utf-8")

    for dom in ("intel-rapl:0", "intel-rapl-mmio:0"):
        rdir = sys_root / "class" / "powercap" / dom
        rdir.mkdir(parents=True)
        (rdir / "constraint_0_power_limit_uw").write_text(
            f"{GAMING_PRIME_PL1_W * 1_000_000}\n", encoding="utf-8"
        )
        (rdir / "constraint_1_power_limit_uw").write_text(
            f"{GAMING_PRIME_PL2_W * 1_000_000}\n", encoding="utf-8"
        )

    cpu0 = sys_root / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
    cpu0.mkdir(parents=True)
    (cpu0 / "scaling_max_freq").write_text(
        f"{GAMING_PRIME_CPU_MAX_FREQ_KHZ}\n", encoding="utf-8"
    )

    ok, violations = verify_gaming_prime_invariants(sys_root=sys_root, proc_root=proc_root)
    assert ok
    assert violations == ()

    # Introduce drift in PL1 and fan2_boost
    (aw_hwmon / "fan2_boost").write_text("0\n", encoding="utf-8")
    (sys_root / "class" / "powercap" / "intel-rapl:0" / "constraint_0_power_limit_uw").write_text(
        "65000000\n", encoding="utf-8"
    )
    ok_drift, drift_violations = verify_gaming_prime_invariants(
        sys_root=sys_root, proc_root=proc_root
    )
    assert not ok_drift
    assert any("gpu_fan_boost=0" in v for v in drift_violations)
    assert any("msr_pl1_w=65.0W" in v for v in drift_violations)




