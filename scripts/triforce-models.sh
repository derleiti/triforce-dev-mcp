#!/usr/bin/env bash
# triforce-models.sh — Model registry inventory snapshot
# Usage:
#   ./triforce-models.sh           # pretty table (default)
#   ./triforce-models.sh --total   # just the number
#   ./triforce-models.sh --json    # JSON for piping/jq
#   ./triforce-models.sh --watch   # auto-refresh every 5s
#   ./triforce-models.sh --help
#
# TRIFORCE_VERBOSE=1 keeps init logs visible (default: suppressed via marker)
#
# Created: 2026-04-28 (Code Review Session)

set -euo pipefail

ROOT="${TRIFORCE_ROOT:-/home/zombie/workspace/triforce}"
PY="${ROOT}/.venv/bin/python3"
MODE="${1:-table}"

if [[ ! -x "$PY" ]]; then
    echo "Error: Python venv not found at $PY" >&2
    echo "Hint: set TRIFORCE_ROOT or run scripts/setup-venv.sh" >&2
    exit 1
fi

case "$MODE" in
    --watch)
        exec watch -n5 -c "$0"
        ;;
    --total|-t) FORMAT="total" ;;
    --json|-j)  FORMAT="json"  ;;
    --help|-h)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        exit 0
        ;;
    table|"")   FORMAT="table" ;;
    *)
        echo "Unknown option: $MODE (use --help)" >&2
        exit 2
        ;;
esac

cd "$ROOT"

# Marker-based filtering: app-init logs go to /dev/null in non-verbose mode,
# the actual output is delimited by ---BEGIN/END---
MARKER_BEGIN="---BEGIN_MODELS_OUTPUT---"
MARKER_END="---END_MODELS_OUTPUT---"

OUTPUT=$("$PY" -c "
import asyncio, json, os
from collections import Counter
os.environ.setdefault('FEDERATION_PSK', 'inventory-script')

from app.services.model_registry import registry

async def main():
    models = await registry.list_models()
    by_provider = Counter(getattr(m, 'provider', 'unknown') for m in models)
    total = len(models)
    fmt = '$FORMAT'

    print('$MARKER_BEGIN')
    if fmt == 'total':
        print(total)
    elif fmt == 'json':
        out = {'total': total, 'providers': dict(by_provider.most_common())}
        print(json.dumps(out, indent=2))
    else:
        print(f'TriForce Model Registry — {total} models across {len(by_provider)} providers')
        print('─' * 50)
        for p, c in by_provider.most_common():
            bar = '█' * max(1, c * 30 // total)
            print(f'  {p:14s} {c:4d}  {bar}')
        print('─' * 50)
    print('$MARKER_END')

asyncio.run(main())
" 2>&1)

if [[ -n "${TRIFORCE_VERBOSE:-}" ]]; then
    echo "$OUTPUT"
else
    # Print only between markers
    echo "$OUTPUT" | sed -n "/$MARKER_BEGIN/,/$MARKER_END/p" | sed '1d;$d'
fi
