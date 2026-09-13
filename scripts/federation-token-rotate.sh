#!/bin/bash
# =====================================================
# TriForce Federation Token Auto-Rotation
# Läuft täglich via systemd-Timer (3:00 Uhr)
#
# WICHTIG: Hetzner wird NICHT neu gestartet.
# Der Vault lädt bei verify_token() dynamisch aus
# der Datei — kein Neustart nötig.
# =====================================================
set -euo pipefail

REPO="/home/zombie/workspace/triforce"
VENV="$REPO/.venv/bin/python3"
LOG="$REPO/logs/federation-rotate.log"
SSH_KEY="$HOME/.ssh/id_ed25519"

log()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"; }
err()  { echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $1" | tee -a "$LOG" >&2; }

notify() {
    local title="$1" body="$2" priority="${3:-normal}"
    curl -s -X POST http://localhost:9000/v1/mcp \
        -H "Content-Type: application/json" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"notify_send\",\"arguments\":{\"title\":\"$title\",\"body\":\"$body\",\"priority\":\"$priority\",\"source\":\"system\",\"auto_resolve\":true}},\"id\":1}" \
        > /dev/null 2>&1 || true
}

log "=== Federation Token Rotation Start ==="

# Tokens rotieren — logging.CRITICAL + warnings.ignore unterdrückt TriForce-Output
ROTATION_JSON=$("$VENV" 2>/dev/null << 'PYEOF' | grep '^RESULT:' | sed 's/^RESULT://'
import sys, os, json, logging, warnings
logging.disable(logging.CRITICAL)
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/zombie/workspace/triforce')
os.chdir('/home/zombie/workspace/triforce')
from dotenv import load_dotenv
load_dotenv('/home/zombie/workspace/triforce/config/triforce.env')
from app.services.federation_vault import get_federation_vault
vault = get_federation_vault()
secret = os.getenv('FEDERATION_SECRET', '')
results = {}
for node_id in ['zombie-pc', 'backup']:
    token = vault.rotate_token(node_id) or vault.register_node(node_id, role='node')
    results[node_id] = token
results['_secret'] = secret
sys.stdout.write('RESULT:' + json.dumps(results) + '\n')
PYEOF
)

if [[ -z "$ROTATION_JSON" ]]; then
    err "Token-Rotation fehlgeschlagen — kein JSON Output"
    notify "Federation Rotation FEHLER ❌" "Kein Output vom Python-Script" "high"
    exit 1
fi

log "Tokens rotiert OK"

ZOMBIE_TOKEN=$(echo "$ROTATION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['zombie-pc'])")
BACKUP_TOKEN=$(echo "$ROTATION_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['backup'])")
FED_SECRET=$(echo "$ROTATION_JSON"  | python3 -c "import json,sys; print(json.load(sys.stdin)['_secret'])")

# .env auf Remote-Node patchen + triforce neustarten
deploy_token() {
    local SSH_HOST="$1" NODE_ID="$2" TOKEN="$3" ENV_FILE="$4"
    log "Deploying $NODE_ID → $SSH_HOST ..."

    ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes \
        -o StrictHostKeyChecking=accept-new "$SSH_HOST" \
        "python3 -c \"
import re
with open('$ENV_FILE') as f: c = f.read()
updates = {'FEDERATION_NODE_ID': '$NODE_ID', 'FEDERATION_SECRET': '$FED_SECRET', 'FEDERATION_TOKEN': '$TOKEN'}
for k, v in updates.items():
    if re.search(rf'^{k}=.*', c, re.MULTILINE): c = re.sub(rf'^{k}=.*', f'{k}={v}', c, flags=re.MULTILINE)
    else: c += f'\n{k}={v}'
with open('$ENV_FILE', 'w') as f: f.write(c)
print('patched')
\"" 2>&1 | while IFS= read -r line; do log "  [$SSH_HOST] $line"; done

    # Node neustarten — HETZNER wird NICHT neugestartet
    ssh -i "$SSH_KEY" -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" \
        "sudo systemctl restart triforce" \
        && log "$SSH_HOST: triforce restarted OK" \
        || { err "$SSH_HOST: restart fehlgeschlagen"; return 1; }

    # Kurz warten damit Node Zeit hat aufzukommen
    sleep 15
    log "$SSH_HOST: deployment complete"
}

ERRORS=0
deploy_token "zombie@10.10.0.2"     "zombie-pc" "$ZOMBIE_TOKEN" "/home/zombie/workspace/triforce/config/triforce.env" || ERRORS=$((ERRORS+1))
deploy_token "zombie@10.10.0.3" "backup"    "$BACKUP_TOKEN" "/home/zombie/workspace/triforce/config/triforce.env" || ERRORS=$((ERRORS+1))

# Hetzner braucht KEINEN Neustart — Vault lädt dynamisch bei verify_token()
log "Vault bereits aktuell — kein Hetzner-Restart nötig"

if [[ "$ERRORS" -eq 0 ]]; then
    notify "Federation Rotation OK ✅" "zombie-pc + backup rotiert. Hetzner-Vault aktuell." "low"
    log "=== Rotation abgeschlossen: ALLE OK ==="
else
    notify "Federation Rotation FEHLER ❌" "$ERRORS Node(s) fehlgeschlagen — Log: $LOG" "high"
    log "=== Rotation abgeschlossen: $ERRORS FEHLER ==="
    exit 1
fi
