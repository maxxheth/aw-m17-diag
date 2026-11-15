# `aw-m17-diag` — Alienware m17 (`0DG9D5`) Diagnostic, Tuning & Driver Advisory Suite

Purpose-built hardware diagnostic, crash forensics, workload tuning, and custom DKMS driver advisory utility for the Alienware m17 (`mothership`, BIOS `2.21.1`, i7-9750H, RTX 2070 Max-Q, Inland QN450 SM2268XT NVMe, Qualcomm QCA6174 Wi-Fi).

## Quickstart

### 1. Immediate Bash Stabilization Script
```bash
# Audit current state (read-only)
/var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --check

# Apply all immediate fixes with automatic backup to /var/backups/aw-m17-diag/
sudo /var/www/aw-m17-diag/scripts/aw-m17-stabilize.sh --apply
```

### 2. Python Diagnostic & Tuning CLI (`uv`)
```bash
cd /var/www/aw-m17-diag

# 360-degree hardware, firmware & minefield audit
uv run aw-m17-diag audit

# Analyze previous boots for abrupt freezes, NULL-byte log cutoffs, OOM/SIGKILLs & DMAR bursts
uv run aw-m17-diag crash-report --boots 5

# Pre-flight readiness check before running heavy Ollama models, Steam gaming, or Unreal Engine 5
uv run aw-m17-diag preflight --workload ollama --model-size-gb 22
uv run aw-m17-diag preflight --workload unreal

# Evaluate Tier 1 (Sysfs) vs Tier 2 (Boot Param) vs Tier 3 (Custom DKMS Driver) solutions
uv run aw-m17-diag driver-advisor --export-dkms

# View or apply workload profiles (extreme-performance, unreal-engine, ollama-heavy, gaming-prime, balanced-daily, powersave)
uv run aw-m17-diag profile show
sudo $(which uv) run aw-m17-diag profile set gaming-prime
sudo $(which uv) run aw-m17-diag profile set gaming-prime --refresh-steam  # also recycles stale Steam Flatpak GL mounts
sudo $(which uv) run aw-m17-diag profile set unreal-engine

# Recycle a stale Steam Flatpak sandbox (fixes "Non-DX12 Video Card" after background flatpak GL updates)
uv run aw-m17-diag refresh-steam

# Configure or inspect the profile loaded automatically on system startup
uv run aw-m17-diag profile startup --status
sudo $(which uv) run aw-m17-diag profile startup gaming-prime
sudo $(which uv) run aw-m17-diag profile startup --disable

# Live real-time hardware & minefield telemetry dashboard
uv run aw-m17-diag watch
```
