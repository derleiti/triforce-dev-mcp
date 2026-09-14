#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CANONICAL="${SCRIPT_DIR}/../../../../docker/repository/add-ailinux-repo.sh"
if [[ ! -x "$CANONICAL" ]]; then
  echo "AILinux canonical repository client not found: $CANONICAL" >&2
  exit 1
fi
exec "$CANONICAL" "$@"
