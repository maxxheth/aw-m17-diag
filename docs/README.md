# `aw-m17-diag` Documentation Index

Technical documentation and CLI reference for the Alienware m17 R1 (`0DG9D5`, BIOS `2.21.1`) diagnostic, workload tuning, and custom DKMS driver suite.

---

## 1. Workload Profiles & `profile` CLI Reference

`aw-m17-diag` provides 5 hardware tuning profiles managed through `aw-m17-diag profile`:

| Profile Name | ACPI `platform_profile` | CPU `EPP` | CPU RAPL (`PL1` / `PL2`) | CPU Max Clock | GPU Boost Clock | Unplugged `alx` NIC | Primary Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`extreme-performance`** | `balanced-performance` | `performance` (`0x00`) | `70W` / `90W` | `4.50 GHz` | Unlocked (`90W` `P0`) | Auto-unbound | Maximum zero-compromise all-core CPU turbo + 90W GPU performance; zero frequency ramp latency. |
| **`gaming-prime`** | `balanced-performance` | `balance_performance` (`0x80`) | `55W` / `75W` | `4.50 GHz` | Unlocked (`90W` `P0`) | Auto-unbound | Cool & sustained 60Hz PRIME gaming; relieves shared CPU+GPU heatpipe by ~6–10°C while preserving `<16.67ms` vblank pacing. |
| **`ollama-heavy`** | `balanced-performance` | `balance_performance` (`0x80`) | `65W` / `85W` | `4.50 GHz` | Unlocked (`90W` `P0`) | Bound (hotplug ready) | Sustained multi-hour local LLM inference (`AVX2` + `CUDA`) with `swappiness=10` & `dirty_ratio=10`. |
| **`balanced-daily`** | `balanced` | `balance_performance` (`0x80`) | `45W` / `70W` | `4.50 GHz` | Unlocked | Bound (hotplug ready) | Quiet daily workstation profile locked to Intel nominal `45W` TDP. |
| **`powersave`** | `quiet` | `power` (`0xFF`) | `25W` / `35W` | `4.50 GHz` | Unlocked | Bound (hotplug ready) | Cool & quiet low-power operation (`25W` PL1 / `35W` PL2). |

### Profile Commands (`show`, `set`, `startup`)

```bash
# 1. Inspect all profiles, live hardware state, active session profile, and startup boot profile
uv run aw-m17-diag profile show

# 2. Apply a profile for the CURRENT SESSION ONLY (does not overwrite boot startup default)
#    Records session state in /run/aw-m17-diag/current-profile so S3 sleep/resume preserves it.
sudo $(which uv) run aw-m17-diag profile set gaming-prime
sudo $(which uv) run aw-m17-diag profile set extreme-performance

# 3. Apply a profile immediately AND set it to load automatically on system startup
sudo $(which uv) run aw-m17-diag profile set gaming-prime --startup

# 4. Dedicated Startup Profile Management (aw-m17-diag profile startup)
#    Check current startup profile, systemd service (aw-m17-profile.service), and S3 sleep hook:
uv run aw-m17-diag profile startup --status

#    Configure a profile to load automatically on every boot (and apply it now):
sudo $(which uv) run aw-m17-diag profile startup gaming-prime

#    Configure the boot startup profile without mutating the current live session:
sudo $(which uv) run aw-m17-diag profile startup balanced-daily --no-apply-now

#    Disable automatic startup profile loading on boot:
sudo $(which uv) run aw-m17-diag profile startup --disable
```

---

## 2. Guides & Technical References

- **[Thermal, Power & Fan Fine-Tuning Guide (`thermal-and-gpu-tuning.md`)](./thermal-and-gpu-tuning.md)**
  - Shared CPU/GPU copper heatpipe architecture and hardware thermal thresholds (`i7-9750H`, `RTX 2070 Max-Q`, and DRAM-less `Inland QN450 SM2268XT` NVMe).
  - Analysis of the **84°C** gaming load reading vs. GPU Boost 4.0 voltage/frequency step-down (`78°C–80°C`), VBIOS `GPU Target Temp` (`87°C`), `GPU Slowdown Temp` (`94°C`), and `GPU Shutdown Temp` (`99°C`).
  - Detailed breakdown and tradeoffs of the three fine-tuning levers:
    1. **Option 1:** Shared heatpipe CPU RAPL (`PL1`/`PL2`), `EPP` (`balance_performance` vs `performance`), and why `scaling_max_freq` must remain unlocked (`4.50 GHz`) for 60Hz PRIME vblank deadlines (`<16.67ms`).
    2. **Option 2:** RTX 2070 Max-Q V/F sweet-spotting via `nvidia-smi -lgc` vs. unlocked `-rgc` (and why mobile Max-Q VBIOS `90.06.2d.00.dc` locks `nvidia-smi -pl`).
    3. **Option 3:** Kernel-space AWCC `custom` (`0x00`) WMI `0x15` activation to unlock manual `fan1_boost` / `fan2_boost` pre-staging and avoid userspace `-EINVAL` (`Errno 22`) and preset `-EBADRQC` (`Errno 56`).
  - Side-by-side **Preset Decision Matrix** (`Baseline` / `extreme-performance` vs. `Preset A: Cool & Consistent`, `Preset B: gaming-prime`, and `Preset C: Aggressive Pre-Staged Fans`).
  - **Technical Appendix (Section 5):** Post-S3-Resume **30W / 810 MHz (`P5`) GPU Clamp** (`_PSR=0` race, `.bss+0x1fece0` state cache, and `nvidia_power_unclamp.ko`).
  - **Technical Appendix (Section 6):** Unplugged Killer E2500 (`alx`, `0000:6f:00.0`) **PCIe MMIO Stats Stall** (`alx_update_hw_stats` 49-register `ioread32` bottleneck under Wine/Ubisoft Connect that dropped framerates to **14.0 FPS**, and automatic unbinding in `gaming-prime` / `extreme-performance`).
  - **Startup & S3 Resume Persistence Guide (Section 7):** How `/etc/aw-m17-diag/startup-profile`, `/run/aw-m17-diag/current-profile`, `/etc/systemd/system/aw-m17-profile.service`, and `/lib/systemd/system-sleep/zz-aw-m17-profile` interact.
- **[Initial Architecture & Stabilization Plan (`../PLAN.md`)](../PLAN.md)**
  - Root-cause forensics for historical crashes, NVMe APST (`default_ps_max_latency_us`), `ath10k_pci` DMAR faults, and 3-tier mitigation design.
