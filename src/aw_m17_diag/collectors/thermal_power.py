"""CPU temperatures, Alienware WMI fans, ACPI platform profile, and RAPL collector."""

from __future__ import annotations

from pathlib import Path

from aw_m17_diag.collectors._utils import (
    find_hwmon_by_name,
    get_current_boot_kernel_logs,
    read_int_safe,
    read_text_safe,
)
from aw_m17_diag.models import ThermalPowerSnapshot


def collect_thermal_power(
    sys_root: Path = Path("/sys"),
    klog: str | None = None,
) -> ThermalPowerSnapshot:
    """Collect coretemp, alienware_wmi fans/temps, platform profile, and MSR/MMIO RAPL limits."""
    hwmon_root = sys_root / "class" / "hwmon"

    # 1. Coretemp (Intel CPU package + cores)
    coretemp_dir = find_hwmon_by_name("coretemp", hwmon_root)
    pkg_temp_c: float | None = None
    core_max_c: float | None = None
    if coretemp_dir is not None:
        t1 = read_int_safe(coretemp_dir / "temp1_input")
        if t1 is not None:
            pkg_temp_c = round(t1 / 1000.0, 1)
        core_temps: list[float] = []
        for idx in range(2, 12):
            ti = read_int_safe(coretemp_dir / f"temp{idx}_input")
            if ti is not None:
                core_temps.append(round(ti / 1000.0, 1))
        if core_temps:
            core_max_c = max(core_temps)

    # 2. Alienware WMI hwmon (CPU/GPU fans, boost, EC temps)
    aw_dir = find_hwmon_by_name("alienware_wmi", hwmon_root)
    aw_cpu_c: float | None = None
    aw_gpu_c: float | None = None
    cpu_fan_rpm: int | None = None
    gpu_fan_rpm: int | None = None
    cpu_fan_boost: int | None = None
    gpu_fan_boost: int | None = None

    if aw_dir is not None:
        awt1 = read_int_safe(aw_dir / "temp1_input")
        awt2 = read_int_safe(aw_dir / "temp2_input")
        if awt1 is not None:
            aw_cpu_c = round(awt1 / 1000.0, 1)
        if awt2 is not None:
            aw_gpu_c = round(awt2 / 1000.0, 1)
        cpu_fan_rpm = read_int_safe(aw_dir / "fan1_input")
        gpu_fan_rpm = read_int_safe(aw_dir / "fan2_input")
        cpu_fan_boost = read_int_safe(aw_dir / "fan1_boost")
        gpu_fan_boost = read_int_safe(aw_dir / "fan2_boost")

    # 3. ACPI Platform Profile
    acpi_dir = sys_root / "firmware" / "acpi"
    profile = read_text_safe(acpi_dir / "platform_profile", default="unknown")
    choices_raw = read_text_safe(acpi_dir / "platform_profile_choices")
    choices = tuple(choices_raw.split()) if choices_raw else ()

    # 4. CPUFreq / intel_pstate
    cpufreq_dir = sys_root / "devices" / "system" / "cpu" / "cpu0" / "cpufreq"
    scaling_driver = read_text_safe(cpufreq_dir / "scaling_driver", default="unknown")
    scaling_gov = read_text_safe(cpufreq_dir / "scaling_governor", default="unknown")
    epp = read_text_safe(cpufreq_dir / "energy_performance_preference", default="unknown")

    # 5. RAPL Power Limits (MSR vs MMIO)
    rapl_msr = sys_root / "class" / "powercap" / "intel-rapl:0"
    rapl_mmio = sys_root / "class" / "powercap" / "intel-rapl-mmio:0"

    msr_pl1_uw = read_int_safe(rapl_msr / "constraint_0_power_limit_uw")
    msr_pl2_uw = read_int_safe(rapl_msr / "constraint_1_power_limit_uw")
    mmio_pl1_uw = read_int_safe(rapl_mmio / "constraint_0_power_limit_uw")
    mmio_pl2_uw = read_int_safe(rapl_mmio / "constraint_1_power_limit_uw")

    msr_pl1_w = round(msr_pl1_uw / 1_000_000.0, 1) if msr_pl1_uw is not None else None
    msr_pl2_w = round(msr_pl2_uw / 1_000_000.0, 1) if msr_pl2_uw is not None else None
    mmio_pl1_w = round(mmio_pl1_uw / 1_000_000.0, 1) if mmio_pl1_uw is not None else None
    mmio_pl2_w = round(mmio_pl2_uw / 1_000_000.0, 1) if mmio_pl2_uw is not None else None

    logs = klog if klog is not None else get_current_boot_kernel_logs()
    invalid_crit = "Invalid critical threshold (-274000)" in logs
    wmi_bug = (
        "WQBC data block query control method not found" in logs
        or "WMBD method block execution control method not found" in logs
    )

    return ThermalPowerSnapshot(
        cpu_package_temp_c=pkg_temp_c,
        cpu_core_max_temp_c=core_max_c,
        aw_cpu_temp_c=aw_cpu_c,
        aw_gpu_temp_c=aw_gpu_c,
        cpu_fan_rpm=cpu_fan_rpm,
        gpu_fan_rpm=gpu_fan_rpm,
        cpu_fan_boost=cpu_fan_boost,
        gpu_fan_boost=gpu_fan_boost,
        platform_profile=profile,
        available_profiles=choices,
        scaling_driver=scaling_driver,
        scaling_governor=scaling_gov,
        epp=epp,
        msr_pl1_w=msr_pl1_w,
        msr_pl2_w=msr_pl2_w,
        mmio_pl1_w=mmio_pl1_w,
        mmio_pl2_w=mmio_pl2_w,
        acpi_invalid_crit_temp_bug=invalid_crit,
        wmi_missing_methods_bug=wmi_bug,
    )
