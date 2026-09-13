#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entrypoint. Repository signing has exactly one canonical
# implementation to prevent key/configuration drift between script copies.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
CANONICAL="$ROOT/docker/repository/sign-repos.sh"

if [[ ! -x "$CANONICAL" ]]; then
    printf 'Canonical repository signer not found or not executable: %s\n' "$CANONICAL" >&2
    exit 1
fi

exec "$CANONICAL" "$@"
