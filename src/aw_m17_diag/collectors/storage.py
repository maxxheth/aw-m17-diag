"""Storage collector for Inland QN450 (Silicon Motion SM2268XT [126f:2268]) NVMe SSD."""

from __future__ import annotations

from pathlib import Path

from aw_m17_diag.collectors._utils import (
    find_hwmon_by_name,
    get_current_boot_kernel_logs,
    read_int_safe,
    read_text_safe,
)
from aw_m17_diag.models import StorageSnapshot


def collect_storage(
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
    klog: str | None = None,
) -> StorageSnapshot:
    """Collect NVMe SSD health, APST latency, hwmon sensors, and SM2268XT quirks."""
    nvme_dir = sys_root / "class" / "nvme" / "nvme0"
    model = read_text_safe(nvme_dir / "model", default="Unknown NVMe")
    firmware_rev = read_text_safe(nvme_dir / "firmware_rev", default="Unknown")

    # Check PCI vendor:device on 0000:6e:00.0 (or via nvme0/device)
    dev_dir = nvme_dir / "device"
    vendor = read_text_safe(dev_dir / "vendor").removeprefix("0x")
    device = read_text_safe(dev_dir / "device").removeprefix("0x")
    pci_id = f"{vendor}:{device}" if vendor and device else "126f:2268"

    apst_latency = read_int_safe(
        sys_root / "module" / "nvme_core" / "parameters" / "default_ps_max_latency_us"
    )
    cmdline = read_text_safe(proc_root / "cmdline")
    cmdline_apst_disabled = "nvme_core.default_ps_max_latency_us=0" in cmdline

    # Read NVMe hwmon temperatures (millidegrees Celsius)
    hwmon_dir = find_hwmon_by_name("nvme", sys_root / "class" / "hwmon")
    composite_c: float | None = None
    sensor1_c: float | None = None
    sensor2_c: float | None = None
    bogus_max = False

    if hwmon_dir is not None:
        t1 = read_int_safe(hwmon_dir / "temp1_input")
        t2 = read_int_safe(hwmon_dir / "temp2_input")
        t3 = read_int_safe(hwmon_dir / "temp3_input")
        if t1 is not None:
            composite_c = round(t1 / 1000.0, 2)
        if t2 is not None:
            sensor1_c = round(t2 / 1000.0, 2)
        if t3 is not None:
            sensor2_c = round(t3 / 1000.0, 2)

        t2_max = read_int_safe(hwmon_dir / "temp2_max")
        t3_max = read_int_safe(hwmon_dir / "temp3_max")
        # SM2268XT reports 65261850 mC (0xFFFE Kelvin) on uninitialized sensor thresholds
        if (t2_max is not None and t2_max > 200_000) or (t3_max is not None and t3_max > 200_000):
            bogus_max = True

    logs = klog if klog is not None else get_current_boot_kernel_logs()
    bad_crto = "bad crto:" in logs
    unchecked_buf = "using unchecked data buffer" in logs
    old_nguid = "No UUID available providing old NGUID" in logs

    return StorageSnapshot(
        pci_id=pci_id,
        model=model,
        firmware_rev=firmware_rev,
        apst_max_latency_us=apst_latency,
        cmdline_apst_disabled=cmdline_apst_disabled,
        composite_temp_c=composite_c,
        sensor1_nand_temp_c=sensor1_c,
        sensor2_ctrl_temp_c=sensor2_c,
        bogus_hwmon_max_detected=bogus_max,
        dmesg_bad_crto_detected=bad_crto,
        dmesg_unchecked_buffer_detected=unchecked_buf,
        dmesg_old_nguid_detected=old_nguid,
    )
