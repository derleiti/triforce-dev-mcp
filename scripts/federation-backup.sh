#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# FEDERATION BACKUP — Auto-detect & rsync to Hetzner StorageBox
# Runs on ALL nodes, auto-detects content to backup
# Schedule: every 2 hours via systemd timer
# ═══════════════════════════════════════════════════════════════
set -uo pipefail

NODE_ID="${FEDERATION_NODE_ID:-$(hostname -s)}"
BACKUP_BASE="/mnt/storagebox/federation-backups/${NODE_ID}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOG="/tmp/federation-backup-${TIMESTAMP}.log"
ERRORS=0

log() { echo "[BACKUP $(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== ${NODE_ID} backup started ==="

# ── 1. Check mount ──────────────────────────────────────────
if ! mountpoint -q /mnt/storagebox 2>/dev/null; then
    log "StorageBox not mounted, attempting mount..."
    sudo mount /mnt/storagebox 2>/dev/null || { log "FATAL: mount failed"; exit 1; }
fi

mkdir -p "${BACKUP_BASE}"

# ── 2. Standard excludes ────────────────────────────────────
EXCLUDES=(
    --exclude='.venv/' --exclude='__pycache__/' --exclude='*.pyc'
    --exclude='.git/' --exclude='node_modules/' --exclude='*.log'
    --exclude='*.sock' --exclude='.cache/' --exclude='.npm/'
    --exclude='*.iso' --exclude='*.img' --exclude='*.qcow2'
    --exclude='.local/share/Trash/' --exclude='.local/share/Steam/'
    --exclude='.steam/' --exclude='.wine/' --exclude='snap/'
    --exclude='.mozilla/' --exclude='.config/chromium/' --exclude='.thunderbird/'
    --exclude='Downloads/' --exclude='.local/share/lutris/'
)

# ── 3. Auto-detect & sync content ──────────────────────────
do_sync() {
    local label="$1" src="$2" dest="$3"
    shift 3
    local extra=("$@")
    if [ ! -d "$src" ]; then return; fi
    log "Syncing ${label} ..."
    mkdir -p "$dest"
    rsync -a --delete --no-links "${EXCLUDES[@]}" "${extra[@]}" \
        "$src" "$dest" 2>&1 | tail -5 | tee -a "$LOG" || { log "WARN: ${label} had errors"; ERRORS=$((ERRORS+1)); }
}

# ── triforce backend (all nodes) ────────────────────────────
do_sync "triforce" "/home/zombie/workspace/triforce/" "${BACKUP_BASE}/triforce/" \
    --exclude='docker/' --exclude='html/' --exclude='logs/'

# ── home dir (configs, ssh, scripts) ────────────────────────
do_sync "home" "/home/zombie/" "${BACKUP_BASE}/home/" \
    --exclude='triforce/' --exclude='*.iso' --exclude='*.img'

# ── Docker volumes (auto-detect) ────────────────────────────
if [ -d "/home/zombie/workspace/triforce/docker" ]; then
    # WordPress HTML
    do_sync "wp-html" "/home/zombie/workspace/triforce/docker/wordpress/html/" "${BACKUP_BASE}/wp-html/" \
        --exclude='wp-content/cache/' --exclude='wp-content/wp-cloudflare-super-page-cache/' \
        --exclude='wp-content/uploads/wpo/' --exclude='wp-content/debug.log'

    # Apache vhosts
    do_sync "apache-vhosts" "/home/zombie/workspace/triforce/docker/wordpress/apache/" "${BACKUP_BASE}/apache/"

    # Docker compose files
    for dc in /home/zombie/workspace/triforce/docker/*/docker-compose.yml; do
        [ -f "$dc" ] || continue
        dcdir=$(dirname "$dc")
        dcname=$(basename "$dcdir")
        mkdir -p "${BACKUP_BASE}/docker-compose/"
        cp -a "$dc" "${BACKUP_BASE}/docker-compose/${dcname}.yml" 2>/dev/null
    done
fi

# ── ai-coder (if exists) ────────────────────────────────────
do_sync "ai-coder" "/home/zombie/workspace/ai-coder/" "${BACKUP_BASE}/ai-coder/"

# ── DB dumps (WordPress MariaDB) ────────────────────────────
if docker ps --format '{{.Names}}' 2>/dev/null | grep -q wordpress_db; then
    log "Dumping WordPress DB ..."
    mkdir -p "${BACKUP_BASE}/db-dumps/"
    docker exec wordpress_db mariadb-dump -u wordpress -p94ed31becc83a760eada140022345074 wordpress 2>/dev/null \
        | gzip > "${BACKUP_BASE}/db-dumps/wordpress_${TIMESTAMP}.sql.gz"
    # Keep only last 5 dumps
    ls -t "${BACKUP_BASE}/db-dumps/"wordpress_*.sql.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null
    log "DB dump OK"
fi

# ── 4. Metadata ─────────────────────────────────────────────
cat > "${BACKUP_BASE}/last-backup.json" << META
{
    "node_id": "${NODE_ID}",
    "timestamp": "${TIMESTAMP}",
    "date": "$(date -Iseconds)",
    "hostname": "$(hostname)",
    "kernel": "$(uname -r)",
    "errors": ${ERRORS},
    "disk_used": "$(du -sh ${BACKUP_BASE} 2>/dev/null | cut -f1)"
}
META

log "=== ${NODE_ID} backup completed (${ERRORS} errors) ==="

# Cleanup old logs
find /tmp -name 'federation-backup-*.log' -mtime +7 -delete 2>/dev/null
