#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE="triforce.service"
UNIT="/etc/systemd/system/$SERVICE"

[[ $EUID -eq 0 ]] || { echo "Run with sudo/root." >&2; exit 1; }
[[ -f "$UNIT" ]] || { echo "Missing unit: $UNIT" >&2; exit 1; }

# Current TriForce intentionally runs with these relaxed settings because
# privileged operations are mediated by its own helper/policy layer.
required=(
  'NoNewPrivileges=false'
  'ProtectSystem=false'
  'PrivateTmp=false'
)

changed=0
backup="$UNIT.bak.full.$(date +%Y%m%d-%H%M%S)"
for setting in "${required[@]}"; do
  key=${setting%%=*}
  if grep -q "^${setting}$" "$UNIT"; then
    echo "OK: $setting"
    continue
  fi
  (( changed )) || cp -a "$UNIT" "$backup"
  if grep -q "^${key}=" "$UNIT"; then
    sed -i "s/^${key}=.*/${setting}/" "$UNIT"
  else
    sed -i "/^\[Service\]/a ${setting}" "$UNIT"
  fi
  changed=1
done

if (( changed )); then
  systemd-analyze verify "$UNIT"
  systemctl daemon-reload
  systemctl restart "$SERVICE"
fi

systemctl is-active --quiet "$SERVICE"
echo "TriForce service is active with current sudo-compatible settings."
grep -E '^(NoNewPrivileges|ProtectSystem|PrivateTmp)=' "$UNIT"
