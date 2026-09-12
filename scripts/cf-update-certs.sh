#!/usr/bin/env bash
set -Eeuo pipefail

# ================================================================
# AILinux Cloudflare + Let's Encrypt Certificate Manager
# - Ubuntu 26.04 / APT only -- NO SNAP
# - DNS-01 via Cloudflare
# - Certificate lineage: ailinux.me
# - Domains: ailinux.me + *.ailinux.me
# - Reloads Docker Apache after successful renewal
# ================================================================

LOG_DIR="/var/log/cf-le"
LOG_FILE="${LOG_DIR}/cf-update-certs.log"

CERT_NAME="${CERT_NAME:-ailinux.me}"
DOMAINS=(
  "ailinux.me"
  "*.ailinux.me"
)

CRED_FILE="/etc/letsencrypt/cloudflare.ini"

ENV_CANDIDATES=(
  "/home/zombie/triforce/config/triforce.env"
  "/home/zombie/triforce/.env"
  "/home/zombie/scripts/.env"
  "/etc/ailinux.env"
)


log() {
  printf '[CF-LE] %s\n' "$*" | tee -a "$LOG_FILE"
}

warn() {
  printf '[CF-LE][WARN] %s\n' "$*" | tee -a "$LOG_FILE" >&2
}

die() {
  printf '[CF-LE][ERR] %s\n' "$*" | tee -a "$LOG_FILE" >&2
  exit 1
}

require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Bitte mit sudo/root ausführen."
}

init_log() {
  install -d -m 0750 "$LOG_DIR"
  touch "$LOG_FILE"
  chmod 0640 "$LOG_FILE"
  log "============================================================"
  log "Start: $(date -Iseconds)"
}

load_env() {
  local env_file=""

  for candidate in "${ENV_CANDIDATES[@]}"; do
    if [[ -f "$candidate" ]]; then
      env_file="$candidate"
      break
    fi
  done

  if [[ -n "$env_file" ]]; then
    log "Lade Environment aus: $env_file"
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  else
    warn "Keine Environment-Datei gefunden; vorhandene Cloudflare-Credentials werden verwendet."
  fi

  # Unterstützte Alias-Namen
  CF_API_TOKEN="${CF_API_TOKEN:-${CLOUDFLARE_API_TOKEN:-}}"
  CF_EMAIL="${CF_EMAIL:-${CLOUDFLARE_EMAIL:-}}"
  CF_GLOBAL_API_KEY="${CF_GLOBAL_API_KEY:-${CLOUDFLARE_GLOBAL_API_KEY:-${CLOUDFLARE_API_KEY:-}}}"

  LE_EMAIL="${LETSENCRYPT_EMAIL:-${CF_EMAIL:-admin@ailinux.me}}"
}

ensure_packages() {
  if command -v certbot >/dev/null 2>&1 \
     && certbot plugins 2>/dev/null | grep -q 'dns-cloudflare'; then
    log "Certbot + dns-cloudflare bereits vorhanden."
    return
  fi

  log "Installiere Certbot + Cloudflare-Plugin via APT..."

  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    certbot \
    python3-certbot-dns-cloudflare

  command -v certbot >/dev/null 2>&1 \
    || die "Certbot wurde nicht korrekt installiert."

  certbot plugins 2>/dev/null | grep -q 'dns-cloudflare' \
    || die "dns-cloudflare Plugin fehlt."
}

credentials_valid() {
  [[ -s "$CRED_FILE" ]] || return 1

  grep -Eq '^[[:space:]]*dns_cloudflare_api_token[[:space:]]*=' "$CRED_FILE" \
    && return 0

  grep -Eq '^[[:space:]]*dns_cloudflare_api_key[[:space:]]*=' "$CRED_FILE" \
    && grep -Eq '^[[:space:]]*dns_cloudflare_email[[:space:]]*=' "$CRED_FILE" \
    && return 0

  return 1
}

setup_credentials() {
  install -d -m 0700 /etc/letsencrypt

  if [[ -n "${CF_API_TOKEN:-}" ]]; then
    log "Aktualisiere Cloudflare-Credentials aus API Token."
    umask 077
    printf 'dns_cloudflare_api_token = %s\n' "$CF_API_TOKEN" > "$CRED_FILE"

  elif [[ -n "${CF_EMAIL:-}" && -n "${CF_GLOBAL_API_KEY:-}" ]]; then
    log "Aktualisiere Cloudflare-Credentials aus Global API Key."
    umask 077
    {
      printf 'dns_cloudflare_email = %s\n' "$CF_EMAIL"
      printf 'dns_cloudflare_api_key = %s\n' "$CF_GLOBAL_API_KEY"
    } > "$CRED_FILE"

  elif credentials_valid; then
    log "Verwende vorhandene Cloudflare-Credentials: $CRED_FILE"

  else
    die "Keine nutzbaren Cloudflare-Credentials gefunden."
  fi

  chown root:root "$CRED_FILE"
  chmod 0600 "$CRED_FILE"
}

install_deploy_hook() {
  local hook="/etc/letsencrypt/renewal-hooks/deploy/20-ailinux-services.sh"

  install -d -m 0755 "$(dirname "$hook")"

  cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
set -u

COMPOSE="/home/zombie/triforce/docker/wordpress/docker-compose.yml"

logger -t ailinux-certbot "Let's Encrypt certificate deployed"

if command -v docker >/dev/null 2>&1 && [[ -f "$COMPOSE" ]]; then
  docker compose -f "$COMPOSE" restart apache \
    || logger -t ailinux-certbot "WARNING: WordPress Apache restart failed"
fi

for service in postfix dovecot; do
  if systemctl is-active --quiet "$service" 2>/dev/null; then
    systemctl reload "$service" \
      || systemctl restart "$service" \
      || true
  fi
done
HOOK

  chmod 0755 "$hook"
}

request_or_renew() {
  local args=()

  for domain in "${DOMAINS[@]}"; do
    args+=(-d "$domain")
  done

  log "Prüfe Zertifikat: ${DOMAINS[*]}"

  certbot certonly \
    --cert-name "$CERT_NAME" \
    --dns-cloudflare \
    --dns-cloudflare-credentials "$CRED_FILE" \
    --dns-cloudflare-propagation-seconds 30 \
    --preferred-challenges dns \
    --agree-tos \
    --non-interactive \
    --keep-until-expiring \
    --email "$LE_EMAIL" \
    "${args[@]}"

  [[ -s "/etc/letsencrypt/live/${CERT_NAME}/fullchain.pem" ]] \
    || die "fullchain.pem fehlt nach Certbot-Lauf."

  [[ -s "/etc/letsencrypt/live/${CERT_NAME}/privkey.pem" ]] \
    || die "privkey.pem fehlt nach Certbot-Lauf."

  log "Zertifikat vorhanden: /etc/letsencrypt/live/${CERT_NAME}/"
}

show_expiry() {
  if command -v openssl >/dev/null 2>&1; then
    local expiry
    expiry="$(
      openssl x509 \
        -in "/etc/letsencrypt/live/${CERT_NAME}/cert.pem" \
        -noout -enddate 2>/dev/null || true
    )"
    [[ -n "$expiry" ]] && log "$expiry"
  fi
}

main() {
  require_root
  init_log
  load_env
  ensure_packages
  setup_credentials
  install_deploy_hook

  case "${1:-}" in
    --dry-run)
      log "Starte Certbot Renewal Dry-Run."
      certbot renew --dry-run
      ;;
    *)
      request_or_renew
      show_expiry
      ;;
  esac

  log "Fertig: $(date -Iseconds)"
}

main "$@"
