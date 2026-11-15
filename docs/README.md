# `aw-m17-diag` Documentation Index

Technical documentation and CLI reference for the Alienware m17 R1 (`0DG9D5`, BIOS `2.21.1`) diagnostic, workload tuning, and custom DKMS driver suite.

---

## 1. Workload Profiles & `profile` CLI Reference

`aw-m17-diag` provides 6 hardware tuning profiles managed through `aw-m17-diag profile`:

| Profile Name | ACPI `platform_profile` | CPU `EPP` | CPU RAPL (`PL1` / `PL2`) | CPU Max Clock | GPU Boost & V/F Curve | Exhaust Fan Boost | Unplugged `alx` NIC | Primary Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **`extreme-performance`** | `balanced-performance` | `performance` (`0x00`) | `70W` / `90W` | `4.50 GHz` | Unlocked (`90W` `P0`) | EC Auto | Auto-unbound | Maximum zero-compromise all-core CPU turbo + 90W GPU performance; zero frequency ramp latency. |
| **`unreal-engine`** | `custom` (`platform-profile-0`) | `balance_performance` (`0x80`) | `45W` / `70W` | `4.50 GHz` | `1,560 MHz` (`+135 MHz` NVML UV) | `+100%` / `+100%` | Auto-unbound | Bimodal Unreal Engine 5.8 workstation profile (Lumen/Nanite viewport + 12T `ShaderCompileWorker` / `clang++` bursts, `2%/6%` `SM2268XT` DDC writeback, `inotify` & VMA floors). |
| **`gaming-prime`** | `custom` (`platform-profile-0`) | `balance_performance` (`0x80`) | `38W` / `60W` | `4.50 GHz` | `1,560 MHz` (`+135 MHz` NVML UV) | `+100%` / `+100%` | Auto-unbound | Cool & sustained 60Hz PRIME gaming enforcing core thermal invariants (`+100%` exhaust fan boost, `+135 MHz` V/F undervolt @ `1,560 MHz`, `38W/60W` shared-heatpipe relief, `<16.67ms` vblank pacing). |
| **`ollama-heavy`** | `balanced-performance` | `balance_performance` (`0x80`) | `65W` / `85W` | `4.50 GHz` | Unlocked (`90W` `P0`) | EC Auto | Bound (hotplug ready) | Sustained multi-hour local LLM inference (`AVX2` + `CUDA`) with `swappiness=10` & `dirty_ratio=10`. |
| **`balanced-daily`** | `balanced` | `balance_performance` (`0x80`) | `45W` / `70W` | `4.50 GHz` | Unlocked | EC Auto (`0%`) | Bound (hotplug ready) | Quiet daily workstation profile locked to Intel nominal `45W` TDP. |
| **`powersave`** | `quiet` | `power` (`0xFF`) | `25W` / `35W` | `4.50 GHz` | Unlocked | EC Auto (`0%`) | Bound (hotplug ready) | Cool & quiet low-power operation (`25W` PL1 / `35W` PL2). |

### Profile Commands (`show`, `set`, `startup`)

```bash
# 1. Inspect all profiles, live hardware state, active session profile, and startup boot profile
uv run aw-m17-diag profile show

# 2. Apply a profile for the CURRENT SESSION ONLY (does not overwrite boot startup default)
#    Records session state in /run/aw-m17-diag/current-profile so S3 sleep/resume preserves it.
sudo $(which uv) run aw-m17-diag profile set gaming-prime
sudo $(which uv) run aw-m17-diag profile set gaming-prime --refresh-steam  # or -r: recycle stale Steam Flatpak GL/Vulkan mounts & reload audio
sudo $(which uv) run aw-m17-diag profile set unreal-engine
sudo $(which uv) run aw-m17-diag profile set extreme-performance

# Standalone Steam Flatpak GL/Vulkan & PipeWire refresh (fixes "Non-DX12 Video Card" after background flatpak update):
uv run aw-m17-diag refresh-steam

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
    2. **Option 2:** Native Linux NVML GPU V/F undervolting (`+135 MHz` via `nvmlDeviceSetGpcClkVfOffset`, silicon-calibrated below the `+165 MHz` `Xid 109` $V_{\min}$ cliff) paired with `nvidia-smi -lgc 300,1560` vs. unlocked `-rgc` (and why mobile Max-Q VBIOS `90.06.2d.00.dc` locks `nvidia-smi -pl`).
    3. **Option 3:** Native per-driver `/sys/class/platform-profile/platform-profile-0/profile` `custom` (`0x00`) activation in Linux 7.0 to unlock `0..100%` `fan1_boost` / `fan2_boost` (`SetAddonSpeedPercent`) cleanly under `profile_lock` without out-of-tree WMI kernel modules.
  - Side-by-side **Preset Decision Matrix** (`Baseline` / `extreme-performance` vs. `Preset A: Cool & Consistent`, `Preset B: gaming-prime`, and `Preset C: Aggressive Pre-Staged Fans`).
  - **Technical Appendix (Section 5):** Post-S3-Resume **30W / 810 MHz (`P5`) GPU Clamp** (`_PSR=0` race, `.bss+0x1fece0` state cache, and `nvidia_power_unclamp.ko`).
  - **Technical Appendix (Section 6):** Unplugged Killer E2500 (`alx`, `0000:6f:00.0`) **PCIe MMIO Stats Stall** (`alx_update_hw_stats` 49-register `ioread32` bottleneck under Wine/Ubisoft Connect that dropped framerates to **14.0 FPS**, and automatic unbinding in `gaming-prime` / `unreal-engine` / `extreme-performance`).
  - **Unreal Engine 5.8 Workstation Guide (Section 7):** Bimodal `45W/70W` RAPL + `+135 MHz @ 1,560 MHz` NVML undervolt (`Xid 109` guardband), `2%/6%` `SM2268XT` DDC writeback, `fs.inotify` / `vm.max_map_count` floors, and 8 GB GDDR6 preflight checks (`--workload unreal`).
  - **Startup & S3 Resume Persistence Guide (Section 8):** How `/etc/aw-m17-diag/startup-profile`, `/run/aw-m17-diag/current-profile`, `/etc/systemd/system/aw-m17-profile.service`, and `/lib/systemd/system-sleep/zz-aw-m17-profile` interact.
- **[Initial Architecture & Stabilization Plan (`../PLAN.md`)](../PLAN.md)**
  - Root-cause forensics for historical crashes, NVMe APST (`default_ps_max_latency_us`), `ath10k_pci` DMAR faults, and 3-tier mitigation design.
