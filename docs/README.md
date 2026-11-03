# `aw-m17-diag` Documentation Index

Technical documentation for the Alienware m17 R1 (`0DG9D5`, BIOS `2.21.1`) diagnostic, workload tuning, and custom DKMS driver suite.

## Guides & References

- **[Thermal, Power & Fan Fine-Tuning Guide (`thermal-and-gpu-tuning.md`)](./thermal-and-gpu-tuning.md)**
  - Shared CPU/GPU copper heatpipe architecture and hardware thermal thresholds (`i7-9750H`, `RTX 2070 Max-Q`, and DRAM-less `Inland QN450 SM2268XT` NVMe).
  - Analysis of the **84°C** gaming load reading vs. GPU Boost 4.0 voltage/frequency step-down (`78°C–80°C`), VBIOS `GPU Target Temp` (`87°C`), `GPU Slowdown Temp` (`94°C`), and `GPU Shutdown Temp` (`99°C`).
  - Detailed breakdown and tradeoffs of the three fine-tuning levers:
    1. **Option 1:** Shared heatpipe CPU RAPL (`PL1`/`PL2`) & `EPP` re-balancing (`50–55W` PL1 / `70–75W` PL2 + `balance_performance`).
    2. **Option 2:** RTX 2070 Max-Q V/F sweet-spotting via `nvidia-smi -pl 80` (`78W–82W` board power cap) or `nvidia-smi -lgc 300,1680` clock ceiling.
    3. **Option 3:** Kernel-space AWCC `custom` (`0x00`) WMI `0x15` activation to unlock manual `fan1_boost` / `fan2_boost` pre-staging and avoid userspace `-EINVAL` (`Errno 22`) and preset `-EBADRQC` (`Errno 56`).
  - Side-by-side **Preset Decision Matrix** (`Baseline` vs. `Preset A: Cool & Consistent`, `Preset B: Max GPU + Shared-Pipe Relief`, and `Preset C: Aggressive Pre-Staged Fans`).
  - Technical appendix on the **Post-S3-Resume 30W / 810 MHz (`P5`) GPU Clamp** (`_PSR=0` race, `.bss+0x1fece0` state cache, and `nvidia_power_unclamp.ko`).
- **[Initial Architecture & Stabilization Plan (`../PLAN.md`)](../PLAN.md)**
  - Root-cause forensics for historical crashes, NVMe APST (`default_ps_max_latency_us`), `ath10k_pci` DMAR faults, and 3-tier mitigation design.
