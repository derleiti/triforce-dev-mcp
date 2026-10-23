#!/usr/bin/env bash
# Safe TriForce federation audit / explicit single-node rotation.
# Intentionally read-only by default: the old daily auto-rotation was unsafe.
set -Eeuo pipefail
umask 077

REPO="/home/zombie/workspace/triforce"
PYTHON="$REPO/.venv/bin/python"
RUNNER="$REPO/scripts/federation/safe_token_rotation.py"

[[ -x "$PYTHON" && -f "$RUNNER" ]] || {
  printf '%s\n' "Federation runner missing; no credentials changed." >&2
  exit 1
}

exec "$PYTHON" "$RUNNER" "$@"
