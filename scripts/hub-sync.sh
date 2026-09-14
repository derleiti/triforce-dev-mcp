#!/bin/bash
# ============================================================================
# TriForce Hub Sync Script
# Synchronizes all federation hubs with latest release from update.ailinux.me
# ============================================================================

set -e

# Configuration
UPDATE_URL="https://update.ailinux.me/server"
MANIFEST_URL="${UPDATE_URL}/manifest.json"
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/workspace/triforce}"
BACKUP_DIR="${TRIFORCE_DIR}/.backups/updates"
LOG_FILE="${TRIFORCE_DIR}/logs/hub-sync.log"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() {
    local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $1"
    echo -e "$msg"
    echo "$msg" >> "$LOG_FILE" 2>/dev/null || true
}

# Ensure directories
mkdir -p "$BACKUP_DIR" "$(dirname $LOG_FILE)"

log "${GREEN}=== TriForce Hub Sync ===${NC}"

# 1. Fetch manifest
log "Fetching manifest from ${MANIFEST_URL}..."
MANIFEST=$(curl -sf "$MANIFEST_URL") || {
    log "${RED}ERROR: Cannot fetch manifest${NC}"
    exit 1
}

# 2. Parse version info
REMOTE_VERSION=$(echo "$MANIFEST" | python3 -c "import sys,json; print(json.load(sys.stdin)['server']['version'])" 2>/dev/null)
REMOTE_SHA256=$(echo "$MANIFEST" | python3 -c "import sys,json; print(json.load(sys.stdin)['server']['downloads']['tarball']['sha256'])" 2>/dev/null)
DOWNLOAD_URL=$(echo "$MANIFEST" | python3 -c "import sys,json; print(json.load(sys.stdin)['server']['downloads']['tarball']['url'])" 2>/dev/null)

if [ -z "$REMOTE_VERSION" ]; then
    log "${RED}ERROR: Cannot parse manifest${NC}"
    exit 1
fi

log "Remote version: ${REMOTE_VERSION}"

# 3. Check local version
LOCAL_VERSION=$(grep -oP "VERSION\s*=\s*['\"]?\K[0-9]+\.[0-9]+" "${TRIFORCE_DIR}/app/config.py" 2>/dev/null || echo "0.0")
log "Local version: ${LOCAL_VERSION}"

# 4. Compare versions
if [ "$REMOTE_VERSION" = "$LOCAL_VERSION" ]; then
    log "${GREEN}Already up to date (v${LOCAL_VERSION})${NC}"
    exit 0
fi

log "${YELLOW}Update available: ${LOCAL_VERSION} -> ${REMOTE_VERSION}${NC}"

# 5. Download update
TEMP_FILE=$(mktemp)
log "Downloading ${DOWNLOAD_URL}..."
curl -sfL "$DOWNLOAD_URL" -o "$TEMP_FILE" || {
    log "${RED}ERROR: Download failed${NC}"
    rm -f "$TEMP_FILE"
    exit 1
}

# 6. Verify SHA256
ACTUAL_SHA256=$(sha256sum "$TEMP_FILE" | cut -d' ' -f1)
if [ "$ACTUAL_SHA256" != "$REMOTE_SHA256" ]; then
    log "${RED}ERROR: SHA256 mismatch!${NC}"
    log "Expected: $REMOTE_SHA256"
    log "Got:      $ACTUAL_SHA256"
    rm -f "$TEMP_FILE"
    exit 1
fi
log "${GREEN}SHA256 verified${NC}"

# 7. Backup current
BACKUP_FILE="${BACKUP_DIR}/triforce-${LOCAL_VERSION}-$(date +%Y%m%d_%H%M%S).tar.gz"
log "Creating backup: ${BACKUP_FILE}"
tar -czf "$BACKUP_FILE" -C "$(dirname $TRIFORCE_DIR)" \
    --exclude='.venv' \
    --exclude='logs/*.log' \
    --exclude='.backups' \
    "$(basename $TRIFORCE_DIR)/app" \
    "$(basename $TRIFORCE_DIR)/config" \
    "$(basename $TRIFORCE_DIR)/scripts" 2>/dev/null || true

# 8. Extract update
log "Extracting update..."
tar -xzf "$TEMP_FILE" -C "$TRIFORCE_DIR" --strip-components=0 2>/dev/null || {
    # Try without strip
    tar -xzf "$TEMP_FILE" -C "$TRIFORCE_DIR"
}
rm -f "$TEMP_FILE"

# 9. Restart service
log "Restarting triforce service..."
if systemctl is-active --quiet triforce.service; then
    sudo systemctl restart triforce.service
    sleep 3
    if systemctl is-active --quiet triforce.service; then
        log "${GREEN}Service restarted successfully${NC}"
    else
        log "${RED}Service failed to start! Rolling back...${NC}"
        tar -xzf "$BACKUP_FILE" -C "$(dirname $TRIFORCE_DIR)"
        sudo systemctl restart triforce.service
        exit 1
    fi
fi

log "${GREEN}=== Update complete: v${REMOTE_VERSION} ===${NC}"

# 10. Cleanup old backups (keep last 5)
ls -t "${BACKUP_DIR}"/triforce-*.tar.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true

exit 0
