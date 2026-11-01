"""Hardware and kernel telemetry collectors for Alienware m17 (0DG9D5)."""

from aw_m17_diag.collectors.crash_forensics import analyze_boot_history
from aw_m17_diag.collectors.gpu_display import collect_gpu_display
from aw_m17_diag.collectors.memory import collect_memory
from aw_m17_diag.collectors.pcie_iommu import collect_pcie_iommu
from aw_m17_diag.collectors.storage import collect_storage
from aw_m17_diag.collectors.thermal_power import collect_thermal_power

__all__ = [
    "analyze_boot_history",
    "collect_gpu_display",
    "collect_memory",
    "collect_pcie_iommu",
    "collect_storage",
    "collect_thermal_power",
]
