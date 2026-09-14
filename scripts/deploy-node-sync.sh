#!/bin/bash
# ============================================================================
# Deploy triforce-node-sync auf zombie-pc und backup
# Läuft auf: hetzner (master)
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRIFORCE_DIR="$(dirname "$SCRIPT_DIR")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

deploy_node() {
    local NODE_HOST="$1"
    local NODE_USER="$2"
    local NODE_TRIFORCE_DIR="$3"
    local NODE_LABEL="$4"

    log "=== Deploye auf $NODE_LABEL ($NODE_HOST) ==="

    # 1. Sync-Verzeichnis anlegen
    ssh -o BatchMode=yes -o ConnectTimeout=10 "${NODE_USER}@${NODE_HOST}" \
        "sudo mkdir -p /opt/triforce-sync && sudo chown ${NODE_USER}:${NODE_USER} /opt/triforce-sync"

    # 2. Script kopieren
    scp -o BatchMode=yes -o ConnectTimeout=10 \
        "${SCRIPT_DIR}/node-sync-from-master.sh" \
        "${NODE_USER}@${NODE_HOST}:/opt/triforce-sync/node-sync-from-master.sh"

    ssh -o BatchMode=yes "${NODE_USER}@${NODE_HOST}" \
        "chmod +x /opt/triforce-sync/node-sync-from-master.sh"

    log "  Script deployed: /opt/triforce-sync/node-sync-from-master.sh"

    # 3. Systemd Units kopieren
    scp -o BatchMode=yes \
        "${SCRIPT_DIR}/triforce-node-sync.service" \
        "${NODE_USER}@${NODE_HOST}:/tmp/triforce-node-sync.service"
    scp -o BatchMode=yes \
        "${SCRIPT_DIR}/triforce-node-sync.timer" \
        "${NODE_USER}@${NODE_HOST}:/tmp/triforce-node-sync.timer"

    # 4. Units installieren + aktivieren
    ssh -o BatchMode=yes "${NODE_USER}@${NODE_HOST}" "
        sudo cp /tmp/triforce-node-sync.service /etc/systemd/system/triforce-node-sync.service
        sudo cp /tmp/triforce-node-sync.timer    /etc/systemd/system/triforce-node-sync.timer
        sudo systemctl daemon-reload
        sudo systemctl enable triforce-node-sync.timer
        sudo systemctl start  triforce-node-sync.timer
        echo 'Timer-Status:'
        systemctl status triforce-node-sync.timer --no-pager -l | head -8
    "

    log "  Systemd Units installiert und aktiviert ✓"

    # 5. Einmal sofort ausführen (Initialer Sync-Test)
    log "  Führe initialen Sync aus..."
    ssh -o BatchMode=yes "${NODE_USER}@${NODE_HOST}" \
        "sudo systemctl start triforce-node-sync.service && echo 'Initialer Sync OK'" || \
        log "  WARN: Initialer Sync fehlgeschlagen (Service läuft möglicherweise bereits)"

    log "=== $NODE_LABEL fertig ==="
}

# Deploy auf zombie-pc
deploy_node "10.10.0.2" "zombie"     "/home/zombie/workspace/triforce"     "zombie-pc"

# Deploy auf backup
deploy_node "10.10.0.3" "zombie" "/home/zombie/workspace/triforce" "backup"

log ""
log "=== Deploy abgeschlossen ==="
log "Sync läuft auf beiden Nodes: Bootup +30s, dann alle 5 Minuten"
log "Logs: <TRIFORCE_DIR>/logs/node-sync.log"
log ""
log "Status prüfen mit:"
log "  ssh zombie@10.10.0.2 'systemctl status triforce-node-sync.timer'"
log "  ssh zombie@10.10.0.3 'systemctl status triforce-node-sync.timer'"
