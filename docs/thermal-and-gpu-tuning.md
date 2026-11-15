# Thermal, Power & Fan Fine-Tuning Guide — Alienware m17 (`0DG9D5`)

This document details the thermal architecture, hardware sensor thresholds, firmware constraints, and fine-tuning options for the **Alienware m17 R1 (`0DG9D5`, BIOS `2.21.1`)** equipped with the **Intel Core i7-9750H**, **NVIDIA GeForce RTX 2070 with Max-Q Design (`10de:1f10`)**, and **Inland QN450 NVMe (`SM2268XT`)**.

---

## 1. Hardware Thermal Topology & Sensor Thresholds

### Shared Copper Heatpipe Architecture
On the Alienware m17 R1 (`0DG9D5`), the CPU (`i7-9750H`) and dGPU (`RTX 2070 Max-Q`) are coupled to a **shared copper vapor/heatpipe assembly** exhausted by two radial blowers:
- **`fan1` (`CPU Fan`)**: Left exhaust fin stack (`~4,900 RPM` nominal max).
- **`fan2` (`Video Fan`)**: Right/rear exhaust fin stack (`~4,800 RPM` nominal max).

Because the primary heatpipes bridge both dies:
1. **Thermal Cross-Talk:** Every additional 10W dissipated by the CPU raises the water/vapor temperature inside the shared heatpipe, directly reducing the thermal delta ($\Delta T$) available to cool the RTX 2070 Max-Q die and surrounding GDDR6 VRMs.
2. **Chassis Heat Soak:** Sustained heatpipe saturation (>82°C for 30+ minutes) radiates into the M.2 bay housing the DRAM-less **Inland QN450 (`SM2268XT`)** NVMe controller.

### Component Thermal Limits Table

| Component | Sensor / Source | Optimal Sustained Load | Soft Target / Down-Binning | Heavy Throttle Threshold | Hardware Shutdown |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **NVIDIA RTX 2070 Max-Q** (`TU106`, VBIOS `90.06.2d.00.dc`) | `nvidia-smi` (`GPU Current Temp`) | **72°C – 79°C** | **78°C – 80°C** (GPU Boost 4.0 V/F step-down)<br>**87°C** (`GPU Target Temp`) | **94°C** (`GPU Slowdown Temp` — `HW Thermal Slowdown`) | **99°C** (`GPU Shutdown Temp`)<br>**102°C** (`Max Operating Temp`) |
| **Intel Core i7-9750H** (`Coffee Lake-H`, 6C/12T) | `coretemp` (`Package id 0`) | **75°C – 85°C** | **85°C – 92°C** | **100°C** (`PROCHOT#` / $T_{\text{JUNCTION}}$) | **~105°C – 110°C** (`THERMTRIP#`) |
| **Inland QN450 NVMe** (`SM2268XT`, DRAM-less) | `nvme` (`Composite` / `Sensor 2`) | **45°C – 64°C** | **70°C** (Composite / Controller warning) | **78°C** (`Sensor 2` controller severe I/O throttle) | **85°C+** (Controller reset / link drop) |

---

## 2. Evaluating an 84°C GPU Temperature Under Gaming Load

When running with the full **90.00 W** GPU power limit restored (`P0` performance state, `6001 MHz` GDDR6 clock) and **65W PL1 / 90W PL2** CPU RAPL limits, a GPU die reading of **84°C** means:

1. **Silicon Safety Margin (Safe):**
   - **10°C below** the RTX 2070 Max-Q hardware thermal slowdown point (**94°C**).
   - **15°C below** the GPU hardware thermal shutdown threshold (**99°C**).
   - **16°C below** the Intel i7-9750H `PROCHOT#` ceiling (**100°C**).
   - There is no risk of immediate silicon damage or emergency thermal shutdown at 84°C.

