#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="triforce.service"
UNIT="/etc/systemd/system/$SERVICE"

[[ $EUID -eq 0 ]] || { echo "Run with sudo/root." >&2; exit 1; }
[[ -f "$UNIT" ]] || { echo "Missing unit: $UNIT" >&2; exit 1; }

echo "Checking sudo-compatible TriForce service hardening..."
if grep -q '^NoNewPrivileges=false' "$UNIT"; then
  echo "OK: NoNewPrivileges=false already set"
else
  cp -a "$UNIT" "$UNIT.bak.$(date +%Y%m%d-%H%M%S)"
  if grep -q '^NoNewPrivileges=' "$UNIT"; then
    sed -i 's/^NoNewPrivileges=.*/NoNewPrivileges=false/' "$UNIT"
  else
    sed -i '/^\[Service\]/a NoNewPrivileges=false' "$UNIT"
  fi
  systemd-analyze verify "$UNIT"
  systemctl daemon-reload
  systemctl restart "$SERVICE"
fi
systemctl --no-pager --full status "$SERVICE" | sed -n '1,35p'
