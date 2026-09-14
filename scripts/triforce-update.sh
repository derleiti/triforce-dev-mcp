#!/bin/bash
#
# TriForce GitHub Auto-Update Service
# Pulls changes from GitHub and restarts service if needed
#

set -euo pipefail

REPO_DIR="${TRIFORCE_DIR:-/home/zombie/workspace/triforce}"
SERVICE_NAME="triforce"
BRANCH="master"
LOG_TAG="triforce-update"

log() {
    logger -t "$LOG_TAG" "$1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

error() {
    logger -t "$LOG_TAG" -p user.err "$1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" >&2
}

cd "$REPO_DIR" || { error "Cannot cd to $REPO_DIR"; exit 1; }

# Check if git repo
if [ ! -d .git ]; then
    error "Not a git repository: $REPO_DIR"
    exit 1
fi

# Fetch remote changes
log "Fetching remote changes..."
if ! git fetch origin "$BRANCH" 2>&1; then
    error "Git fetch failed"
    exit 1
fi

# Compare local vs remote
LOCAL_HASH=$(git rev-parse HEAD)
REMOTE_HASH=$(git rev-parse "origin/$BRANCH")

if [ "$LOCAL_HASH" = "$REMOTE_HASH" ]; then
    log "Already up to date ($LOCAL_HASH)"
    exit 0
fi

log "Update available: $LOCAL_HASH -> $REMOTE_HASH"

# Check for local changes
if ! git diff --quiet HEAD 2>/dev/null; then
    if [ "${1:-}" = "--force" ]; then
        log "Force mode: stashing local changes"
        git stash push -m "auto-stash $(date +%Y%m%d-%H%M%S)" || true
    else
        error "Local changes detected. Use --force to stash"
        exit 1
    fi
fi

# Pull changes
log "Pulling changes..."
if ! git pull --ff-only origin "$BRANCH" 2>&1; then
    error "Git pull failed"
    exit 1
fi

NEW_HASH=$(git rev-parse HEAD)
log "Updated to $NEW_HASH"

# Check if service restart needed
CHANGED_FILES=$(git diff --name-only "$LOCAL_HASH" "$NEW_HASH")

if echo "$CHANGED_FILES" | grep -qE '\.(py|yaml|yml|json)$'; then
    log "Config/code changed - restarting $SERVICE_NAME..."
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl restart "$SERVICE_NAME"
        sleep 2
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            log "Service restarted successfully"
        else
            error "Service failed to restart!"
            exit 1
        fi
    fi
else
    log "No restart needed"
fi

log "Update complete"
