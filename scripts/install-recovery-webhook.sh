#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Usage: sudo $0" >&2; exit 1; }
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
UNIT_DIR=/etc/systemd/system
CONFIG_DIR=/etc/triforce
TOKEN_FILE="$CONFIG_DIR/recovery-token"
MARKER="$CONFIG_DIR/source-mode.enabled"

[[ -d "$TRIFORCE_DIR/.git" ]] || { echo "Not a TriForce source checkout: $TRIFORCE_DIR" >&2; exit 1; }
[[ -x "$TRIFORCE_DIR/scripts/install-service.sh" ]] || { echo "Missing install-service.sh" >&2; exit 1; }
[[ -x "$TRIFORCE_DIR/scripts/start-triforce.sh" ]] || { echo "Missing start-triforce.sh" >&2; exit 1; }

install -d -m 0750 "$CONFIG_DIR"
if [[ ! -s "$TOKEN_FILE" ]]; then
    umask 077
    python3 - <<'PYTOKEN' > "$TOKEN_FILE"
import secrets
print(secrets.token_urlsafe(48))
PYTOKEN
fi
chmod 0600 "$TOKEN_FILE"
chown root:root "$TOKEN_FILE"
printf 'source\n' > "$MARKER"
chmod 0644 "$MARKER"

install -m 0644 "$TRIFORCE_DIR/packaging/systemd/triforce-source-recovery.service" "$UNIT_DIR/triforce-source-recovery.service"
install -m 0644 "$TRIFORCE_DIR/packaging/systemd/triforce-recovery-webhook.service" "$UNIT_DIR/triforce-recovery-webhook.service"

systemd-analyze verify "$UNIT_DIR/triforce-source-recovery.service" "$UNIT_DIR/triforce-recovery-webhook.service"
systemctl daemon-reload
systemctl enable --now triforce-recovery-webhook.service

echo "TriForce recovery webhook enabled on wg0:9191 (token stored root-only; not printed)."
