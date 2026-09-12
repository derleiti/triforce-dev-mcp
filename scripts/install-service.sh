#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Usage: sudo $0" >&2; exit 1; }
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
TARGET="/etc/systemd/system/triforce.service"

candidates=(
  "$TRIFORCE_DIR/scripts/systemd/triforce.service"
  "$TRIFORCE_DIR/packaging/systemd/triforce.service"
  "$TRIFORCE_DIR/config/triforce.service"
)
source_unit=""
for f in "${candidates[@]}"; do
  if [[ -f "$f" ]]; then source_unit="$f"; break; fi
done
[[ -n "$source_unit" ]] || { echo "No current triforce.service source found in $TRIFORCE_DIR" >&2; exit 1; }

systemd-analyze verify "$source_unit"
if [[ -f "$TARGET" ]]; then
  cp -a "$TARGET" "$TARGET.bak.$(date +%Y%m%d-%H%M%S)"
fi
install -m 0644 "$source_unit" "$TARGET"
systemctl daemon-reload
systemctl enable --now triforce.service
systemctl --no-pager --full status triforce.service | sed -n '1,35p'
