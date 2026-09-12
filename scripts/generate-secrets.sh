#!/usr/bin/env bash
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Secret Generator für TriForce .env
# Ersetzt __GENERATE_SECRET_XX__ Platzhalter mit zufälligen Werten
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

set -e

ENV_FILE="${1:-config/triforce.env}"
TEMPLATE_FILE="${2:-config/triforce.env.template}"

# Farben
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Secret Generator
generate_secret() {
    local length="${1:-32}"
    openssl rand -base64 48 | tr -d '/+=' | head -c "$length"
}

# Prüfe ob Template existiert
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    log_error "Template nicht gefunden: $TEMPLATE_FILE"
    exit 1
fi

# Kopiere Template -> .env (falls nicht existiert oder --force)
if [[ ! -f "$ENV_FILE" ]] || [[ "$3" == "--force" ]]; then
    log_info "Erstelle $ENV_FILE aus Template..."
    cp "$TEMPLATE_FILE" "$ENV_FILE"
else
    log_warn "$ENV_FILE existiert bereits. Nutze --force zum Überschreiben."
fi

# Ersetze Platzhalter
log_info "Generiere Secrets..."

# __GENERATE_SECRET_32__ → 32 Zeichen
while grep -q "__GENERATE_SECRET_32__" "$ENV_FILE"; do
    SECRET=$(generate_secret 32)
    sed -i "0,/__GENERATE_SECRET_32__/s/__GENERATE_SECRET_32__/$SECRET/" "$ENV_FILE"
done

# __GENERATE_SECRET_16__ → 16 Zeichen
while grep -q "__GENERATE_SECRET_16__" "$ENV_FILE"; do
    SECRET=$(generate_secret 16)
    sed -i "0,/__GENERATE_SECRET_16__/s/__GENERATE_SECRET_16__/$SECRET/" "$ENV_FILE"
done

# Zähle generierte Secrets

log_info "✅ Secrets generiert!"
log_info "   Datei: $ENV_FILE"
log_info "   Variablen: $(grep -c '^[A-Z]' "$ENV_FILE")"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "⚠️  WICHTIG: Passe folgende Werte an:"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DOMAIN=example.com         → Deine Domain"
echo "  GEMINI_API_KEY=            → API Key eintragen"
echo "  ANTHROPIC_API_KEY=         → API Key eintragen"
echo "  GITHUB_TOKEN=              → GitHub Token"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
