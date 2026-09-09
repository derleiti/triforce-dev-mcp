#!/bin/bash
# TriForce startup wrapper. Configuration files are parsed as data by Python;
# this script intentionally never source/eval's user-editable dotenv content.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
BRANCH="${TRIFORCE_BRANCH:-master}"
UPDATE_INTERVAL="${TRIFORCE_UPDATE_INTERVAL:-300}"
AUTO_UPDATE="${TRIFORCE_AUTO_UPDATE:-0}"
CONFIG_FILE="${TRIFORCE_CONFIG_FILE:-$REPO_DIR/config/triforce.env}"
PYTHON_BIN="${TRIFORCE_PYTHON:-$REPO_DIR/.venv/bin/python}"

cd "$REPO_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

do_update() {
    log "Checking for updates (branch=$BRANCH)..."
    local current_branch remote_hash local_hash need_stash=0
    current_branch=$(git branch --show-current 2>/dev/null || echo "")
    if [ "$current_branch" != "$BRANCH" ]; then
        log "Skip update: on branch '$current_branch' but BRANCH='$BRANCH'"
        return 1
    fi
    if ! git fetch origin "$BRANCH" 2>/dev/null; then
        log "Skip update: 'git fetch origin $BRANCH' failed"
        return 1
    fi
    if ! remote_hash=$(git rev-parse --verify "origin/$BRANCH" 2>/dev/null); then
        log "Skip update: remote ref 'origin/$BRANCH' not found"
        return 1
    fi
    local_hash=$(git rev-parse HEAD)
    [ "$local_hash" != "$remote_hash" ] || return 1
    log "Update available: $local_hash -> $remote_hash"
    if ! git diff --quiet || ! git diff --cached --quiet; then
        need_stash=1
        log "Local uncommitted edits detected, stashing..."
        git stash push -m "auto-stash $(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
    fi
    if ! git pull --ff-only origin "$BRANCH"; then
        log "Pull failed (not fast-forward). Restoring stash if created..."
        [ "$need_stash" = "1" ] && git stash pop 2>/dev/null || true
        return 1
    fi
    log "Updated to $(git rev-parse --short HEAD)"
    return 0
}

update_loop() {
    while true; do
        sleep "$UPDATE_INTERVAL"
        if do_update; then
            log "Code changed - signaling restart..."
            kill -TERM "$MAIN_PID" 2>/dev/null || true
            exit 0
        fi
    done
}

[ -x "$PYTHON_BIN" ] || { log "Python runtime missing: $PYTHON_BIN"; exit 1; }

log "Starting TriForce..."
UPDATE_PID=""
if [ "$AUTO_UPDATE" = "1" ]; then
    log "Auto-update enabled (branch=$BRANCH, interval=${UPDATE_INTERVAL}s)"
    do_update || log "Already up to date"
    update_loop & UPDATE_PID=$!
else
    log "Auto-update disabled"
fi

# Parse the configuration once as dotenv data, validate it through the canonical
# Settings model, then export the resolved file values to the Uvicorn process.
# This keeps legacy os.getenv() consumers consistent without source/eval.
export TRIFORCE_CONFIG_FILE="$CONFIG_FILE"
log "Launching backend with canonical config: $CONFIG_FILE"
"$PYTHON_BIN" -m app.server_launcher --config "$CONFIG_FILE" &
MAIN_PID=$!
log "Backend PID: $MAIN_PID"

cleanup() {
    [ -z "$UPDATE_PID" ] || kill "$UPDATE_PID" 2>/dev/null || true
    kill -TERM "$MAIN_PID" 2>/dev/null || true
}

on_signal() {
    cleanup
    set +e
    wait "$MAIN_PID" 2>/dev/null
    set -e
    exit 0
}
trap on_signal TERM INT

set +e
wait "$MAIN_PID"
EXIT_CODE=$?
set -e
[ -z "$UPDATE_PID" ] || kill "$UPDATE_PID" 2>/dev/null || true
exit "$EXIT_CODE"
