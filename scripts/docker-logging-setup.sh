#!/usr/bin/env bash
# ============================================================================
# Docker Log Collector für TriStar Central Logging
# ============================================================================
# Sammelt Logs aus allen Docker-Containern nach /var/tristar/logs/docker/
#
# Usage:
#   ./docker-log-setup.sh install   # Einmalig einrichten
#   ./docker-log-setup.sh status    # Status prüfen
#   ./docker-log-setup.sh tail      # Live alle Logs
# ============================================================================

LOG_BASE="/var/tristar/logs/docker"
SCRIPT_PATH="/usr/local/bin/docker-log-sync.sh"
SERVICE_PATH="/etc/systemd/system/docker-log-sync.service"

# Bekannte Docker-Stacks
STACKS=(
    "wordpress:${TRIFORCE_DIR:-/home/zombie/triforce}/docker/wordpress"
    "ailinux-repo:${TRIFORCE_DIR:-/home/zombie/triforce}/docker/repository"
    "mailserver:${TRIFORCE_DIR:-/home/zombie/triforce}/docker/mailserver"
)

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================================
# Install: Verzeichnisse + Symlinks + Service
# ============================================================================
install() {
    log_info "Erstelle Log-Verzeichnisse..."
    
    # Hauptverzeichnis
    mkdir -p "$LOG_BASE"
    
    # Unterverzeichnisse pro Stack
    for stack in "${STACKS[@]}"; do
        name="${stack%%:*}"
        mkdir -p "$LOG_BASE/$name"
        log_info "  → $LOG_BASE/$name/"
    done
    
    # Live-Collector Script erstellen
    log_info "Erstelle Log-Collector Script..."
    
    cat > "$SCRIPT_PATH" << 'COLLECTOR'
#!/bin/bash
# Docker Log Sync - läuft als Service

LOG_BASE="/var/tristar/logs/docker"

sync_container_logs() {
    local container="$1"
    local stack="$2"
    local log_file="$LOG_BASE/$stack/${container}.log"
    
    # Docker logs seit letztem Sync holen (letzte Zeile als Marker)
    docker logs --since 1m "$container" >> "$log_file" 2>&1
}

# Hauptloop
while true; do
    for container in $(docker ps --format '{{.Names}}'); do
        # Stack ermitteln aus Container-Name oder Label
        if [[ "$container" == *wordpress* ]] || [[ "$container" == *mysql* ]] || [[ "$container" == *mariadb* ]]; then
            stack="wordpress"
        elif [[ "$container" == *mail* ]] || [[ "$container" == *postfix* ]] || [[ "$container" == *dovecot* ]]; then
            stack="mailserver"
        elif [[ "$container" == *repo* ]] || [[ "$container" == *nginx* ]] || [[ "$container" == *apt* ]]; then
            stack="ailinux-repo"
        else
            stack="misc"
            mkdir -p "$LOG_BASE/misc"
        fi
        
        sync_container_logs "$container" "$stack"
    done
    
    sleep 60  # Jede Minute syncen
done
COLLECTOR
    
    chmod +x "$SCRIPT_PATH"
    
    # Systemd Service erstellen
    log_info "Erstelle Systemd Service..."
    
    cat > "$SERVICE_PATH" << SERVICE
[Unit]
Description=Docker Log Sync für TriStar
After=docker.service
Requires=docker.service

[Service]
Type=simple
ExecStart=$SCRIPT_PATH
Restart=always
RestartSec=10
User=root

[Install]
WantedBy=multi-user.target
SERVICE
    
    # Service aktivieren
    systemctl daemon-reload
    systemctl enable docker-log-sync
    systemctl start docker-log-sync
    
    log_info "✅ Installation abgeschlossen!"
    log_info ""
    log_info "Log-Verzeichnis: $LOG_BASE"
    log_info "Service Status:  systemctl status docker-log-sync"
    
    # Initial: Aktuelle Container-Logs holen
    log_info ""
    log_info "Hole initiale Logs von laufenden Containern..."
    
    for container in $(docker ps --format '{{.Names}}'); do
        log_info "  → $container"
    done
}

# ============================================================================
# Status: Übersicht
# ============================================================================
status() {
    echo "═══════════════════════════════════════════════════════════════"
    echo " Docker Log Collector Status"
    echo "═══════════════════════════════════════════════════════════════"
    echo ""
    
    # Service Status
    if systemctl is-active --quiet docker-log-sync; then
        log_info "Service: ${GREEN}running${NC}"
    else
        log_warn "Service: ${RED}stopped${NC}"
    fi
    echo ""
    
    # Laufende Container
    echo "Laufende Container:"
    docker ps --format "  {{.Names}}\t{{.Status}}" 2>/dev/null || echo "  (Docker nicht erreichbar)"
    echo ""
    
    # Log-Dateien
    echo "Log-Dateien in $LOG_BASE:"
    if [ -d "$LOG_BASE" ]; then
        find "$LOG_BASE" -name "*.log" -exec ls -lh {} \; 2>/dev/null | awk '{print "  " $NF " (" $5 ")"}'
    else
        echo "  (Verzeichnis existiert nicht)"
    fi
    echo ""
    
    # Disk Usage
    echo "Speicherverbrauch:"
    du -sh "$LOG_BASE" 2>/dev/null || echo "  n/a"
}

# ============================================================================
# Tail: Live alle Logs
# ============================================================================
tail_logs() {
    log_info "Live-Tail aller Docker Logs (Ctrl+C zum Beenden)..."
    echo ""
    
    # Alle Container parallel tailing
    for container in $(docker ps --format '{{.Names}}'); do
        docker logs -f --tail 10 "$container" 2>&1 | sed "s/^/[$container] /" &
    done
    
    wait
}

# ============================================================================
# Alternative: Direkte Symlinks zu Docker JSON Logs
# ============================================================================
create_symlinks() {
    log_info "Erstelle Symlinks zu Docker JSON Logs..."
    
    mkdir -p "$LOG_BASE/raw"
    
    for container in $(docker ps -q); do
        name=$(docker inspect --format '{{.Name}}' "$container" | sed 's/^\///')
        log_path=$(docker inspect --format '{{.LogPath}}' "$container")
        
        if [ -n "$log_path" ] && [ -f "$log_path" ]; then
            ln -sf "$log_path" "$LOG_BASE/raw/${name}.json"
            log_info "  → $name"
        fi
    done
    
    log_info "Symlinks erstellt in $LOG_BASE/raw/"
    log_info "Hinweis: JSON-Format, mit 'jq' parsen"
}

# ============================================================================
# Main
# ============================================================================
case "${1:-status}" in
    install)
        install
        ;;
    status)
        status
        ;;
    tail)
        tail_logs
        ;;
    symlinks)
        create_symlinks
        ;;
    *)
        echo "Usage: $0 {install|status|tail|symlinks}"
        exit 1
        ;;
esac
