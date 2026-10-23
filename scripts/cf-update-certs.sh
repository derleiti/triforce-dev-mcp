#!/usr/bin/env bash
# AILinux Let's Encrypt renewal: Cloudflare DNS-01; all existing lineages.
# DNS credentials and R2 credentials are intentionally separate.
set -Eeuo pipefail
umask 077
export HOME=/home/zombie

CONFIG_FILE="/home/zombie/workspace/triforce/config/triforce.env"
CRED_FILE="/etc/letsencrypt/cloudflare.ini"
LOG_DIR="/var/log/cf-le"
LOG_FILE="$LOG_DIR/cf-update-certs.log"
LOCK_FILE="/run/lock/ailinux-cert-renew.lock"

log() {
  printf '[CF-LE] %s %s\n' "$(date -Iseconds)" "$*" | tee -a "$LOG_FILE"
}
fail() {
  log "ERROR: $*"
  exit 1
}

[ "$(id -u)" -eq 0 ] || { echo "Must run as root." >&2; exit 1; }
install -d -m 0750 "$LOG_DIR"
touch "$LOG_FILE"
chmod 0640 "$LOG_FILE"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  log "Another certificate renewal is running; skipping overlap."
  exit 0
fi

if [ "$#" -eq 0 ]; then
  MODE="--renew"
else
  MODE="$1"
fi
case "$MODE" in
  --renew|--dry-run|--check) ;;
  *) echo "Usage: $0 [--renew|--dry-run|--check]" >&2; exit 2 ;;
esac

command -v certbot >/dev/null 2>&1 || fail "Certbot missing"
certbot plugins 2>/dev/null | grep -q 'dns-cloudflare' || fail "Certbot dns-cloudflare plugin missing"

# Validate the DNS-zone visibility before changing any credential file.
# The config reader deliberately does not source/eval triforce.env.
# Never use CLOUDFLARE_R2_* credentials for ACME DNS challenges.
log "Checking Cloudflare zone access for ailinux.me ($MODE)."
if ! python3 - "$CONFIG_FILE" "$CRED_FILE" <<'PY' 2>&1 | tee -a "$LOG_FILE"; then
import json
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

config_path = pathlib.Path(sys.argv[1])
cred_path = pathlib.Path(sys.argv[2])
zone = "ailinux.me"

def parse_kv(path):
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        values[key.strip()] = value
    return values

settings = parse_kv(config_path)
expected_zone_id = settings.get("CLOUDFLARE_ZONE_ID", "")
if not (len(expected_zone_id) == 32 and all(x in "0123456789abcdef" for x in expected_zone_id.lower())):
    print("BLOCKED: CLOUDFLARE_ZONE_ID must be a 32-character hexadecimal zone identifier.")
    sys.exit(78)
stored = parse_kv(cred_path)
current = stored.get("dns_cloudflare_api_token", "")
# Deliberately never use generic Cloudflare, Workers AI or R2 tokens for DNS.
candidates = [
    ("CLOUDFLARE_DNS_API_TOKEN", settings.get("CLOUDFLARE_DNS_API_TOKEN", "")),
    ("existing credential file", current),
]

seen = set()
selected = None
for label, token in candidates:
    if not token or token in seen:
        continue
    seen.add(token)
    url = "https://api.cloudflare.com/client/v4/zones?" + urllib.parse.urlencode(
        {"name": zone, "per_page": "1"}
    )
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "User-Agent": "AILinux-Cert-Renew/1.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
            status_code = response.status
        matches = [
            item for item in result.get("result", [])
            if isinstance(item, dict) and item.get("name") == zone and item.get("id") == expected_zone_id
        ]
        if status_code == 200 and result.get("success") and matches:
            selected = (label, token)
            print("ZONE_CHECK_OK source=" + label + " zone=" + zone + " zone_id=" + expected_zone_id)
            break
        print("ZONE_CHECK_FAIL source=" + label + " http=" + str(status_code)
              + " visible_zones=" + str(len(matches)))
    except urllib.error.HTTPError as exc:
        print("ZONE_CHECK_FAIL source=" + label + " http=" + str(exc.code))
    except Exception as exc:
        print("ZONE_CHECK_FAIL source=" + label
              + " reason=" + type(exc).__name__)

if selected is None:
    print("BLOCKED: No DNS API token can read zone ailinux.me; existing credentials untouched.")
    sys.exit(78)

label, validated_token = selected
if validated_token != current:
    cred_path.parent.mkdir(parents=True, exist_ok=True)
    if cred_path.exists():
        backup_dir = pathlib.Path("/var/backups/ailinux-cert-renew")
        backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        backup = backup_dir / (
            "cloudflare.ini.pre-sync-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        )
        shutil.copy2(cred_path, backup)
        os.chmod(backup, 0o600)
    fd, temp_name = tempfile.mkstemp(prefix=".cloudflare.", dir=str(cred_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("dns_cloudflare_api_token = " + validated_token + "\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, cred_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    print("CREDENTIALS_SYNC_OK validated DNS token activated; previous credentials backed up")
else:
    print("CREDENTIALS_OK existing validated token unchanged")
PY
  fail "Cloudflare DNS-01 preflight failed; certificates were not modified."
fi

log "Running Certbot for all existing certificate lineages."
case "$MODE" in
  --check)
    certbot certificates 2>&1 | tee -a "$LOG_FILE"
    ;;
  --dry-run)
    certbot renew --dry-run --non-interactive --no-random-sleep-on-renew 2>&1 | tee -a "$LOG_FILE"
    ;;
  --renew)
    certbot renew --non-interactive --no-random-sleep-on-renew 2>&1 | tee -a "$LOG_FILE"
    ;;
esac
log "Certbot $MODE completed successfully."
