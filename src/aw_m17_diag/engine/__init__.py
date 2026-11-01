"""Diagnostic evaluation engine, custom driver advisor, and workload profile manager."""

from aw_m17_diag.engine.driver_advisor import (
    evaluate_driver_candidates,
    export_dkms_candidates,
)
from aw_m17_diag.engine.evaluator import evaluate_preflight, run_full_audit
from aw_m17_diag.engine.profiles import (
    PROFILES,
    apply_workload_profile,
    get_workload_profile,
)

__all__ = [
    "PROFILES",
    "apply_workload_profile",
    "evaluate_driver_candidates",
    "evaluate_preflight",
    "export_dkms_candidates",
    "get_workload_profile",
    "run_full_audit",
]
