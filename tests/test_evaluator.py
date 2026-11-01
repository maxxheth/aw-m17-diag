"""Unit tests for rules evaluator, preflight memory budget checks, and profile dry-run."""

from __future__ import annotations

import errno
from pathlib import Path
from unittest.mock import patch

from aw_m17_diag.engine.evaluator import evaluate_preflight, evaluate_snapshots
from aw_m17_diag.engine.profiles import (
    apply_workload_profile,
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
