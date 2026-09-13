#!/usr/bin/env bash
set -Eeuo pipefail

TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
INSTALLER="$TRIFORCE_DIR/scripts/install-service.sh"
EXPECTED_START="$TRIFORCE_DIR/scripts/start-triforce.sh"
MARKER="/etc/triforce/source-mode.enabled"

log() { printf '[triforce-recovery] %s\n' "$*"; }
fail() { log "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "must run as root"
[[ -f "$MARKER" ]] || fail "source-mode marker missing: $MARKER"
[[ -x "$INSTALLER" ]] || fail "installer missing or not executable: $INSTALLER"
[[ -x "$EXPECTED_START" ]] || fail "source start script missing or not executable: $EXPECTED_START"
[[ -d "$TRIFORCE_DIR/.git" ]] || fail "refusing non-checkout source directory: $TRIFORCE_DIR"

log "restoring operator-selected source service from $TRIFORCE_DIR"
"$INSTALLER" --mode source
systemctl daemon-reload
systemctl enable triforce.service >/dev/null
systemctl restart triforce.service

# Do not assume an instant Uvicorn bind after restart. Wait up to 30s.
for _ in $(seq 1 30); do
    if systemctl is-active --quiet triforce.service; then
        if systemctl show triforce.service -p ExecStart --value | grep -Fq "$EXPECTED_START"; then
            if curl -fsS --max-time 2 "${TRIFORCE_RECOVERY_HEALTH_URL:-http://172.17.0.1:9000/health}" >/dev/null 2>&1; then
                log "source service restored and health check passed"
                exit 0
            fi
        fi
    fi
    sleep 1
done

systemctl --no-pager --full status triforce.service || true
fail "source service did not become healthy within 30 seconds"
