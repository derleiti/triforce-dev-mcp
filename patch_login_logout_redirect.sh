#!/usr/bin/env bash
set -Eeuo pipefail

cd /home/zombie/workspace/triforce

TARGET="/home/zombie/workspace/triforce/docker/wordpress/login.ailinux.me/index.html"
BACKUP_DIR="/home/zombie/workspace/triforce/patch_backups"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="${BACKUP_DIR}/login.repo.index.html.bak.${STAMP}"
TMP_FILE="${BACKUP_DIR}/login.repo.index.html.patched.${STAMP}"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$TARGET" ]]; then
  echo "[ERR] Target not found: $TARGET" >&2
  exit 1
fi

cp "$TARGET" "$BACKUP_FILE"
echo "[OK] Backup created: $BACKUP_FILE"

python3 - "$TARGET" "$TMP_FILE" <<'PY'
from pathlib import Path
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
text = src.read_text(encoding='utf-8')
original = text

needle = """(function checkSession(){
  if(getParam('action')==='logout'){
    ['ailinux_token','ailinux_email','ailinux_tier','ailinux_client_id',
     'ailinux_wp_last_sync','ailinux_wp_can_admin','ailinux_display_name'].forEach(lsRemove);
    return;
  }
"""

replacement = """(function checkSession(){
  if(getParam('action')==='logout'){
    ['ailinux_token','ailinux_email','ailinux_tier','ailinux_client_id',
     'ailinux_wp_last_sync','ailinux_wp_can_admin','ailinux_display_name'].forEach(lsRemove);
    var back=getRedirect();
    if(back){
      setTimeout(function(){ window.location.replace(back); }, 50);
    }
    return;
  }
"""

if "window.location.replace(back)" in text:
    print('[OK] Patch already present, nothing to do.')
    dst.write_text(text, encoding='utf-8')
    raise SystemExit(0)

if needle not in text:
    print('[ERR] Could not find logout session block.', file=sys.stderr)
    raise SystemExit(1)

text = text.replace(needle, replacement, 1)
if text == original:
    print('[ERR] No changes applied.', file=sys.stderr)
    raise SystemExit(1)

dst.write_text(text, encoding='utf-8')
print(f'[OK] Patched temp file {dst}')
PY

mv "$TMP_FILE" "$TARGET"
echo "[OK] Installed patched file to $TARGET"

echo "[OK] Done"
