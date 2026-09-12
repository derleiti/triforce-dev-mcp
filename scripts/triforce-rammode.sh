#!/usr/bin/env bash
# TriForce RAM-Mode Controller v1.0
# Runs TriForce/TriStar Backend completely in RAM for maximum performance

set -euo pipefail

# Configuration
TRISTAR_RAM_DIR="/var/tristar"
PERSIST_DIR="/opt/triforce/persist"
TMPFS_SIZE="256M"
RSYNC_INTERVAL=30
PID_FILE="/var/run/triforce-rammode.pid"
STREAMER_PID_FILE="/var/run/triforce-streamer.pid"
LOG_FILE="/var/log/triforce-rammode.log"
PYTHON_VENV="/home/zombie/triforce/.venv"
APP_DIR="/home/zombie/triforce"
UVICORN_PID_FILE="/var/run/triforce-uvicorn.pid"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log_info() {
    log "${BLUE}[INFO]${NC} $1"
}

log_warn() {
    log "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    log "${RED}[ERROR]${NC} $1"
}

log_success() {
    log "${GREEN}[SUCCESS]${NC} $1"
}

# Check if running as root
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "This script must be run as root"
        exit 1
    fi
}

# Create required directories
setup_directories() {
    log_info "Setting up directory structure..."
    
    # Create persist directory if it doesn't exist
    mkdir -p "$PERSIST_DIR"/{logs/central,memory,prompts,agents,projects,cli-config,models,settings}
    
    # Set permissions
    chown -R zombie:zombie "$PERSIST_DIR" 2>/dev/null || true
    
    log_success "Directories created"
}

# Mount tmpfs for RAM-first operation
mount_tmpfs() {
    log_info "Mounting tmpfs at $TRISTAR_RAM_DIR..."
    
    # Check if already mounted
    if mountpoint -q "$TRISTAR_RAM_DIR"; then
        log_warn "tmpfs already mounted at $TRISTAR_RAM_DIR"
        return 0
    fi
    
    # Create mount point
    mkdir -p "$TRISTAR_RAM_DIR"
    
    # Mount tmpfs
    mount -t tmpfs -o size=$TMPFS_SIZE,mode=755,noatime,nodev,nosuid tmpfs "$TRISTAR_RAM_DIR"
    
    # Set ownership
    chown zombie:zombie "$TRISTAR_RAM_DIR"
    
    log_success "tmpfs mounted successfully"
}

# Copy persistent data to RAM
sync_to_ram() {
    log_info "Syncing persistent data to RAM..."
    
    if [[ -d "$PERSIST_DIR" ]]; then
        # Use rsync for efficient copying
        rsync -av --delete "$PERSIST_DIR/" "$TRISTAR_RAM_DIR/" 2>/dev/null || {
            log_warn "No persistent data found, creating fresh structure"
        }
    fi
    
    # Create required subdirectories in RAM
    mkdir -p "$TRISTAR_RAM_DIR"/{logs/central,memory,prompts,agents,projects,cli-config,models,settings,pids,queue,reports,autoprompts,jobs}
    chown -R zombie:zombie "$TRISTAR_RAM_DIR"
    
    log_success "Data synced to RAM"
}