2. **Performance & Frametime Stability Margin (Sub-Optimal above 80°C):**
   - **GPU Boost 4.0 Down-Binning (`78°C–86°C`):** NVIDIA's 12nm Turing (`TU106`) boost algorithm begins stepping down frequency bins (~15 MHz per temperature step) once the die crosses **~78–80°C**.
   - **Proximity to the 87°C VBIOS Target Ceiling:** At **84°C**, the GPU is only **3°C away** from its **87°C** VBIOS `GPU Target Temperature`. Once a long gaming session heat-soaks the chassis by another 3°C, the VBIOS asserts `SW Thermal Slowdown` and dynamically trims board power below 90W to prevent crossing 87°C, causing periodic clock dips and **1% low frametime micro-stutters**.

---

## 3. Fine-Tuning Options & Tradeoffs

There are three primary engineering levers available on `mothership` to keep the RTX 2070 Max-Q in the **75°C–79°C** sweet spot while maximizing gaming performance and stability.

---

### Option 1: Re-Balance the Shared CPU/GPU Heatpipe (`CPU RAPL PL1/PL2` & `EPP`) — *Active in `gaming-prime`*

#### Mechanism
Previously, the `gaming-prime` profile configured:
- **CPU RAPL PL1 (Sustained):** `65W` (`65,000,000 uW`)
- **CPU RAPL PL2 (Short Burst):** `90W` (`90,000,000 uW`)
- **Energy Performance Preference (`EPP`):** `performance`

In [`src/aw_m17_diag/engine/profiles.py`](../src/aw_m17_diag/engine/profiles.py), `gaming-prime` now enforces these core configuration invariants:
- **AWCC Platform Profile (`/sys/class/platform-profile/platform-profile-0/profile`):** `custom` (`0x00`)
- **Exhaust Fan Curve Boost (`fan1_boost` / `fan2_boost`):** `+100%` / `+100%` (clamped strictly to `0..100%` `SetAddonSpeedPercent` domain; zeroed automatically before transitioning back to preset profiles)
- **CPU RAPL PL1 (Sustained):** `38W` (`38,000,000 uW`)
- **CPU RAPL PL2 (Short Burst):** `60W` (`60,000,000 uW`)
- **Energy Performance Preference (`EPP`):** `balance_performance`
- **CPU Max Turbo (`scaling_max_freq`):** `4.50 GHz` (`4,500,000 kHz` — full single-core boost unlocked)
- **GPU NVML V/F Undervolt + Boost Ceiling:** `+135 MHz` GPC V/F offset (`nvmlDeviceSetGpcClkVfOffset`) paired with `nvidia-smi -lgc 300,1560` (`1,560 MHz` running at the `~1,425 MHz` voltage step — 2 Turing `15 MHz` PLL bins of $V_{\min}$ guardband below `+165 MHz`, which triggered `NVRM: Xid 109 (CTX SWITCH TIMEOUT)` after 73 minutes of DX12/`vkd3d-proton` load)

