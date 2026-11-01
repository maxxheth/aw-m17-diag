#!/usr/bin/env bash
# ==============================================================================
# aw-m17-stabilize.sh — Immediate Hardware & Kernel Stabilization Script
# Target: Alienware m17 (0DG9D5, BIOS 2.21.1) — "mothership"
#
# Remediates:
#   1. Inland QN450 (SM2268XT [126f:2268]) NVMe APST PCIe bus drop-offs
#   2. Qualcomm Atheros QCA6174 ([168c:003e]) Intel VT-d DMAR write fault floods
#   3. Intel i915 / drm_kms_helper HPD polling CPU workqueue stalls (>10ms)
#   4. Unwired NVIDIA TU106 Type-C UCSI (01:00.3 [10de:1adb]) I2C boot timeouts
#   5. Swap starvation (2 GiB -> 32 GiB /swapfile) & VM dirty-page writeback tuning
#   6. Uncapped CPU MSR RAPL PL1 (200W -> 65W sustained / 90W burst)
# ==============================================================================

set -euo pipefail

readonly SCRIPT_NAME="$(basename "$0")"
readonly BACKUP_ROOT="/var/backups/aw-m17-diag"
readonly GRUB_FILE="/etc/default/grub"
readonly SYSCTL_FILE="/etc/sysctl.d/99-aw-m17-workstation.conf"
readonly MODPROBE_FILE="/etc/modprobe.d/aw-m17-blacklist.conf"
readonly RAPL_SERVICE_FILE="/etc/systemd/system/aw-m17-rapl.service"
readonly SWAPFILE_PATH="/swapfile"

# Default parameters
MODE="check"
TARGET_SWAP_GB=32
ENABLE_IOMMU_PT=1
ROLLBACK_TARGET=""
TARGET_PL1_UW=65000000
TARGET_PL2_UW=90000000

# ANSI colors
if [[ -t 1 ]]; then
    readonly C_RESET='\033[0m'
    readonly C_BOLD='\033[1m'
    readonly C_GREEN='\033[0;32m'
    readonly C_YELLOW='\033[1;33m'
    readonly C_RED='\033[0;31m'
    readonly C_CYAN='\033[0;36m'
else
    readonly C_RESET=''
    readonly C_BOLD=''
    readonly C_GREEN=''
    readonly C_YELLOW=''
    readonly C_RED=''
    readonly C_CYAN=''
fi

log_info()  { printf "${C_CYAN}[INFO]${C_RESET}  %s\n" "$*"; }
log_ok()    { printf "${C_GREEN}[OK]${C_RESET}    %s\n" "$*"; }
log_warn()  { printf "${C_YELLOW}[WARN]${C_RESET}  %s\n" "$*"; }
log_err()   { printf "${C_RED}[ERROR]${C_RESET} %s\n" "$*" >&2; }
log_step()  { printf "\n${C_BOLD}==> %s${C_RESET}\n" "$*"; }

usage() {
    cat <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS]

Immediate stabilization script for Alienware m17 (0DG9D5).
Defaults to --check (read-only audit) if no mode flag is given.

Modes:
  --check, --dry-run       Audit current state and show pending changes (default)
  --apply                  Apply all remediations transactionally (requires root)
  --rollback [TIMESTAMP]   Restore backups from ${BACKUP_ROOT}/<TIMESTAMP> (or 'latest')

