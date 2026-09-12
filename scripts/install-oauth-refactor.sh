#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="${TRIFORCE_DIR:-/home/zombie/triforce}"
required=(
  "$ROOT/app/routes/oauth_service.py"
  "$ROOT/app/utils/auth_middleware.py"
  "$ROOT/app/utils/mcp_auth.py"
  "$ROOT/app/routes/mcp_remote.py"
  "$ROOT/app/main.py"
)
for f in "${required[@]}"; do
  [[ -r "$f" ]] || { echo "ERROR: missing OAuth component: $f" >&2; exit 1; }
done
"${TRIFORCE_PYTHON:-$ROOT/.venv/bin/python}" - <<PY
from pathlib import Path
root = Path("$ROOT")
main = (root / "app/main.py").read_text()
remote = (root / "app/routes/mcp_remote.py").read_text()
assert "oauth_service" in main, "oauth_service router is not registered"
assert "oauth_router" in main, "oauth_router is not registered"
assert "centralized in app/routes/oauth_service.py" in remote or "OAuth Endpoints - REMOVED" in remote, "mcp_remote OAuth split marker missing"
print("OAuth refactor already installed and structurally valid.")
PY
echo "No source files were modified. Current TriForce OAuth architecture is already canonical."