> **Why Frequency Caps (`3.70 GHz` CPU / `1500 MHz` GPU) Cause Frame-Skipping on `eDP-1-1` (`PRIME Synchronization: 1`):**
> The internal `eDP-1-1` panel runs at `1920x1080 @ 60.01 Hz` (`16.66 ms` vblank interval) driven by Intel `i915` Pipe A with `PRIME Synchronization: 1`. Every frame rendered by the RTX 2070 Max-Q (`01:00.0`) must complete CPU main-thread/DXVK dispatch, GPU rasterization, and PCIe DMA copy to `i915` within `16.66 ms` (`scanlines 1073..1079`). Capping `scaling_max_freq` to `3.70 GHz` (`45W` PL1) and locking the GPU to `1500 MHz` pushes frame completion just past `16.66 ms` in heavy DXVK titles (*Assassin's Creed Mirage*), causing `i915` Pipe A atomic vblank misses (`i915 0000:00:02.0: [drm] *ERROR* Atomic update failure on pipe A ... min 1073, max 1079`) and severe double-buffered `60 -> 30 FPS` frame-skipping. Keeping full `4.50 GHz` CPU single-thread boost and full `90W` GPU boost while trimming sustained all-core CPU RAPL to `55W / 75W` keeps frame times safely below `16.66 ms` while relieving 10–15W of continuous heat from the shared heatpipe.

#### Tradeoffs
| Dimension | Impact |
| :--- | :--- |
| **GPU Temperature** | **-3°C to -5°C** reduction (less heat transferred across the shared heatpipe). |
| **CPU Temperature** | **-6°C to -10°C** reduction (`Package id 0` stays further from 90°C+). |
| **Average / Peak FPS** | **~0% to -1%** in GPU-bound titles at 1080p/1440p Ultra; **-2% to -4%** in heavily CPU-bound simulation/strategy games. |
| **1% Low Frametimes** | **Improved** in long sessions by preventing shared heatsink saturation. |
| **Shader Compilation** | All-core background shader compilation runs at ~3.6–3.8 GHz instead of 4.0 GHz (~5–8% slower compile bursts). |

#### Manual Verification Commands
```bash
# Set 52W PL1 / 72W PL2 across both MSR and MMIO RAPL domains
for d in /sys/class/powercap/intel-rapl:0 /sys/class/powercap/intel-rapl-mmio:0; do
  echo 52000000 | sudo tee "$d/constraint_0_power_limit_uw"
  echo 72000000 | sudo tee "$d/constraint_1_power_limit_uw"
done

# Set EPP to balance_performance so idle cores drop voltage between frames
for f in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do
  echo balance_performance | sudo tee "$f"
done
```

---

### Option 2: Sweet-Spot the RTX 2070 Max-Q V/F Curve (`nvidia-smi -lgc`) — *Optional Manual Cap*

#### Mechanism
The RTX 2070 Max-Q (`TU106`) has a default board power limit of **90.00 W** (`Min Power Limit: 1.00 W`, `Max Power Limit: 90.00 W`). On 12nm Turing silicon, the voltage/frequency curve is non-linear:
- **0W → 72W:** Near-linear frequency scaling (`~1440–1500 MHz` @ `~775–812 mV`).
- **72W → 90W (+25% heat):** Steep voltage wall (`~900–1000+ mV`) yielding only **+150 to +210 MHz** at a disproportionate thermal cost on the shared CPU/GPU heatpipe.

On the mobile RTX 2070 Max-Q (`10de:1f10`, VBIOS `90.06.2d.00.dc`), direct `nvidia-smi -pl` writes are locked by the VBIOS (`Changing power management limit is not supported`), whereas **Graphics Clock Locking (`nvidia-smi -lgc 300,1500`)** is fully supported:
- **`nvidia-smi -lgc 300,1500`:** Allows normal dynamic idle downclocking (`300 MHz`) while capping maximum boost frequency at **`1500 MHz`**, keeping GPU board power around **68W–74W** (-15W less heat on the shared heatpipe compared to 1650–1710 MHz) and preventing the GPU and CPU from saturating the shared cooler.

#### Tradeoffs
| Dimension | Impact |
| :--- | :--- |
| **GPU Temperature** | **-5°C to -8°C** reduction (typically holds **76°C – 79°C** instead of **84°C – 87°C**). |
| **GPU Boost Behavior** | Eliminates 87°C VBIOS thermal bouncing; GPU holds a flat, rock-steady clock speed across multi-hour sessions. |
| **Average / Peak FPS** | **-2% to -3.5%** peak framerate vs. uncapped 90W in cold-start benchmarks. |
| **1% Low Frametimes** | **Improved** after 20+ minutes of gameplay because the GPU never hits the 87°C VBIOS thermal throttle wall. |
| **NVMe & VRM Temps** | Reduces chassis radiant heat, keeping the DRAM-less `SM2268XT` NVMe controller cooler. |

#### Manual Verification Commands
```bash
# Enable persistence mode so settings hold across client invocations
sudo nvidia-smi -pm 1

# Approach 2A: Cap GPU board power to 80W (recommended sweet spot: 78W-82W)
sudo nvidia-smi -pl 80

# Approach 2B (Alternative): Lock GPU clock range to 300 MHz - 1680 MHz
sudo nvidia-smi -lgc 300,1680

# Reset to stock 90W / unlocked clocks at any time:
sudo nvidia-smi -pl 90
sudo nvidia-smi -rgc
```

---

### Option 3: Per-Driver `platform-profile-0` `custom` (`0x00`) Mode to Unlock `+100%` `fan1_boost` / `fan2_boost` — *Active in `gaming-prime`*

#### Mechanism & Firmware Constraints
On the Alienware m17 (`0DG9D5`), `alienware-wmi.ko` exposes `/sys/class/hwmon/hwmon*/fan1_boost` and `fan2_boost` backed by WMI method `0x15` (`AWCC_METHOD_THERMAL_CONTROL`, operation `0x02` = `AWCC_OP_SET_FAN_BOOST` / AWCC `SetAddonSpeedPercent`). Two kernel/firmware rules govern this interface:

1. **Alienware EC Preset Lock (`-EBADRQC` / `Errno 56`):**
   Whenever the Embedded Controller (EC) is in an automatic thermal preset (`cool` = `0xA2`, `quiet` = `0xA3`, `balanced` = `0xA0`, or `balanced-performance` = `0xA1`), the EC governs fan PWM curves internally (capping `fan2` around `~3,811 RPM` / `~85%` under gaming load) and rejects WMI `0x15` op `0x02` with `0xffffffff` (`-EBADRQC`).
2. **Legacy `/sys/firmware/acpi/platform_profile` vs. Per-Driver `/sys/class/platform-profile/platform-profile-0/profile`:**
   In Linux 6.14+ / 7.0 (`drivers/acpi/platform_profile.c`), legacy global `platform_profile_store()` (`/sys/firmware/acpi/platform_profile`) explicitly blocks userspace writes of `"custom"` (`if (i == PLATFORM_PROFILE_CUSTOM) return -EINVAL`), whereas the per-driver class device `profile_store()` at `/sys/class/platform-profile/platform-profile-0/profile` (`name = alienware-wmi`) **natively accepts `"custom"` in userspace** under `&profile_lock` with zero out-of-tree WMI kernel modules required.
3. **Strict `0..100%` `SetAddonSpeedPercent` Domain & Ordered Transitions:**
   AWCC firmware interprets the `fan*_boost` byte as a `0..100%` curve offset (`SetAddonSpeedPercent`). Passing out-of-domain values (`> 100`, e.g., `200`) or leaving non-zero boost latched when switching back to a preset profile can trip the WMAX EC watchdog. `WorkloadProfileSpec` and `write_fan_boost()` enforce `0 <= boost <= 100` as a hard invariant, and `_apply_platform_profile_and_fans()` zeroes `fan1_boost` / `fan2_boost` before exiting `custom` mode.

> **Critical Firmware Caveat on `0DG9D5`:**
> On this laptop, the EC's thermal profile byte also governs the ACPI D-Notifier level (`0xD1`–`0xD5`) sent to the NVIDIA GPU and the EC's internal PECI/PL4 limits. `balanced-performance` (`0xA1`, the AWCC G-Mode / high-performance table) is guaranteed to permit full 90W GPU operation. If switching the EC to `0x00` (`AWCC_PROFILE_CUSTOM`) causes the EC to drop its internal fan/power table or assert `0xD2` D-Notifier capping, `nvidia_power_unclamp.ko` must be run immediately afterward to ensure the GPU remains unclamped at 90W.

#### Tradeoffs
| Dimension | Impact |
| :--- | :--- |
| **GPU & CPU Temps** | **-3°C to -6°C** during the first 10–15 minutes of load by eliminating the EC's thermal hysteresis lag. |
| **Acoustics** | **High fan noise (~50–54 dBA)** immediately upon activating the profile (`4,800–4,900 RPM` continuous). |
| **Fan Bearing Wear** | Higher cumulative RPM hours if left enabled during light desktop or menu idle periods. |
| **Complexity** | Requires a kernel-space WMI call (Tier 3 DKMS helper) rather than a pure sysfs write, and loses the EC's automatic `0xA1` dynamic curve if userspace crashes. |

---

## 4. Combined Tuning Presets & Decision Matrix

The table below compares the current unclamped baseline against three practical combinations of Options 1–3:

| Configuration | GPU Power Limit | CPU RAPL (`PL1 / PL2`) | CPU `EPP` | EC Thermal Profile | Expected GPU Temp | Expected CPU Temp | Peak FPS vs. Baseline | 1% Low Stability | Acoustics |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Previous Unclamped Baseline** | **90W** (`P0`) | **65W / 90W** | `performance` | `balanced-performance` (`0xA1`) | **83°C – 86°C** | **86°C – 94°C** | **100% (Baseline)** | Good (risk of 87°C VBIOS trim after 30m) | Moderate–High (EC auto curve) |
| **Preset A: "Cool & Consistent" (Options 1 + 2)** *(Recommended for 60m+ sessions)* | **80W** (`-pl 80`) | **52W / 72W** | `balance_performance` | `balanced-performance` (`0xA1`) | **75°C – 79°C** | **76°C – 83°C** | **97% – 98%** (-2% to -3%) | **Best** (Zero 87°C VBIOS thermal down-binning) | Moderate (Fans stay 300–500 RPM quieter) |
| **Preset B: "Max GPU + Shared-Pipe Relief" (Option 1 Only)** *(Active `gaming-prime` default)* | **90W** (`P0`) | **55W / 75W** | `balance_performance` | `balanced-performance` (`0xA1`) | **79°C – 82°C** | **78°C – 85°C** | **99% – 100%** (~0% loss in GPU-bound games) | **Very Good** (4–5°C headroom to 87°C target) | Moderate–High (EC auto curve) |
| **Preset C: "Aggressive Pre-Staged Fans" (Options 1 + 3)** | **90W** (`P0`) | **55W / 75W** | `balance_performance` | Kernel WMI `0x00` + `fan*_boost` | **77°C – 81°C** | **76°C – 84°C** | **100%** | **Very Good** | **Loud** (Fixed high RPM even in menus) |

---

## 5. Technical Appendix: Post-S3-Resume 30W / 810 MHz (`P5`) GPU Clamp & `nvidia_power_unclamp.ko`

### Symptom
After resuming from S3 suspend-to-RAM (`deep` sleep) on BIOS `2.21.1` with NVIDIA driver `595.99.02`, the RTX 2070 Max-Q frequently wakes up severely throttled even while plugged into the 240W AC adapter:
- `Current Power Limit`: **`30.00 W`** (instead of `90.00 W`)
- `Performance State`: **`P5`** (instead of `P0`)
- `Memory Clock`: **`810 MHz`** (instead of `6001 MHz` — an **86.5% VRAM bandwidth reduction**)
- `Clocks Event Reasons`: `SW Power Cap: Active` and `SW Thermal Slowdown: Active` (even at **43°C–52°C** die temperature)

### Root Cause Analysis
1. **Transient EC `_PSR = 0` Race on S3 Wake:**
   When `mothership` wakes from S3 (`ACPI: PM: Low-level resume complete` → `ACPI: EC: EC started`), the Alienware EC takes ~200 ms to re-negotiate the Dell 1-Wire 240W adapter ID pin. During that window, `\_SB_.PCI0.AC0_._PSR` evaluates to `0` (Battery / AC offline).
2. **`nvidia.ko` Latches Battery Power Cap on Resume:**
   During `NV_PM_ACTION_RESUME`, `nvidia.ko` evaluates `_PSR = 0` and transitions the GPU into its 30.00 W battery / D-Notifier cap (`P5` state, `810 MHz` memory clock). However, `nv_restore_user_channels()` (`nv.c`) never re-queries `rm_power_source_change_event()` or `rm_request_dnotifier_state()` once `_PSR` returns to `1`.
3. **`.bss+0x1fece0` State Cache in `rm_power_source_change_event`:**
   Inside `nv-kernel.o_binary` (`595.99.02`), `rm_power_source_change_event(nv_stack_t *sp, NvU32 state)` caches the last power source state (`0` = AC, `1` = Battery) at `.bss+0x1fece0` and immediately returns (`cmp %esi, .bss+0x1fece0; je return`) if called with `0` when `.bss+0x1fece0` is already `0` from before sleep.

### How `nvidia_power_unclamp.ko` Resolves the Clamp
The DKMS helper module at [`dkms-candidates/nvidia-s3-power-unclamp/nvidia_power_unclamp.c`](../dkms-candidates/nvidia-s3-power-unclamp/nvidia_power_unclamp.c) (automatically invoked by `aw-m17-diag profile set gaming-prime` and `ollama-heavy` when `power.limit <= 35W` is detected on AC power):
1. Resolves the live `nv_linux_state_t` (`nvl`) for PCI device `10de:1f10` and verifies the `nv_acpi_object` pointer (`+0x8d8` on `595.99.02`) by validating `view.notifier_data == nvl` via `copy_from_kernel_nofault()`.
2. Calls `rm_request_dnotifier_state(sp, nvl)` to re-evaluate the ACPI `_DOD` / D-Notifier state while `_PSR == 1`.
3. Forces a `1 -> 0` state transition (`rm_power_source_change_event(sp, 1)` followed by `rm_power_source_change_event(sp, 0)`) to bypass `.bss+0x1fece0` and execute RM internal control `0x2080205b` (AC Power Source Restore).
4. Dispatches `rm_acpi_notify(sp, nvl, 0xc0)` (GPS Power Budget Refresh `0x00000122`) and `rm_acpi_notify(sp, nvl, 0xd1)` (D-Notifier Level 0 = Full 90.00 W Uncapped Power `0x20802092`).
5. Immediately unloads itself (`-EAGAIN` init completion) after restoring `enforced.power.limit = 90.00 W` and clearing `SW Power Cap` / `SW Thermal Slowdown`.

---

## 6. Technical Appendix: Unplugged Killer E2500 (`alx`) PCIe MMIO Stats Stall (14 FPS -> 62.6 FPS Fix)

### Symptom
When running Ubisoft Connect games (*Assassin's Creed Mirage*) under Wine/Proton while on Wi-Fi (`wlp112s0`) with no Ethernet cable plugged into the Qualcomm Atheros Killer E2500 Gigabit NIC (`0000:6f:00.0` `[1969:e0b1]`, interface `enp111s0`), `winedevice.exe` (`wine_sechost_service`) consumes **73.5% of a CPU core in kernel space**, starving render threads and dropping in-game framerate to **14.0 FPS**.

### Root Cause
1. **Ubisoft Connect + Wine NSI Polling:** `upc.exe` / `UplayWebCore.exe` continuously polls Windows Network Store Interface (`nsi.sys` via `wine_sechost_service`) hundreds of times per second, triggering Linux Netlink `RTM_GETLINK` / `/proc/net/dev` dumps.
2. **Synchronous PCIe MMIO Reads in `alx_get_stats64()`:** In `drivers/net/ethernet/atheros/alx/main.c`, `alx_get_stats64()` unconditionally calls `alx_update_hw_stats()`, which performs **49 synchronous uncached `ioread32()` PCIe MMIO register reads** (`0x1700..0x17c0`) on every query—even when `carrier == 0` (unplugged) or the interface is `DOWN`.

### Automatic Mitigation in `gaming-prime`
When `gaming-prime` is applied, `_manage_alx_nic()` in [`src/aw_m17_diag/engine/profiles.py`](../src/aw_m17_diag/engine/profiles.py):
- Checks `/sys/bus/pci/drivers/alx/0000:6f:00.0/net/*/carrier`.
- If `carrier != 1` (no Ethernet cable plugged in), unbinds `0000:6f:00.0` via `/sys/bus/pci/drivers/alx/unbind`, dropping `wine_sechost_service` kernel CPU overhead from **73.5% to 12%** and restoring full **60+ FPS**.
- If a wired Ethernet cable is connected (`carrier == 1`), leaves `alx` bound. Switching back to non-gaming profiles (`balanced-daily`, `ollama-heavy`, `powersave`) automatically rebinds `0000:6f:00.0`.

---

## 7. Unreal Engine 5.8 Workstation Tuning (`unreal-engine` Profile)

Unreal Engine 5.8 (`/opt/UnrealEngine`) places a **bimodal real-time 3D + 12-thread burst compute** load on `0DG9D5` that differs from both steady-state gaming (`gaming-prime`) and pure LLM compute (`ollama-heavy`):

1. **Bimodal Shared-Heatpipe Calibration (`45W` PL1 / `70W` PL2 + `+100%` Dual Blowers + `+135 MHz @ 1,560 MHz` NVML UV):**
   - Unlike games (where GPU utilization stays near 99% continuously and CPU needs only `38W`), Unreal Editor frequently launches 12-thread `ShaderCompileWorker` (DXC SPIR-V), `UnrealBuildTool` (`clang++` / `lld`), and `ZenServer` Oodle compression bursts while a real-time Lumen / Nanite viewport remains active.
   - `unreal-engine` unlocks AWCC `custom` (`+100%` / `+100%` dual-fan exhaust) and pairs **`45W` PL1 / `70W` PL2** (`+18.4%` sustained CPU budget over `gaming-prime`'s `38W`, locked to Intel's nominal `45W` TDP so Core 0 coldplate bleed does not saturate the shared vapor loop) with the **`+135 MHz` @ `1,560 MHz` NVML GPU undervolt** (preserving 2 Turing `15 MHz` PLL bins of $V_{\min}$ margin below `+165 MHz` so `UnrealEditor` never crashes with `Xid 109` / `VK_ERROR_DEVICE_LOST`).
2. **DRAM-less `SM2268XT` NVMe Writeback Smoothing (`dirty_background_ratio=2`, `dirty_ratio=6`):**
   - On `62.6 GiB` RAM with `kernel.hung_task_panic = 1` (`120s` timeout), `5% / 10%` dirty thresholds allow `3.1–6.3 GiB` of un-flushed pages before synchronous throttling, which can saturate the DRAM-less `Inland QN450` SLC cache during massive `DerivedDataCache` (`DDC`), `.pak` cooking, or debug `lld` link bursts. Tightening to **`2% / 6%` (`~1.25 GiB / ~3.75 GiB`)** streams DDC writes continuously and keeps `Sensor 2` controller temperatures below the `78°C` throttle cliff.
3. **Linux File-Watcher (`fs.inotify`) & VMA (`vm.max_map_count`) Floors:**
   - Enforces `fs.inotify.max_user_instances >= 2048` (remediating the `128` kernel default that triggers `EMFILE` across `FDirectoryWatcherLinux`, `ZenServer`, Live Coding, and IDE file watchers), `fs.inotify.max_user_watches >= 1,048,576`, `fs.inotify.max_queued_events >= 65,536`, and `vm.max_map_count >= 1,048,576`.
4. **8 GB GDDR6 (`RTX 2070 Max-Q`) Pool Guidance & Preflight Check:**
   - Run `uv run aw-m17-diag preflight --workload unreal` before launching heavy UE 5.8 projects to verify `>= 6 GiB` free GDDR6 VRAM, unclamped `90W` TGP, `>= 24 GiB` available RAM, and `unreal-engine` invariants (`PRE-UE-INV`). For heavy Lumen + Virtual Shadow Map scenes on `8192 MiB` VRAM, setting `r.Streaming.PoolSize=2000` (or `Medium`/`High` scalability while editing) prevents `VK_ERROR_OUT_OF_DEVICE_MEMORY`.

---

## 8. Automatic Startup & S3 Resume Profile Management

You can manage whether a workload profile loads for the current session only or automatically on every boot:

```bash
# Apply a profile for the current session only (does not alter boot startup profile):
sudo $(which uv) run aw-m17-diag profile set gaming-prime
sudo $(which uv) run aw-m17-diag profile set unreal-engine
sudo $(which uv) run aw-m17-diag profile set extreme-performance

# Apply a profile AND set it to load automatically on system startup:
sudo $(which uv) run aw-m17-diag profile set gaming-prime --startup

# Dedicated startup profile management:
uv run aw-m17-diag profile startup --status
sudo $(which uv) run aw-m17-diag profile startup gaming-prime
sudo $(which uv) run aw-m17-diag profile startup --disable
```

