#!/bin/bash
# TriForce Federation Node Update Script
# Syncs code from Hetzner (hub) to all nodes

set -e

NODES=("zombie@10.10.0.2:/home/zombie/workspace/triforce" "zombie@10.10.0.3:/home/zombie/workspace/triforce")
SOURCE="/home/zombie/workspace/triforce"
LOG="/home/zombie/workspace/triforce/logs/node-updates.log"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

# Files to sync
SYNC_DIRS=(
    "app/routes"
    "app/services"
    "app/mcp"
    "scripts"
)

log "=== Starting node update ==="

for NODE in "${NODES[@]}"; do
    NODE_HOST="${NODE%:*}"
    NODE_PATH="${NODE#*:}"
    NODE_NAME="${NODE_HOST#*@}"
    
    log "Updating $NODE_NAME..."
    
    for DIR in "${SYNC_DIRS[@]}"; do
        if [ -d "$SOURCE/$DIR" ]; then
            rsync -avz --delete "$SOURCE/$DIR/" "$NODE_HOST:$NODE_PATH/$DIR/" 2>/dev/null || {
                log "  Warning: rsync failed for $DIR on $NODE_NAME, trying scp..."
                scp -r "$SOURCE/$DIR/"* "$NODE_HOST:$NODE_PATH/$DIR/" 2>/dev/null || true
            }
        fi
    done
    
    # Sync config (without secrets)
    scp "$SOURCE/config/federation_nodes.json" "$NODE_HOST:$NODE_PATH/config/" 2>/dev/null || true
    
    # Restart service
    ssh "$NODE_HOST" "sudo systemctl restart triforce" 2>/dev/null && \
        log "  $NODE_NAME: restarted" || \
        log "  $NODE_NAME: restart failed"
done

log "=== Update complete ==="

# Verify federation health
sleep 5
HEALTH=$(curl -s http://localhost:9000/v1/federation/status 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{d[\"healthy_count\"]}/{d[\"total_count\"]} healthy')" 2>/dev/null || echo "unknown")
log "Federation status: $HEALTH"
