#!/usr/bin/env bash
set -euo pipefail

###############################################
#  🐧 LINUX SYSTEM OPTIMIZER v2.1 (SAFE MODE)
#  Repaired, optimized & extended by Nova AI
#  for Markus @ ailinux.me
###############################################

#--------------------------------------------------
# COLORS
#--------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
WHITE='\033[1;37m'
BOLD='\033[1m'
NC='\033[0m'

CHECK="✅"
WARN="⚠️"
ERROR="❌"
INFO="ℹ️"
ROCKET="🚀"
DISK="💾"
CPU_ICON="🧠"
GPU_ICON="🖼️"
NET_ICON="🌐"

VERSION="2.1"
PROFILE="desktop"
DRY_RUN=false

LOG_FILE="/tmp/systemoptimizer_$(date +%Y%m%d_%H%M%S).log"
BACKUP_DIR="/tmp/sysopt_backup_$(date +%Y%m%d_%H%M%S)"

declare -A CURRENT_SETTINGS
declare -A OPTIMAL_SETTINGS

#--------------------------------------------------
# LOGGING
#--------------------------------------------------
log() { echo -e "$1" | tee -a "$LOG_FILE"; }
success(){ log "${GREEN}${CHECK}${NC} $1"; }
warning(){ log "${YELLOW}${WARN}${NC} $1"; }
error(){ log "${RED}${ERROR}${NC} $1"; }
info(){ log "${CYAN}${INFO}${NC} $1"; }

section_header(){
    echo ""
    log "${PURPLE}══════════════════════════════════════════════════════${NC}"
    log "${PURPLE}${BOLD}$1${NC}"
    log "${PURPLE}══════════════════════════════════════════════════════${NC}"
}

#--------------------------------------------------
# BANNER
#--------------------------------------------------
show_banner() {
echo -e "${CYAN}"
cat << "EOF"
╦  ╦╔╗╔╦ ╦═╗ ╦  ╔═╗╔═╗╔╦╗╦╔╦╗╦╔═╗╔═╗╦═╗
║  ║║║║║ ║╔╩╦╝  ║ ║╠═╝ ║ ║║║║║╔═╝║╣ ╠╦╝
╩═╝╩╝╚╝╚═╝╩ ╚═  ╚═╝╩   ╩ ╩╩ ╩╩╚═╝╚═╝╩╚═

███████╗██╗   ██╗███████╗████████╗
██╔════╝╚██╗ ██╔╝██╔════╝╚══██╔══╝
███████╗ ╚████╔╝ ███████╗   ██║   
╚════██║  ╚██╔╝  ╚════██║   ██║   
███████║   ██║   ███████║   ██║   
╚══════╝   ╚═╝   ╚══════╝   ╚═╝   
EOF
echo -e "${NC}"
echo -e "${WHITE}${BOLD}Version $VERSION — SAFE AUTO-TUNER${NC}"
echo -e "${BLUE}Assembled by Nova AI for Markus${NC}"
echo ""
}

#--------------------------------------------------
# ROOT CHECK
#--------------------------------------------------
check_root(){
    if [[ $EUID -ne 0 ]]; then
        error "Root required."
        exit 1
    fi
}

#--------------------------------------------------
# BACKUP
#--------------------------------------------------
backup_settings(){
    section_header "$DISK BACKUP CURRENT SETTINGS"
    mkdir -p "$BACKUP_DIR"

    sysctl -a > "$BACKUP_DIR/sysctl.conf" 2>/dev/null || true
    cp /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor \
       "$BACKUP_DIR/cpu_governor.txt" 2>/dev/null || true

    success "Backup saved → $BACKUP_DIR"
}

#--------------------------------------------------
# CPU INFO
#--------------------------------------------------
collect_cpu(){
    section_header "$CPU_ICON CPU ANALYSIS"

    local vendor
    vendor=$(grep -m1 vendor_id /proc/cpuinfo | awk -F: '{print $2}' | xargs)
    local model
    model=$(grep -m1 "model name" /proc/cpuinfo | awk -F: '{print $2}' | xargs)


    log "Vendor: ${WHITE}$vendor${NC}"
    log "Model: ${WHITE}$model${NC}"

    if [[ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]]; then
        CURRENT_SETTINGS[cpu_governor]=$(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)
        log "Governor: ${CYAN}${CURRENT_SETTINGS[cpu_governor]}${NC}"
    else
        CURRENT_SETTINGS[cpu_governor]="unknown"
    fi
}

#--------------------------------------------------
# RAM INFO
#--------------------------------------------------
collect_ram(){
    section_header "$DISK RAM ANALYSIS"
    local total
    total=$(grep MemTotal /proc/meminfo | awk '{print $2/1024/1024}')
    log "Total RAM: ${WHITE}${total} GB${NC}"
}

