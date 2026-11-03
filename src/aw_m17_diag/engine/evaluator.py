"""Rules engine evaluating system health across all 8 Alienware m17 hardware minefields."""

from __future__ import annotations

import platform
from pathlib import Path

from aw_m17_diag.collectors._utils import get_current_boot_kernel_logs, read_text_safe
from aw_m17_diag.collectors.gpu_display import collect_gpu_display
from aw_m17_diag.collectors.memory import collect_memory
from aw_m17_diag.collectors.pcie_iommu import collect_pcie_iommu
from aw_m17_diag.collectors.storage import collect_storage
from aw_m17_diag.collectors.thermal_power import collect_thermal_power
from aw_m17_diag.engine.driver_advisor import evaluate_driver_candidates
from aw_m17_diag.models import (
    GpuDisplaySnapshot,
    HealthFinding,
    MemorySnapshot,
    PcieIommuSnapshot,
    RemediationTier,
    Severity,
    StorageSnapshot,
    SystemAuditReport,
    ThermalPowerSnapshot,
)


def evaluate_snapshots(
    storage: StorageSnapshot,
    memory: MemorySnapshot,
    pcie_iommu: PcieIommuSnapshot,
    gpu_display: GpuDisplaySnapshot,
    thermal_power: ThermalPowerSnapshot,
) -> tuple[HealthFinding, ...]:
    """Evaluate all collected hardware snapshots and return prioritized HealthFindings."""
    findings: list[HealthFinding] = []

    # 1. Minefield 1: Inland QN450 (SM2268XT [126f:2268]) NVMe APST Latency
    apst_ok = storage.apst_max_latency_us == 0 and storage.cmdline_apst_disabled
    if storage.apst_max_latency_us == 0 and not storage.cmdline_apst_disabled:
        findings.append(
            HealthFinding(
                check_id="NVME-APST-01",
                subsystem="Storage (NVMe)",
                title="Inland QN450 (SM2268XT) APST Disabled in Live Sysfs Only",
                severity=Severity.WARN,
                current_value="sysfs=0 us, GRUB cmdline missing",
                expected_value="nvme_core.default_ps_max_latency_us=0 in GRUB & sysfs",
                explanation=(
                    "APST is disabled for the current boot, but will revert to 100000 us "
                    "on next reboot unless persisted in /etc/default/grub."
                ),
                remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    elif not apst_ok:
        findings.append(
            HealthFinding(
                check_id="NVME-APST-01",
                subsystem="Storage (NVMe)",
                title="Inland QN450 (SM2268XT [126f:2268]) APST Deep Sleep Crash Risk",
                severity=Severity.CRITICAL,
                current_value=f"{storage.apst_max_latency_us} us (APST Active)",
                expected_value="0 us (APST Disabled) or SM2268XT PCI Quirk",
                explanation=(
                    "The DRAM-less Silicon Motion SM2268XT controller drops off PCIe bus 6e:00.0 "
                    "when waking from deep APST states during burst I/O (causing instant OS lockups)."
                ),
                remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="NVME-APST-01",
                subsystem="Storage (NVMe)",
                title="Inland QN450 (SM2268XT) APST Protection",
                severity=Severity.PASS,
                current_value="0 us (Persistent + Live)",
                expected_value="0 us",
                explanation="NVMe Autonomous Power State Transitions are disabled.",
                remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
                remediation_cmd="None",
            )
        )

    # 2. Minefield 1B: SM2268XT Firmware / Hwmon Quirks (0xFFFE K, bad crto:14)
    if storage.bogus_hwmon_max_detected or storage.dmesg_bad_crto_detected:
        ctrl_temp_str = (
            f"{storage.sensor2_ctrl_temp_c}°C" if storage.sensor2_ctrl_temp_c is not None else "N/A"
        )
        findings.append(
            HealthFinding(
                check_id="NVME-QUIRK-02",
                subsystem="Storage (NVMe)",
                title="SM2268XT Controller Quirk & 65,261.85°C (0xFFFE K) Hwmon Bug",
                severity=Severity.INFO,
                current_value=f"bad_crto={storage.dmesg_bad_crto_detected}, CtrlTemp={ctrl_temp_str}, 0xFFFE_max={storage.bogus_hwmon_max_detected}",
                expected_value="PCI_VDEVICE(SMI, 0x2268) in nvme_id_table",
                explanation=(
                    "Kernel lacks a built-in pci_device_id quirk for Silicon Motion [126f:2268], "
                    "causing CRTO register warnings and 65,261.85°C bogus hwmon max thresholds."
                ),
                remediation_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
                remediation_cmd="uv run aw-m17-diag driver-advisor --export-dkms",
            )
        )

    # 3. Minefield 2: Qualcomm Atheros QCA6174 [168c:003e] DMAR Write Faults
    if pcie_iommu.dmar_fault_count_current_boot > 0:
        findings.append(
            HealthFinding(
                check_id="IOMMU-DMAR-01",
                subsystem="PCIe / IOMMU",
                title="Qualcomm QCA6174 [168c:003e] Intel VT-d DMAR Write Fault Flood",
                severity=Severity.CRITICAL
                if pcie_iommu.dmar_fault_count_current_boot >= 10
                else Severity.WARN,
                current_value=f"{pcie_iommu.dmar_fault_count_current_boot} faults ({pcie_iommu.iommu_domain_type})",
                expected_value="0 DMAR faults (DMA_BIDIRECTIONAL DKMS fix or iommu=pt)",
                explanation=(
                    "ath10k_pci maps Copy Engine rings as DMA_TO_DEVICE (read-only PTE), "
                    "causing Intel VT-d to block hardware completion writes on 70:00.0."
                ),
                remediation_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply  # or build ath10k-dmar-fix DKMS",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="IOMMU-DMAR-01",
                subsystem="PCIe / IOMMU",
                title="Qualcomm QCA6174 [168c:003e] DMAR Fault Status",
                severity=Severity.PASS,
                current_value=f"0 faults ({pcie_iommu.iommu_domain_type})",
                expected_value="0 faults",
                explanation="No Intel VT-d DMAR write faults recorded in current boot.",
                remediation_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
                remediation_cmd="None",
            )
        )

    # 4. Minefield 3: Swap Capacity & VM Writeback Tuning for 64 GB RAM + LLM Loads
    if memory.swap_total_gib < 16.0:
        findings.append(
            HealthFinding(
                check_id="MEM-SWAP-01",
                subsystem="Memory / Swap",
                title="Undersized Swapfile for 64 GB RAM + Local LLM Workloads",
                severity=Severity.CRITICAL,
                current_value=f"{memory.swap_total_gib} GiB total ({memory.swap_free_gib} GiB free)",
                expected_value=">= 32.0 GiB /swapfile",
                explanation=(
                    "A 2 GiB swapfile is immediately exhausted when mmap-loading 60B-120B "
                    "models in Ollama, causing kernel page-reclaim deadlocks."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="MEM-SWAP-01",
                subsystem="Memory / Swap",
                title="Swapfile Capacity for Heavy LLM Workloads",
                severity=Severity.PASS,
                current_value=f"{memory.swap_total_gib} GiB total ({memory.swap_free_gib} GiB free)",
                expected_value=">= 32.0 GiB",
                explanation="Swap headroom is sufficient to absorb LLM memory spikes.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    if memory.swappiness > 20 or memory.dirty_ratio > 15:
        findings.append(
            HealthFinding(
                check_id="MEM-SYSCTL-02",
                subsystem="Memory / VM",
                title="VM Swappiness & Dirty-Page Writeback Ratios Untuned",
                severity=Severity.WARN,
                current_value=f"swappiness={memory.swappiness}, dirty_ratio={memory.dirty_ratio}%, dirty_bg={memory.dirty_background_ratio}%",
                expected_value="swappiness=10, dirty_ratio=10%, dirty_bg=5%",
                explanation=(
                    "Default dirty_ratio=20% allows up to 12.5 GiB of dirty RAM before flushing, "
                    "which can overwhelm the DRAM-less SM2268XT NVMe write cache."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="MEM-SYSCTL-02",
                subsystem="Memory / VM",
                title="VM Swappiness & Dirty-Page Writeback Ratios",
                severity=Severity.PASS,
                current_value=f"swappiness={memory.swappiness}, dirty_ratio={memory.dirty_ratio}%, dirty_bg={memory.dirty_background_ratio}%",
                expected_value="swappiness<=15, dirty_ratio<=10%",
                explanation="VM sysctls are tuned to prevent NVMe writeback stalls.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    # 5. Minefield 4: Unwired NVIDIA TU106 Type-C UCSI Controller (01:00.3)
    if not gpu_display.ucsi_ccg_blacklisted and gpu_display.ucsi_timeout_in_dmesg:
        findings.append(
            HealthFinding(
                check_id="GPU-UCSI-01",
                subsystem="GPU / USB-C",
                title="Unwired NVIDIA TU106 Type-C UCSI Controller (01:00.3) I2C Timeout",
                severity=Severity.WARN,
                current_value="ucsi_ccg active (i2c_transfer failed -110)",
                expected_value="ucsi_ccg & i2c_nvidia_gpu blacklisted",
                explanation=(
                    "Alienware m17 routes Type-C via Intel Thunderbolt; probing 01:00.3 "
                    "stalls boot and logs i2c timeout error e0000000."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="GPU-UCSI-01",
                subsystem="GPU / USB-C",
                title="NVIDIA TU106 Type-C UCSI Controller (01:00.3) Status",
                severity=Severity.PASS,
                current_value="Blacklisted" if gpu_display.ucsi_ccg_blacklisted else "No timeout",
                expected_value="Blacklisted",
                explanation="Unwired GPU Type-C controller is blacklisted from probing.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    # 6. Minefield 5: Intel i915 / drm_kms_helper HPD CPU Workqueue Stalls
    if not gpu_display.drm_kms_poll_disabled:
        findings.append(
            HealthFinding(
                check_id="DISPLAY-HPD-01",
                subsystem="Display (i915)",
                title="DRM KMS Output Polling Hogging CPU Workqueues (>10ms Stalls)",
                severity=Severity.WARN,
                current_value=f"poll=Enabled (hogged CPU {gpu_display.i915_workqueue_hog_count}x this boot)",
                expected_value="drm_kms_helper.poll=0 (Disabled)",
                explanation=(
                    "output_poll_execute and i915_hpd_poll_init_work stall bound CPU "
                    "workqueues for >10ms every 10s (over 1,000 times overnight)."
                ),
                remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="DISPLAY-HPD-01",
                subsystem="Display (i915)",
                title="DRM KMS Output Polling CPU Workqueue Protection",
                severity=Severity.PASS,
                current_value="drm_kms_helper.poll=0 (Disabled)",
                expected_value="Disabled",
                explanation="Periodic KMS connector polling is disabled.",
                remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
                remediation_cmd="None",
            )
        )

    # 7. Minefield 6: Flatpak vs Host NVIDIA Driver Synchronization
    if gpu_display.flatpak_driver_mismatch:
        fp_list = ", ".join(gpu_display.flatpak_nvidia_gl_versions) or "none"
        findings.append(
            HealthFinding(
                check_id="GPU-FLATPAK-02",
                subsystem="GPU / Flatpak",
                title="Host vs. Flatpak NVIDIA GL Runtime Version Mismatch",
                severity=Severity.WARN,
                current_value=f"Host={gpu_display.nvidia_driver_version}, Flatpak=[{fp_list}]",
                expected_value=f"org.freedesktop.Platform.GL.nvidia-{gpu_display.nvidia_driver_version.replace('.', '-')}",
                explanation=(
                    "Mismatched NVIDIA GL runtimes cause Steam and Flatpak apps to fail "
                    "DRI3/GLX screen creation ('failed to load driver: nvidia-drm')."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="flatpak update -y",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="GPU-FLATPAK-02",
                subsystem="GPU / Flatpak",
                title="Host vs. Flatpak NVIDIA GL Runtime Sync",
                severity=Severity.PASS,
                current_value=f"Host={gpu_display.nvidia_driver_version} synced",
                expected_value="Matched",
                explanation="Flatpak NVIDIA GL runtime matches host kernel module.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    # 7B. Minefield 6B: NVIDIA RTX 2070 Max-Q Post-S3-Resume 30W / 810 MHz VRAM Clamp
    gpu_clamped = (
        gpu_display.gpu_power_cap_w is not None and gpu_display.gpu_power_cap_w < 60.0
    ) or (
        gpu_display.gpu_sw_thermal_slowdown
        and (gpu_display.gpu_temp_c is not None and gpu_display.gpu_temp_c < 75)
    )
    if gpu_clamped:
        cap_str = (
            f"{gpu_display.gpu_power_cap_w}W"
            if gpu_display.gpu_power_cap_w is not None
            else "Unknown"
        )
        mem_clk_str = (
            f"{gpu_display.gpu_clock_mem_mhz} MHz"
            if gpu_display.gpu_clock_mem_mhz is not None
            else "N/A"
        )
        findings.append(
            HealthFinding(
                check_id="GPU-POWER-03",
                subsystem="GPU / EC Power",
                title="RTX 2070 Max-Q 30W / 810 MHz VRAM Clamp (Post-S3-Resume AC/D-Notifier Desync)",
                severity=Severity.CRITICAL,
                current_value=(
                    f"Cap={cap_str}, PState={gpu_display.gpu_pstate}, "
                    f"VRAM={mem_clk_str}, SW_Therm_Slowdown={gpu_display.gpu_sw_thermal_slowdown} "
                    f"@{gpu_display.gpu_temp_c}°C"
                ),
                expected_value="Cap=90.0W, P0 (6001 MHz VRAM), SW_Therm_Slowdown=False",
                explanation=(
                    "During S3 resume, the Alienware EC briefly reports _PSR=0 (AC offline) while "
                    "re-negotiating with the 240W adapter, causing nvidia.ko to latch its 30.00W "
                    "Battery / SW Thermal Slowdown state (P5, 810 MHz GDDR6) without re-syncing AC/0xD1."
                ),
                remediation_tier=RemediationTier.TIER_3_CUSTOM_DRIVER_DKMS,
                remediation_cmd="sudo $(which uv) run aw-m17-diag profile set gaming-prime",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="GPU-POWER-03",
                subsystem="GPU / EC Power",
                title="RTX 2070 Max-Q Enforced Power Cap & Thermal Slowdown Status",
                severity=Severity.PASS,
                current_value=(
                    f"Cap={gpu_display.gpu_power_cap_w}W ({gpu_display.gpu_pstate}), "
                    f"Profile={thermal_power.platform_profile}"
                ),
                expected_value="Cap=90.0W, SW_Therm_Slowdown=False",
                explanation="GPU power ceiling is uncapped at full 90W Max-Q envelope.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    # 8. Minefield 7: CPU MSR RAPL Uncapped PL1 (200W on 45W TDP i7-9750H)
    if thermal_power.msr_pl1_w is not None and thermal_power.msr_pl1_w > 90.0:
        findings.append(
            HealthFinding(
                check_id="POWER-RAPL-01",
                subsystem="Thermal / RAPL",
                title="Uncapped CPU MSR RAPL PL1 Power Limit (200W on 45W i7-9750H)",
                severity=Severity.WARN,
                current_value=f"MSR PL1={thermal_power.msr_pl1_w}W, PL2={thermal_power.msr_pl2_w}W (MMIO PL1={thermal_power.mmio_pl1_w}W)",
                expected_value="MSR PL1 <= 65W, PL2 <= 90W",
                explanation=(
                    "BIOS sets MSR RAPL PL1 to 200W alongside a broken -274°C ACPI critical "
                    "trip point; clamping MSR PL1 to 60-65W prevents VRM/thermal spikes."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )
    else:
        findings.append(
            HealthFinding(
                check_id="POWER-RAPL-01",
                subsystem="Thermal / RAPL",
                title="CPU MSR & MMIO RAPL Power Limits",
                severity=Severity.PASS,
                current_value=f"MSR PL1={thermal_power.msr_pl1_w}W, MMIO PL1={thermal_power.mmio_pl1_w}W",
                expected_value="MSR PL1 <= 75W",
                explanation="CPU long-term power limit is within safe laptop thermal envelope.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="None",
            )
        )

    return tuple(findings)


def run_full_audit(
    sys_root: Path = Path("/sys"),
    proc_root: Path = Path("/proc"),
) -> SystemAuditReport:
    """Collect all hardware telemetry and run the full diagnostic & driver advisory ruleset."""
    klog = get_current_boot_kernel_logs()
    storage = collect_storage(sys_root=sys_root, proc_root=proc_root, klog=klog)
    memory = collect_memory(proc_root=proc_root)
    pcie_iommu = collect_pcie_iommu(sys_root=sys_root, proc_root=proc_root, klog=klog)
    gpu_display = collect_gpu_display(sys_root=sys_root, proc_root=proc_root, klog=klog)
    thermal_power = collect_thermal_power(sys_root=sys_root, klog=klog)

    findings = evaluate_snapshots(
        storage=storage,
        memory=memory,
        pcie_iommu=pcie_iommu,
        gpu_display=gpu_display,
        thermal_power=thermal_power,
    )
    advisories = evaluate_driver_candidates(
        storage=storage,
        pcie_iommu=pcie_iommu,
        gpu_display=gpu_display,
        thermal_power=thermal_power,
    )

    return SystemAuditReport(
        kernel_release=platform.release(),
        cmdline=read_text_safe(proc_root / "cmdline"),
        storage=storage,
        memory=memory,
        pcie_iommu=pcie_iommu,
        gpu_display=gpu_display,
        thermal_power=thermal_power,
        findings=findings,
        driver_advisories=advisories,
    )


def evaluate_preflight(
    report: SystemAuditReport,
    workload: str = "ollama",
    model_size_gb: float = 22.0,
) -> tuple[bool, tuple[HealthFinding, ...]]:
    """Evaluate readiness for an upcoming 'ollama' or 'gaming' workload."""
    checks: list[HealthFinding] = []

    # 1. NVMe APST must be disabled before any heavy I/O workload
    apst_disabled = report.storage.apst_max_latency_us == 0
    checks.append(
        HealthFinding(
            check_id="PRE-NVME-01",
            subsystem="NVMe Stability",
            title="SM2268XT APST Deep-Sleep Lockup Prevention",
            severity=Severity.PASS if apst_disabled else Severity.CRITICAL,
            current_value=f"{report.storage.apst_max_latency_us} us",
            expected_value="0 us",
            explanation="Prevents Inland QN450 NVMe from dropping off PCIe during burst reads/writes.",
            remediation_tier=RemediationTier.TIER_2_BOOT_PARAM,
            remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
        )
    )

    if workload.lower() == "ollama":
        # Calculate memory + swap headroom vs model_size_gb + 20% KV cache overhead
        required_gib = round(model_size_gb * 1.20, 1)
        total_avail_gib = round(report.memory.ram_available_gib + report.memory.swap_free_gib, 1)
        fits_in_ram = report.memory.ram_available_gib >= required_gib
        fits_with_swap = total_avail_gib >= required_gib and report.memory.swap_free_gib >= 16.0

        if fits_in_ram:
            mem_sev = Severity.PASS
        elif fits_with_swap:
            mem_sev = Severity.WARN
        else:
            mem_sev = Severity.CRITICAL

        checks.append(
            HealthFinding(
                check_id="PRE-LLM-MEM",
                subsystem="LLM Memory Budget",
                title=f"Memory + Swap Headroom for {model_size_gb:.1f} GB Model (+20% KV Cache)",
                severity=mem_sev,
                current_value=f"Avail RAM={report.memory.ram_available_gib} GiB, Free Swap={report.memory.swap_free_gib} GiB (Total={total_avail_gib} GiB)",
                expected_value=f">= {required_gib} GiB available with >= 16 GiB swap buffer",
                explanation=(
                    "Models exceeding physical RAM rely on swap; running with < 16 GiB free swap "
                    "risks OOM SIGKILL or kernel page-reclaim livelock (like the Oct 31 09:32 crash)."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply",
            )
        )

    elif workload.lower() == "gaming":
        gpu_clamped = (
            report.gpu_display.gpu_power_cap_w is not None
            and report.gpu_display.gpu_power_cap_w < 60.0
        ) or (
            report.gpu_display.gpu_sw_thermal_slowdown
            and (report.gpu_display.gpu_temp_c is not None and report.gpu_display.gpu_temp_c < 75)
        )
        checks.append(
            HealthFinding(
                check_id="PRE-GAME-GPU",
                subsystem="RTX 2070 Power Cap",
                title="RTX 2070 Max-Q 90W TGP & GDDR6 Clock Unclamped",
                severity=Severity.CRITICAL if gpu_clamped else Severity.PASS,
                current_value=(
                    f"Cap={report.gpu_display.gpu_power_cap_w}W, "
                    f"PState={report.gpu_display.gpu_pstate}, "
                    f"VRAM={report.gpu_display.gpu_clock_mem_mhz} MHz, "
                    f"Profile={report.thermal_power.platform_profile}"
                ),
                expected_value="Cap=90.0W (P0 / 6001 MHz under load), Profile!='balanced-performance'",
                explanation=(
                    "Detects the 30W / P5 (810 MHz VRAM) ACPI SW Thermal Slowdown clamp caused by "
                    "WMI profile 0xA1 ('balanced-performance') or S3 sleep/resume state."
                ),
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="sudo $(which uv) run aw-m17-diag profile set gaming-prime",
            )
        )
        checks.append(
            HealthFinding(
                check_id="PRE-GAME-GL",
                subsystem="Steam / Flatpak GL",
                title="Host vs. Flatpak NVIDIA GL Driver Alignment",
                severity=Severity.WARN
                if report.gpu_display.flatpak_driver_mismatch
                else Severity.PASS,
                current_value=(
                    "MISMATCH" if report.gpu_display.flatpak_driver_mismatch else "Synced"
                ),
                expected_value=f"Synced with {report.gpu_display.nvidia_driver_version}",
                explanation="Required for Steam Flatpak DRI3/GLX hardware acceleration.",
                remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
                remediation_cmd="flatpak update -y",
            )
        )

    # Check NVMe Controller Temp (Sensor 2)
    ctrl_temp = report.storage.sensor2_ctrl_temp_c or 0.0
    checks.append(
        HealthFinding(
            check_id="PRE-THERM-NVME",
            subsystem="NVMe Controller Temp",
            title="SM2268XT Controller Temperature (hwmon3 Sensor 2)",
            severity=Severity.WARN if ctrl_temp >= 70.0 else Severity.PASS,
            current_value=f"{ctrl_temp:.1f}°C",
            expected_value="< 70.0°C before starting load",
            explanation="DRAM-less controller throttles heavily above 78°C.",
            remediation_tier=RemediationTier.TIER_1_USERSPACE_SYSFS,
            remediation_cmd="sudo uv run aw-m17-diag profile set ollama-heavy",
        )
    )

    ready = all(c.severity != Severity.CRITICAL for c in checks)
    return ready, tuple(checks)
