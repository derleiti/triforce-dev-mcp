#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

ts="$(date +%Y%m%d_%H%M%S)"
BAKDIR="$ROOT/.patch-bak/$ts"

log() { echo "[$(date +%H:%M:%S)] $*"; }

log "0) prepare backup dir: $BAKDIR"
mkdir -p "$BAKDIR"

log "1) normalize CRLF in config/triforce.env (if exists)"
if [[ -f "$ROOT/config/triforce.env" ]]; then
  mkdir -p "$BAKDIR/config"
  cp -a "$ROOT/config/triforce.env" "$BAKDIR/config/triforce.env" || true
  sed -i 's/\r$//' "$ROOT/config/triforce.env"
fi

log "2) patch app/routes/client_chat.py (FORCE_GEMINI_OPENROUTER + OpenRouter timeout)"

python3 - <<'PY'
import re, shutil
from pathlib import Path

ROOT = Path("/home/zombie/workspace/triforce")
BAKDIR = Path((ROOT / ".patch-bak").glob("*").__iter__().__next__().as_posix())  # fallback safety (unused)
# better: read from env? not needed here because we already created BAKDIR in bash
# We'll detect the newest backup dir (the one created just now) robustly:
bak_root = ROOT / ".patch-bak"
newest = max([p for p in bak_root.iterdir() if p.is_dir()], key=lambda p: p.name)
BAKDIR = newest

def backup_file(src: Path):
    rel = src.relative_to(ROOT)
    dst = BAKDIR / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)

def write_if_changed(path: Path, new_text: str, label: str) -> bool:
    old = path.read_text(encoding="utf-8")
    if old == new_text:
        print(f"SKIP: {label} (no change)")
        return False
    backup_file(path)
    path.write_text(new_text, encoding="utf-8")
    print(f"OK:   {label}")
    return True

p = ROOT / "app/routes/client_chat.py"
if not p.exists():
    raise SystemExit("ERR: app/routes/client_chat.py not found")

s = p.read_text(encoding="utf-8")
orig = s

# --- ensure "import os" exists (needed for FORCE_GEMINI_OPENROUTER env var) ---
if not re.search(r'^\s*import\s+os\b', s, flags=re.M):
    m = re.search(r'^(from|import)\s+', s, flags=re.M)
    if m:
        s = s[:m.start()] + "import os\n" + s[m.start():]
    else:
        s = "import os\n" + s

# --- DEMO_MODE mapping: extend with FORCE_GEMINI_OPENROUTER (idempotent) ---
if "FORCE_GEMINI_OPENROUTER" not in s:
    s = re.sub(
        r'(?m)^(?P<ind>\s*)if\s+DEMO_MODE\s+and\s+model\.startswith\("gemini/"\)\s*:\s*$',
        r'\g<ind>force_or = (os.getenv("FORCE_GEMINI_OPENROUTER") or "").strip().lower() in ("1","true","yes","on")\n\n'
        r'\g<ind>if (DEMO_MODE or force_or) and model.startswith("gemini/"):',
        s,
        count=1
    )

# --- OpenRouter timeout + error handling (idempotent) ---
# only patch if old form exists and new form not already present
if 'httpx.Timeout(connect=' not in s and 'AsyncClient(timeout=120.0)' in s:
    pat = r'''(?msx)
^(?P<ind>\s*)async\ with\ httpx\.AsyncClient\(timeout=120\.0\)\ as\ client:\n
(?P=ind)\s*response\s*=\s*await\s+client\.post\(\n
(?P<body>.*?)
(?P=ind)\s*\)\n
'''
    m = re.search(pat, s)
    if m:
        ind = m.group("ind")
        body = m.group("body").splitlines()
        # normalize captured body indentation
        stripped = [ln.lstrip() for ln in body]
        while stripped and stripped[0] == "":
            stripped.pop(0)
        while stripped and stripped[-1] == "":
            stripped.pop()

        inner = "\n".join([ind + "            " + ln for ln in stripped])  # 12 spaces relative to ind

        repl = (
            f"{ind}timeout = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)\n\n"
            f"{ind}async with httpx.AsyncClient(timeout=timeout) as client:\n"
            f"{ind}    try:\n"
            f"{ind}        response = await client.post(\n"
            f"{inner}\n"
            f"{ind}        )\n"
            f"{ind}    except httpx.ReadTimeout:\n"
            f"{ind}        raise HTTPException(504, \"OpenRouter Timeout (ReadTimeout). Bitte erneut versuchen.\")\n"
            f"{ind}    except httpx.ConnectTimeout:\n"
            f"{ind}        raise HTTPException(504, \"OpenRouter Timeout (ConnectTimeout).\")\n"
            f"{ind}    except httpx.TimeoutException:\n"
            f"{ind}        raise HTTPException(504, \"OpenRouter Timeout.\")\n"
            f"{ind}    except httpx.RequestError as err:\n"
            f"{ind}        raise HTTPException(502, f\"OpenRouter RequestError: {err}\")\n"
        )
        s = re.sub(pat, repl, s, count=1)
    else:
        print("WARN: OpenRouter AsyncClient(timeout=120.0) block not matched, skipped timeout patch")

write_if_changed(p, s, "client_chat.py FORCE_GEMINI_OPENROUTER + OpenRouter timeout")
PY

log "3) python compileall"
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  "$ROOT/.venv/bin/python" -m compileall -q app
else
  python3 -m compileall -q app
fi

log "4) restart triforce"
sudo systemctl restart triforce

log "5) quick checks"
grep -n "FORCE_GEMINI_OPENROUTER" -n app/routes/client_chat.py || true
grep -n "httpx.Timeout(connect=" -n app/routes/client_chat.py || true

curl -fsS http://127.0.0.1:9000/health && echo
curl -fsS http://127.0.0.1:9000/v1/client/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hi","model":"gemini/gemini-2.0-flash-001"}' \
| python3 -m json.tool | egrep '"backend"|"model"|"tier"'