#--------------------------------------------------
# STORAGE INFO
#--------------------------------------------------
collect_storage(){
    section_header "$DISK STORAGE ANALYSIS"

    lsblk -d -o NAME,SIZE,TYPE,MODEL | tee -a "$LOG_FILE"

    for d in /sys/block/*; do
        [[ ! -f "$d/queue/rotational" ]] && continue
        local name
        name=$(basename "$d")
        local rotational
        rotational=$(cat "$d/queue/rotational")
        local type="HDD"
        [[ "$rotational" == "0" ]] && type="SSD"
        [[ "$name" == nvme* ]] && type="NVMe"

        CURRENT_SETTINGS["scheduler_$name"]=$(grep -oP '\[\K[^\]]+' "$d/queue/scheduler" || echo "unknown")

        log "$name → $type, Scheduler: ${WHITE}${CURRENT_SETTINGS["scheduler_$name"]}${NC}"
    done
}

#--------------------------------------------------
# GPU INFO
#--------------------------------------------------
collect_gpu(){
    section_header "$GPU_ICON GPU INFO"

    if lspci | grep -qi 'amd.*vga'; then
        log "AMD GPU detected"
        for card in /sys/class/drm/card*/device/power_dpm_force_performance_level; do
            CURRENT_SETTINGS[gpu_perf_level]=$(cat "$card" 2>/dev/null || echo "auto")
        done
    elif command -v nvidia-smi &>/dev/null; then
        log "NVIDIA GPU detected"
    else
        warning "No GPU found"
    fi
}

#--------------------------------------------------
# NETWORK INFO
#--------------------------------------------------
collect_net(){
    section_header "$NET_ICON NETWORK ANALYSIS"
    CURRENT_SETTINGS[tcp_cc]=$(cat /proc/sys/net/ipv4/tcp_congestion_control)
    log "TCP Congestion Control: ${CYAN}${CURRENT_SETTINGS[tcp_cc]}${NC}"
}

#--------------------------------------------------
# DETERMINE OPTIMAL SETTINGS
#--------------------------------------------------
determine_optimal(){
    section_header "$ROCKET DETERMINE OPTIMAL SETTINGS"

    case "$PROFILE" in
        gaming)     OPTIMAL_SETTINGS[cpu_governor]="performance" ;;
        desktop)    OPTIMAL_SETTINGS[cpu_governor]="schedutil" ;;
        workstation)OPTIMAL_SETTINGS[cpu_governor]="schedutil" ;;
        server)     OPTIMAL_SETTINGS[cpu_governor]="powersave" ;;
    esac

    OPTIMAL_SETTINGS[tcp_cc]="bbr"
}

#--------------------------------------------------
# APPLY OPTIMIZATIONS (SAFE MODE)
#--------------------------------------------------
apply_cpu(){
    log "Applying CPU governor..."

    local want="${OPTIMAL_SETTINGS[cpu_governor]}"
    local have="${CURRENT_SETTINGS[cpu_governor]}"

    if [[ "$want" != "$have" ]]; then
        [[ "$DRY_RUN" == true ]] && {
            info "[DRY RUN] Would set governor ${want}"
            return
        }

        for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
            echo "$want" > "$cpu" 2>/dev/null || true
        done
        success "Governor set → $want"
    else
        info "Governor already optimal"
    fi
}

apply_net(){
    log "Applying Network..."

    if [[ "$DRY_RUN" == true ]]; then
        info "[DRY RUN] Would set TCP CC → bbr"
        return
    fi

    modprobe tcp_bbr 2>/dev/null || true
    sysctl -qw net.ipv4.tcp_congestion_control=bbr

    success "Applied → BBR"
}

apply_all(){
    section_header "$ROCKET APPLYING ALL OPTIMIZATIONS"

    apply_cpu
    apply_net
}

#--------------------------------------------------
# PERSIST SETTINGS
#--------------------------------------------------
make_persistent(){
    section_header "$DISK MAKE PERSISTENT"

    [[ "$DRY_RUN" == true ]] && {
        info "[DRY RUN] Would write /etc/sysctl.d/99-systemoptimizer.conf"
        return
    }

cat > /etc/sysctl.d/99-systemoptimizer.conf << EOF
# Generated by Linux System Optimizer v$VERSION
net.ipv4.tcp_congestion_control = bbr
net.core.default_qdisc = fq
EOF

    success "Sysctl written"
}

#--------------------------------------------------
# MAIN
#--------------------------------------------------
main(){

    # argument parsing
    while [[ $# -gt 0 ]]; do
        case $1 in
            -p|--profile) PROFILE="$2"; shift 2;;
            -d|--dry-run) DRY_RUN=true; shift;;
            -b|--backup) backup_settings; shift;;
            *) shift;;
        esac
    done

    clear
    show_banner
    check_root

    collect_cpu
    collect_ram
    collect_storage
    collect_gpu
    collect_net

    determine_optimal
    apply_all
    make_persistent

    success "All done!"
    log "Logfile: $LOG_FILE"
}

main "$@"
