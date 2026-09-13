#!/bin/bash
# Rotiere Gemini/Google API Key — Single Source via AI Studio
# Usage: bash rotate-gemini-key.sh
set -e

ENV_FILE="/home/zombie/workspace/triforce/config/triforce.env"

echo "=== Gemini API Key Rotation ==="
echo
echo "1) Öffne https://aistudio.google.com/apikey im Browser"
echo "2) Create API key (in einem Google-Cloud-Project deiner Wahl)"
echo "3) Key (AIzaSy…, 39 Zeichen) hier einfügen — wird NICHT angezeigt:"
echo
read -s -p "API Key: " NEW
echo
echo

# Validate format
if [[ ! "$NEW" =~ ^AIzaSy[A-Za-z0-9_-]{33}$ ]]; then
  echo "❌ Format passt nicht (erwartet: AIzaSy + 33 Zeichen). Abbruch."
  exit 1
fi

# Live test
echo "→ Teste Key gegen Gemini API…"
HTTP=$(curl -sS -o /tmp/gemini-test.json -w "%{http_code}" "https://generativelanguage.googleapis.com/v1beta/models?key=${NEW}" --max-time 10)
if [ "$HTTP" != "200" ]; then
  ERR=$(python3 -c "import json;d=json.load(open('/tmp/gemini-test.json'));print(d.get('error',{}).get('message','?'))" 2>/dev/null)
  echo "❌ HTTP $HTTP: $ERR"
  rm -f /tmp/gemini-test.json
  exit 2
fi
CNT=$(python3 -c "import json;d=json.load(open('/tmp/gemini-test.json'));print(len(d.get('models',[])))")
echo "✓ HTTP 200 — $CNT Modelle entdeckt"
rm -f /tmp/gemini-test.json

# Backup
BAK="${ENV_FILE}.bak.before-gemini-rotate-$(date +%Y%m%d-%H%M%S)"
sudo cp "$ENV_FILE" "$BAK"
echo "✓ Backup: $BAK"

# Update — sed verwendet | als delimiter, sicher weil AIza-Keys nur [A-Za-z0-9_-] enthalten
for var in GEMINI_API_KEY GOOGLE_AI_STUDIO_KEY GOOGLE_GEMINI_KEY; do
  sudo sed -i "s|^${var}=.*|${var}=${NEW}|" "$ENV_FILE"
  echo "✓ $var aktualisiert"
done

# Verify
echo
echo "=== Verify im env-file ==="
grep -E "^(GEMINI_API_KEY|GOOGLE_AI_STUDIO_KEY|GOOGLE_GEMINI_KEY)=" "$ENV_FILE" | sed -E 's/(=AIzaSy)([A-Za-z0-9_-]{6}).*$/\1\2…/'

echo
echo "→ TriForce restart…"
sudo systemctl restart triforce
sleep 8
systemctl is-active triforce | xargs echo "TriForce status:"
echo
echo "→ Verify in laufenden Logs (5 sec):"
sudo journalctl -u triforce --since '8 sec ago' --no-pager | grep -iE 'gemini|google' | tail -5
echo
echo "✓ Fertig. Jetzt:"
echo "  1) Neuen Key in KeePass speichern"
echo "  2) Alte 3 obsoleten Keys in Google Cloud Console löschen:"
echo "     https://console.cloud.google.com/apis/credentials"