# Background disk streamer
start_disk_streamer() {
    log_info "Starting background disk streamer..."
    
    # Kill existing streamer if running
    if [[ -f "$STREAMER_PID_FILE" ]]; then
        local old_pid
        old_pid=$(cat "$STREAMER_PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            kill "$old_pid"
            sleep 2
        fi
        rm -f "$STREAMER_PID_FILE"
    fi
    
    # Start background streamer
    (
        while true; do
            sleep "$RSYNC_INTERVAL"
            
            # Sync RAM to disk
            if [[ -d "$TRISTAR_RAM_DIR" && -d "$PERSIST_DIR" ]]; then
                rsync -av --delete "$TRISTAR_RAM_DIR/" "$PERSIST_DIR/" >/dev/null 2>&1
            fi
        done
    ) &
    
    echo $! > "$STREAMER_PID_FILE"
    log_success "Disk streamer started (PID: $!)"
}

# Stop disk streamer
stop_disk_streamer() {
    log_info "Stopping disk streamer..."
    
    if [[ -f "$STREAMER_PID_FILE" ]]; then
        local pid
        pid=$(cat "$STREAMER_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            wait "$pid" 2>/dev/null || true
        fi
        rm -f "$STREAMER_PID_FILE"
        log_success "Disk streamer stopped"
    else
        log_warn "Disk streamer not running"
    fi
}

# Start TriForce backend
start_backend() {
    log_info "Starting TriForce backend with RAM-mode optimizations..."
    
    # Check if already running
    if [[ -f "$UVICORN_PID_FILE" ]]; then
        local pid
        pid=$(cat "$UVICORN_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "TriForce backend already running (PID: $pid)"
            return 0
        fi
        rm -f "$UVICORN_PID_FILE"
    fi
    
    # Change to app directory
    cd "$APP_DIR"
    
    # Start uvicorn with optimizations
    sudo -u zombie bash -c "
        source $PYTHON_VENV/bin/activate
        export TRIFORCE_RAM_MODE=1
        export PYTHONPATH=$APP_DIR
        nohup $PYTHON_VENV/bin/uvicorn app.main:app \\
            --host 0.0.0.0 \\
            --port 9100 \\
            --workers 1 \\
            --loop uvloop \\
            --no-access-log \\
            --log-level warning \\
            >> /var/log/triforce-backend.log 2>&1 &
        echo \$! > $UVICORN_PID_FILE
    "
    
    # Wait a moment and check if it started
    sleep 3
    if [[ -f "$UVICORN_PID_FILE" ]]; then
        local pid
        pid=$(cat "$UVICORN_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log_success "TriForce backend started (PID: $pid)"
        else
            log_error "Failed to start TriForce backend"
            return 1
        fi
    else
        log_error "Failed to create PID file for TriForce backend"
        return 1
    fi
}

# Stop TriForce backend
stop_backend() {
    log_info "Stopping TriForce backend..."
    
    if [[ -f "$UVICORN_PID_FILE" ]]; then
        local pid
        pid=$(cat "$UVICORN_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill -TERM "$pid"
            
            # Wait for graceful shutdown
            local count=0
            while kill -0 "$pid" 2>/dev/null && [[ $count -lt 15 ]]; do
                sleep 1
                ((count++))
            done
            
            # Force kill if still running
            if kill -0 "$pid" 2>/dev/null; then
                log_warn "Force killing backend"
                kill -KILL "$pid"
            fi
        fi
        rm -f "$UVICORN_PID_FILE"
        log_success "TriForce backend stopped"
    else
        log_warn "TriForce backend not running"
    fi
}

# Final sync to disk
final_sync() {
    log_info "Performing final sync to disk..."
    
    if [[ -d "$TRISTAR_RAM_DIR" && -d "$PERSIST_DIR" ]]; then
        # Comprehensive sync
        rsync -av --delete "$TRISTAR_RAM_DIR/" "$PERSIST_DIR/"
        sync
        log_success "Final sync completed"
    else
        log_warn "Cannot perform final sync - directories missing"
    fi
}

# Unmount tmpfs
unmount_tmpfs() {
    log_info "Unmounting tmpfs..."
    
    if mountpoint -q "$TRISTAR_RAM_DIR"; then
        umount "$TRISTAR_RAM_DIR"
        log_success "tmpfs unmounted"
    else
        log_warn "tmpfs not mounted"
    fi
}

# Check status
status() {
    echo -e "${BLUE}TriForce RAM-Mode Status${NC}"
    echo "========================"
    
    # Check tmpfs mount
    if mountpoint -q "$TRISTAR_RAM_DIR"; then
        echo -e "tmpfs: ${GREEN}MOUNTED${NC} ($TMPFS_SIZE)"
        df -h "$TRISTAR_RAM_DIR" | tail -1
    else
        echo -e "tmpfs: ${RED}NOT MOUNTED${NC}"
    fi
    
    echo ""
    
    # Check disk streamer
    if [[ -f "$STREAMER_PID_FILE" ]]; then
        local streamer_pid
        streamer_pid=$(cat "$STREAMER_PID_FILE")
        if kill -0 "$streamer_pid" 2>/dev/null; then
            echo -e "Disk Streamer: ${GREEN}RUNNING${NC} (PID: $streamer_pid)"
        else
            echo -e "Disk Streamer: ${RED}DEAD${NC} (stale PID file)"
            rm -f "$STREAMER_PID_FILE"
        fi
    else
        echo -e "Disk Streamer: ${RED}NOT RUNNING${NC}"
    fi
    
    # Check backend
    if [[ -f "$UVICORN_PID_FILE" ]]; then
        local backend_pid
        backend_pid=$(cat "$UVICORN_PID_FILE")
        if kill -0 "$backend_pid" 2>/dev/null; then
            echo -e "Backend: ${GREEN}RUNNING${NC} (PID: $backend_pid)"
        else
            echo -e "Backend: ${RED}DEAD${NC} (stale PID file)"
            rm -f "$UVICORN_PID_FILE"
        fi
    else
        echo -e "Backend: ${RED}NOT RUNNING${NC}"
    fi
    
    echo ""
    echo "Directories:"
    echo "  RAM: $TRISTAR_RAM_DIR"
    echo "  Persist: $PERSIST_DIR"
    
    # Show RAM usage if mounted
    if mountpoint -q "$TRISTAR_RAM_DIR"; then
        echo ""
        echo "RAM Usage:"
        du -sh "$TRISTAR_RAM_DIR"/* 2>/dev/null || echo "  No data"
    fi
}

# Start everything
start() {
    log_info "Starting TriForce RAM-Mode..."
    echo $$ > "$PID_FILE"
    
    setup_directories
    mount_tmpfs
    sync_to_ram
    start_disk_streamer
    start_backend
    
    log_success "TriForce RAM-Mode started successfully!"
}

# Stop everything
stop() {
    log_info "Stopping TriForce RAM-Mode..."
    
    stop_backend
    stop_disk_streamer
    final_sync
    unmount_tmpfs
    
    # Clean up PID files
    rm -f "$PID_FILE"
    
    log_success "TriForce RAM-Mode stopped successfully!"
}

# Manual sync
sync() {
    log_info "Performing manual sync..."
    
    if [[ -d "$TRISTAR_RAM_DIR" && -d "$PERSIST_DIR" ]]; then
        rsync -av --delete "$TRISTAR_RAM_DIR/" "$PERSIST_DIR/"
        sync
        log_success "Manual sync completed"
    else
        log_error "Cannot sync - directories missing"
        exit 1
    fi
}

# Main script logic
case "${1:-}" in
    start)
        check_root
        start
        ;;
    stop)
        check_root
        stop
        ;;
    restart)
        check_root
        stop
        sleep 2
        start
        ;;
    status)
        status
        ;;
    sync)
        check_root
        sync
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|sync}"
        echo ""
        echo "Commands:"
        echo "  start   - Start TriForce RAM-Mode"
        echo "  stop    - Stop TriForce RAM-Mode with final sync"
        echo "  restart - Restart TriForce RAM-Mode"
        echo "  status  - Show current status"
        echo "  sync    - Manual sync RAM to disk"
        exit 1
        ;;
esac