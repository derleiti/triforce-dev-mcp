#!/usr/bin/env bash
set -Eeuo pipefail

# Legacy compatibility check. The OAuth implementation now lives directly in
# TriForce app/routes/oauth_service.py and app/utils/auth_middleware.py.
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"

required=(
  "$TRIFORCE_DIR/app/routes/oauth_service.py"
  "$TRIFORCE_DIR/app/utils/auth_middleware.py"
  "$TRIFORCE_DIR/app/utils/mcp_auth.py"
)

missing=0
for f in "${required[@]}"; do
  if [[ -f "$f" ]]; then echo "OK: $f"; else echo "MISSING: $f" >&2; missing=1; fi
done

if (( missing )); then
  echo "Current OAuth implementation is incomplete; repair/update the TriForce source tree instead of applying the retired patch." >&2
  exit 1
fi

if systemctl list-unit-files triforce.service >/dev/null 2>&1; then
  echo "TriForce OAuth files are present. Service: triforce.service"
  echo "No legacy patch was applied."
else
  echo "TriForce files are present, but triforce.service is not installed." >&2
  exit 1
fi
