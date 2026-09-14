#!/bin/bash
# TriForce Node Sync - Updates vom Hetzner Master
# Bootup + alle 5 Minuten via systemd timer

set -euo pipefail

MASTER_HOST="10.10.0.1"
MASTER_USER="zombie"
MASTER_DIR="/home/zombie/workspace/triforce"
MASTER_SSH="${MASTER_USER}@${MASTER_HOST}"
SERVICE_NAME="triforce"

# User + Pfad aus laufendem triforce.service ermitteln
TRIFORCE_USER=$(systemctl show "$SERVICE_NAME" --property=User --value 2>/dev/null | tr -d '[:space:]')
[ -z "$TRIFORCE_USER" ] && TRIFORCE_USER="zombie"

TRIFORCE_DIR="/home/${TRIFORCE_USER}/triforce"
SSH_KEY="/home/${TRIFORCE_USER}/.ssh/id_ed25519"
LOG_FILE="${TRIFORCE_DIR}/logs/node-sync.log"
LOCK_FILE="/tmp/triforce-node-sync.lock"
BACKUP_DIR="${TRIFORCE_DIR}/.backups/node-sync"
SYNC_PATHS=("app/" "requirements.txt" "VERSION")

if [ ! -d "$TRIFORCE_DIR" ]; then echo "ERROR: $TRIFORCE_DIR fehlt" >&2; exit 1; fi
if [ ! -f "$SSH_KEY" ];      then echo "ERROR: $SSH_KEY fehlt" >&2; exit 1; fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" 2>/dev/null || true; }

if [ -e "$LOCK_FILE" ]; then log "Lock aktiv - ueberspringe"; exit 0; fi
echo $$ > "$LOCK_FILE"; trap 'rm -f "$LOCK_FILE"' EXIT

mkdir -p "$(dirname "$LOG_FILE")" "$BACKUP_DIR"
log "=== Node-Sync Start ($(hostname) | user:${TRIFORCE_USER}) ==="

SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new -i ${SSH_KEY}"

if ! ssh $SSH_OPTS "$MASTER_SSH" "echo ok" &>/dev/null; then
    log "WARN: Master ${MASTER_HOST} nicht erreichbar"; exit 0
fi

MASTER_VERSION=$(ssh $SSH_OPTS "$MASTER_SSH" "cat ${MASTER_DIR}/VERSION" | tr -d '[:space:]')
LOCAL_VERSION=$(cat "${TRIFORCE_DIR}/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "0.0.0")
log "Master v${MASTER_VERSION} | Lokal v${LOCAL_VERSION}"

if [ "$MASTER_VERSION" = "$LOCAL_VERSION" ]; then
    log "Aktuell (v${LOCAL_VERSION}) - kein Update"; exit 0
fi

log "UPDATE: ${LOCAL_VERSION} -> ${MASTER_VERSION}"
STAMP=$(date '+%Y%m%d_%H%M%S')
BACKUP_PATH="${BACKUP_DIR}/app_${LOCAL_VERSION}_${STAMP}.tar.gz"
tar -czf "$BACKUP_PATH" -C "$TRIFORCE_DIR" app/ requirements.txt VERSION 2>/dev/null || log "WARN: Backup fehlgeschlagen"
ls -t "${BACKUP_DIR}"/app_*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true

REQ_HASH_OLD=$(sha256sum "${TRIFORCE_DIR}/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo "none")

RSYNC_FAILED=0
for P in "${SYNC_PATHS[@]}"; do
    log "  rsync: ${P}"
    rsync -az --delete --exclude='__pycache__' --exclude='*.pyc' \
        -e "ssh -o BatchMode=yes -o ConnectTimeout=10 -i ${SSH_KEY}" \
        "${MASTER_SSH}:${MASTER_DIR}/${P}" "${TRIFORCE_DIR}/${P}" 2>>"$LOG_FILE" || { log "  WARN: ${P} fehlgeschlagen"; RSYNC_FAILED=1; }
done

if [ "$RSYNC_FAILED" = "1" ]; then
    log "ERROR: rsync fehlgeschlagen - rollback"
    tar -xzf "$BACKUP_PATH" -C "$TRIFORCE_DIR" 2>/dev/null || true; exit 1
fi

REQ_HASH_NEW=$(sha256sum "${TRIFORCE_DIR}/requirements.txt" 2>/dev/null | cut -d' ' -f1 || echo "none")
if [ "$REQ_HASH_OLD" != "$REQ_HASH_NEW" ]; then
    log "requirements geaendert - pip install..."
    [ -f "${TRIFORCE_DIR}/.venv/bin/pip" ] && \
        "${TRIFORCE_DIR}/.venv/bin/pip" install -q -r "${TRIFORCE_DIR}/requirements.txt" 2>>"$LOG_FILE" && \
        log "pip OK" || log "WARN: pip fehlgeschlagen"
fi

NEW_VERSION=$(cat "${TRIFORCE_DIR}/VERSION" 2>/dev/null | tr -d '[:space:]' || echo "?")
log "Restart ${SERVICE_NAME} (v${NEW_VERSION})..."
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    systemctl restart "$SERVICE_NAME" 2>>"$LOG_FILE"; sleep 4
    systemctl is-active --quiet "$SERVICE_NAME" && log "Service OK" || {
        log "ERROR: Service fehlgeschlagen - rollback!"
        tar -xzf "$BACKUP_PATH" -C "$TRIFORCE_DIR" 2>/dev/null || true
        systemctl restart "$SERVICE_NAME" 2>/dev/null || true; exit 1
    }
else
    systemctl start "$SERVICE_NAME" 2>/dev/null || true
fi

log "=== Sync fertig: v${LOCAL_VERSION} -> v${NEW_VERSION} ==="
exit 0