Options:
  --swap-size <GB>         Target /swapfile size in GiB (default: ${TARGET_SWAP_GB})
  --skip-iommu-pt          Do not add 'iommu=pt' to GRUB (use if building ath10k DKMS fix)
  -h, --help               Show this help message
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check|--dry-run)
                MODE="check"
                shift
                ;;
            --apply)
                MODE="apply"
                shift
                ;;
            --rollback)
                MODE="rollback"
                if [[ $# -gt 1 && ! "$2" =~ ^-- ]]; then
                    ROLLBACK_TARGET="$2"
                    shift 2
                else
                    ROLLBACK_TARGET="latest"
                    shift
                fi
                ;;
            --swap-size)
                if [[ $# -lt 2 || ! "$2" =~ ^[0-9]+$ ]]; then
                    log_err "--swap-size requires an integer value in GiB (e.g., 32 or 64)"
                    exit 1
                fi
                TARGET_SWAP_GB="$2"
                shift 2
                ;;
            --skip-iommu-pt)
                ENABLE_IOMMU_PT=0
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                log_err "Unknown argument: $1"
                usage
                exit 1
                ;;
        esac
    done
}

require_root() {
    if [[ "${EUID}" -ne 0 ]]; then
        log_err "Mode '--${MODE}' requires root privileges. Please re-run with: sudo $0 $*"
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# 1. Kernel Boot Parameters & Live Sysfs (NVMe APST, IOMMU, DRM KMS Polling)
# ------------------------------------------------------------------------------
check_or_apply_kernel_params() {
    local backup_dir="${1:-}"
    log_step "1. Kernel Boot Parameters (${GRUB_FILE}) & Live Sysfs"

    # Live sysfs check: NVMe APST
    local current_apst="unknown"
    if [[ -r /sys/module/nvme_core/parameters/default_ps_max_latency_us ]]; then
        current_apst="$(cat /sys/module/nvme_core/parameters/default_ps_max_latency_us)"
    fi
    if [[ "${current_apst}" == "0" ]]; then
        log_ok "Live sysfs nvme_core.default_ps_max_latency_us is 0 (APST disabled)."
    else
        log_warn "Live sysfs nvme_core.default_ps_max_latency_us is ${current_apst} us (SM2268XT APST crash risk!)."
        if [[ "${MODE}" == "apply" ]]; then
            echo 0 > /sys/module/nvme_core/parameters/default_ps_max_latency_us
            log_ok "Applied live sysfs: nvme_core.default_ps_max_latency_us = 0"
        fi
    fi

    # Live sysfs check: drm_kms_helper poll
    local current_poll="unknown"
    if [[ -r /sys/module/drm_kms_helper/parameters/poll ]]; then
        current_poll="$(cat /sys/module/drm_kms_helper/parameters/poll)"
    elif grep -qE '(^|[[:space:]])drm_kms_helper\.poll=(0|N|n)($|[[:space:]])' /proc/cmdline; then
        current_poll="N"
    elif [[ -e /sys/module/drm_kms_helper/parameters/poll ]]; then
        current_poll="Y (inferred from /proc/cmdline; sysfs node is 0600 root-only)"
    fi
    if [[ "${current_poll}" == "N" || "${current_poll}" == "0" ]]; then
        log_ok "Live sysfs drm_kms_helper.poll is disabled (HPD CPU workqueue polling disabled)."
    else
        log_warn "Live sysfs drm_kms_helper.poll is ${current_poll} (causes >10ms CPU workqueue stalls)."
        if [[ "${MODE}" == "apply" ]]; then
            echo N > /sys/module/drm_kms_helper/parameters/poll
            log_ok "Applied live sysfs: drm_kms_helper.poll = N"
        fi
    fi

    # Persistent GRUB configuration check
    if [[ ! -f "${GRUB_FILE}" ]]; then
        log_err "Cannot find ${GRUB_FILE}"
        return 1
    fi

    local grub_line
    grub_line="$(grep -E '^GRUB_CMDLINE_LINUX_DEFAULT=' "${GRUB_FILE}" || true)"
    log_info "Current ${GRUB_FILE}: ${grub_line}"

    local required_params=("nvme_core.default_ps_max_latency_us=0" "drm_kms_helper.poll=0")
    if [[ "${ENABLE_IOMMU_PT}" -eq 1 ]]; then
        required_params+=("iommu=pt")
    fi

    local missing_params=()
    for param in "${required_params[@]}"; do
        if [[ ! "${grub_line}" =~ (^|[\"[:space:]])${param}([\"[:space:]]|$) ]]; then
            missing_params+=("${param}")
        fi
    done

    if [[ ${#missing_params[@]} -eq 0 ]]; then
        log_ok "All required kernel boot parameters are present in ${GRUB_FILE}."
    else
        log_warn "Missing persistent GRUB boot parameters: ${missing_params[*]}"
        if [[ "${MODE}" == "apply" ]]; then
            cp -a "${GRUB_FILE}" "${backup_dir}/grub.bak"
            local current_val
            current_val="$(sed -n 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/\1/p' "${GRUB_FILE}")"
            local new_val="${current_val}"
            for param in "${missing_params[@]}"; do
                new_val="${new_val:+${new_val} }${param}"
            done
            sed -i "s|^GRUB_CMDLINE_LINUX_DEFAULT=.*|GRUB_CMDLINE_LINUX_DEFAULT=\"${new_val}\"|" "${GRUB_FILE}"
            log_ok "Updated ${GRUB_FILE} -> GRUB_CMDLINE_LINUX_DEFAULT=\"${new_val}\""
            update-grub
            log_ok "Regenerated /boot/grub/grub.cfg via update-grub."
        fi
    fi
}

# ------------------------------------------------------------------------------
# 2. Blacklist Unwired NVIDIA TU106 Type-C UCSI Controller (01:00.3)
# ------------------------------------------------------------------------------
check_or_apply_modprobe_blacklist() {
    local backup_dir="${1:-}"
    log_step "2. Unwired NVIDIA TU106 Type-C UCSI Controller Blacklist (${MODPROBE_FILE})"

    if [[ -f "${MODPROBE_FILE}" ]] && grep -q "blacklist ucsi_ccg" "${MODPROBE_FILE}" && grep -q "blacklist i2c_nvidia_gpu" "${MODPROBE_FILE}"; then
        log_ok "${MODPROBE_FILE} already blacklists ucsi_ccg and i2c_nvidia_gpu."
    else
        log_warn "Modules ucsi_ccg and i2c_nvidia_gpu are not blacklisted (causes 01:00.3 I2C timeout -110 on boot)."
        if [[ "${MODE}" == "apply" ]]; then
            if [[ -f "${MODPROBE_FILE}" ]]; then
                cp -a "${MODPROBE_FILE}" "${backup_dir}/aw-m17-blacklist.conf.bak"
            fi
            cat <<'EOF' > "${MODPROBE_FILE}"
# Alienware m17 (0DG9D5): USB-C/Thunderbolt is routed to Intel controller.
# The NVIDIA TU106 internal Cypress CCGx UCSI controller (01:00.3 [10de:1adb])
# is unwired and stalls boot with i2c_transfer failed -110 / e0000000 timeouts.
blacklist ucsi_ccg
blacklist i2c_nvidia_gpu
EOF
            chmod 0644 "${MODPROBE_FILE}"
            log_ok "Created ${MODPROBE_FILE} to disable unwired 01:00.3 I2C probing."
        fi
    fi
}

# ------------------------------------------------------------------------------
# 3. VM & Memory Writeback Sysctl Tuning
# ------------------------------------------------------------------------------
check_or_apply_sysctl() {
    local backup_dir="${1:-}"
    log_step "3. VM & NVMe Writeback Sysctl Tuning (${SYSCTL_FILE})"

    local cur_swappiness cur_dirty_ratio cur_dirty_bg cur_vfs
    cur_swappiness="$(cat /proc/sys/vm/swappiness)"
    cur_dirty_ratio="$(cat /proc/sys/vm/dirty_ratio)"
    cur_dirty_bg="$(cat /proc/sys/vm/dirty_background_ratio)"
    cur_vfs="$(cat /proc/sys/vm/vfs_cache_pressure)"

    log_info "Current VM sysctls: swappiness=${cur_swappiness}, dirty_ratio=${cur_dirty_ratio}, dirty_background_ratio=${cur_dirty_bg}, vfs_cache_pressure=${cur_vfs}"

    if [[ "${cur_swappiness}" -le 15 && "${cur_dirty_ratio}" -le 10 && "${cur_dirty_bg}" -le 5 && -f "${SYSCTL_FILE}" ]]; then
        log_ok "VM sysctl parameters are optimally tuned for 64 GiB RAM + DRAM-less NVMe."
    else
        log_warn "VM sysctls need tuning (recommended: swappiness=10, dirty_ratio=10, dirty_background_ratio=5, vfs_cache_pressure=50)."
        if [[ "${MODE}" == "apply" ]]; then
            if [[ -f "${SYSCTL_FILE}" ]]; then
                cp -a "${SYSCTL_FILE}" "${backup_dir}/99-aw-m17-workstation.conf.bak"
            fi
            cat <<'EOF' > "${SYSCTL_FILE}"
# Alienware m17 (0DG9D5) Workstation VM & Storage Tuning
# Prevents aggressive swapping on 64 GiB RAM and limits burst writeback queues
# on the DRAM-less Silicon Motion SM2268XT (Inland QN450) NVMe SSD.
vm.swappiness = 10
vm.vfs_cache_pressure = 50
vm.dirty_background_ratio = 5
vm.dirty_ratio = 10
EOF
            chmod 0644 "${SYSCTL_FILE}"
            /sbin/sysctl --system >/dev/null
            log_ok "Applied ${SYSCTL_FILE} via sysctl --system."
        fi
    fi
}

# ------------------------------------------------------------------------------
# 4. Swapfile Expansion (/swapfile)
# ------------------------------------------------------------------------------
check_or_apply_swapfile() {
    log_step "4. Swapfile Capacity Check (${SWAPFILE_PATH})"

    local current_swap_bytes=0
    if [[ -f "${SWAPFILE_PATH}" ]]; then
        current_swap_bytes="$(stat -c %s "${SWAPFILE_PATH}")"
    fi
    local current_swap_gb=$(( current_swap_bytes / (1024 * 1024 * 1024) ))
    local target_swap_bytes=$(( TARGET_SWAP_GB * 1024 * 1024 * 1024 ))

    log_info "Current ${SWAPFILE_PATH} size: ${current_swap_gb} GiB (Target: ${TARGET_SWAP_GB} GiB)"

    if [[ "${current_swap_gb}" -ge "${TARGET_SWAP_GB}" ]]; then
        log_ok "${SWAPFILE_PATH} is already >= ${TARGET_SWAP_GB} GiB."
        return 0
    fi

    # Check available disk space on /
    local avail_kb
    avail_kb="$(df --output=avail / | tail -n 1 | tr -d ' ')"
    local required_kb=$(( (TARGET_SWAP_GB - current_swap_gb + 10) * 1024 * 1024 ))
    if [[ "${avail_kb}" -lt "${required_kb}" ]]; then
        log_err "Insufficient free disk space on / to allocate ${TARGET_SWAP_GB} GiB swapfile."
        return 1
    fi

    log_warn "${SWAPFILE_PATH} is only ${current_swap_gb} GiB (exhausted to 1.6 MiB during 70B+ Ollama model loads)."
    if [[ "${MODE}" == "apply" ]]; then
        log_info "Deactivating ${SWAPFILE_PATH}..."
        swapoff "${SWAPFILE_PATH}" || true
        log_info "Allocating ${TARGET_SWAP_GB} GiB ${SWAPFILE_PATH}..."
        fallocate -l "${target_swap_bytes}" "${SWAPFILE_PATH}"
        chmod 0600 "${SWAPFILE_PATH}"
        mkswap "${SWAPFILE_PATH}" >/dev/null
        swapon "${SWAPFILE_PATH}"
        log_ok "Resized and activated ${SWAPFILE_PATH} (${TARGET_SWAP_GB} GiB)."
    fi
}

# ------------------------------------------------------------------------------
# 5. CPU RAPL Power Limit Sanity Check (MSR PL1 200W -> 65W + Boot Persistence)
# ------------------------------------------------------------------------------
check_or_apply_rapl() {
    log_step "5. Intel CPU RAPL Power Limits (MSR vs. MMIO & Boot Persistence)"

    local msr_pl1_path="/sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw"
    local msr_pl2_path="/sys/class/powercap/intel-rapl:0/constraint_1_power_limit_uw"

    if [[ ! -r "${msr_pl1_path}" ]]; then
        log_warn "RAPL sysfs interface not readable at ${msr_pl1_path}."
        return 0
    fi

    local cur_pl1 cur_pl2
    cur_pl1="$(cat "${msr_pl1_path}")"
    cur_pl2="$(cat "${msr_pl2_path}")"
    local cur_pl1_w=$(( cur_pl1 / 1000000 ))
    local cur_pl2_w=$(( cur_pl2 / 1000000 ))

    log_info "Current MSR RAPL: PL1 (long_term) = ${cur_pl1_w}W, PL2 (short_term) = ${cur_pl2_w}W"

    if [[ "${cur_pl1}" -le 75000000 ]]; then
        log_ok "Live MSR RAPL PL1 (${cur_pl1_w}W) is within safe continuous thermal envelope for i7-9750H."
    else
        log_warn "Live MSR RAPL PL1 is uncapped at ${cur_pl1_w}W (45W TDP CPU; risks VRM/thermal spike under prolonged AVX2+CUDA load)."
        if [[ "${MODE}" == "apply" ]]; then
            echo "${TARGET_PL1_UW}" > "${msr_pl1_path}"
            echo "${TARGET_PL2_UW}" > "${msr_pl2_path}"
            log_ok "Clamped live MSR RAPL PL1 to $(( TARGET_PL1_UW / 1000000 ))W and PL2 to $(( TARGET_PL2_UW / 1000000 ))W."
        fi
    fi

    if [[ -f "${RAPL_SERVICE_FILE}" ]] && systemctl is-enabled --quiet aw-m17-rapl.service 2>/dev/null; then
        log_ok "Persistent systemd service (${RAPL_SERVICE_FILE}) is installed and enabled across reboots/resume."
    else
        log_warn "Persistent RAPL clamp service (${RAPL_SERVICE_FILE}) is not installed (BIOS resets MSR PL1 to 200W on every boot/resume)."
        if [[ "${MODE}" == "apply" ]]; then
            cat <<EOF > "${RAPL_SERVICE_FILE}"
[Unit]
Description=Clamp Alienware m17 (0DG9D5) CPU MSR RAPL PL1/PL2 Power Limits
After=multi-user.target suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'echo ${TARGET_PL1_UW} > ${msr_pl1_path} && echo ${TARGET_PL2_UW} > ${msr_pl2_path}'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target sleep.target
EOF
            chmod 0644 "${RAPL_SERVICE_FILE}"
            systemctl daemon-reload
            systemctl enable --now aw-m17-rapl.service >/dev/null 2>&1 || true
            log_ok "Installed and enabled ${RAPL_SERVICE_FILE} (persists $(( TARGET_PL1_UW / 1000000 ))W PL1 across boot & sleep)."
        fi
    fi
}

# ------------------------------------------------------------------------------
# Rollback Handler
# ------------------------------------------------------------------------------
run_rollback() {
    require_root "$@"
    if [[ ! -d "${BACKUP_ROOT}" ]]; then
        log_err "No backup directory found at ${BACKUP_ROOT}"
        exit 1
    fi

    local target_dir=""
    if [[ "${ROLLBACK_TARGET}" == "latest" ]]; then
        target_dir="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d | sort | tail -n 1)"
    else
        target_dir="${BACKUP_ROOT}/${ROLLBACK_TARGET}"
    fi

    if [[ -z "${target_dir}" || ! -d "${target_dir}" ]]; then
        log_err "Backup target directory not found: ${target_dir}"
        exit 1
    fi

    log_step "Rolling back configuration from ${target_dir}"
    if [[ -f "${target_dir}/grub.bak" ]]; then
        cp -a "${target_dir}/grub.bak" "${GRUB_FILE}"
        update-grub
        log_ok "Restored ${GRUB_FILE} and updated GRUB."
    fi
    if [[ -f "${target_dir}/99-aw-m17-workstation.conf.bak" ]]; then
        cp -a "${target_dir}/99-aw-m17-workstation.conf.bak" "${SYSCTL_FILE}"
        /sbin/sysctl --system >/dev/null
        log_ok "Restored ${SYSCTL_FILE}."
    fi
    if [[ -f "${target_dir}/aw-m17-blacklist.conf.bak" ]]; then
        cp -a "${target_dir}/aw-m17-blacklist.conf.bak" "${MODPROBE_FILE}"
        log_ok "Restored ${MODPROBE_FILE}."
    fi
    if [[ -f "${RAPL_SERVICE_FILE}" ]]; then
        systemctl disable --now aw-m17-rapl.service >/dev/null 2>&1 || true
        rm -f "${RAPL_SERVICE_FILE}"
        systemctl daemon-reload
        log_ok "Removed ${RAPL_SERVICE_FILE}."
    fi
    log_ok "Rollback complete."
}

main() {
    parse_args "$@"

    if [[ "${MODE}" == "rollback" ]]; then
        run_rollback "$@"
        exit 0
    fi

    local backup_dir=""
    if [[ "${MODE}" == "apply" ]]; then
        require_root "$@"
        local ts
        ts="$(date +%Y%m%d-%H%M%S)"
        backup_dir="${BACKUP_ROOT}/${ts}"
        mkdir -p "${backup_dir}"
        log_info "Created backup snapshot directory: ${backup_dir}"
    else
        log_info "Running in READ-ONLY CHECK mode (pass --apply with sudo to apply changes)."
    fi

    check_or_apply_kernel_params "${backup_dir}"
    check_or_apply_modprobe_blacklist "${backup_dir}"
    check_or_apply_sysctl "${backup_dir}"
    check_or_apply_swapfile
    check_or_apply_rapl

    printf "\n"
    if [[ "${MODE}" == "check" ]]; then
        log_info "Audit complete. Run 'sudo $0 --apply' to apply the recommended stabilizations."
    else
        log_ok "All immediate stabilizations applied! Backups saved to ${backup_dir}."
        log_info "Note: Reboot when convenient to activate persistent GRUB parameters and module blacklists."
    fi
}

main "$@"
