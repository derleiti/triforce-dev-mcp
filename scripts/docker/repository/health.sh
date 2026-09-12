#!/usr/bin/env bash
# AILinux Repository Healthcheck v2 (Auto-Deps, Cert-Discovery, Stale/By-Hash, IPv6, robust curl)
# Date: 2025-09-25

set -euo pipefail

# ===== Konfiguration =====
DOMAINS=("ailinux.me" "repo.ailinux.me")
ORIGIN_IP="${ORIGIN_IP:-}"
# WICHTIG: ORIGIN_IP muss als Umgebungsvariable gesetzt werden!
ORIGIN_PORT="${ORIGIN_PORT:-8443}"

# Test-URL zu einer Release-Datei deines Repos (über Cloudflare)
CHECK_URL="${CHECK_URL:-https://repo.ailinux.me:8443/mirror/archive.ailinux.me/dists/stable/Release}"

# Zertifikats-Trust
MIN_CERT_DAYS="${MIN_CERT_DAYS:-14}"
TRUSTED_ISSUERS=("Cloudflare" "Let's Encrypt" "Google Trust Services")

# Zertifikate: Entweder direkt angeben ODER Auto-Discovery aus NGINX-Konfigs
# Beispiel: CERT_FILES=("/etc/ssl/certs/fullchain.pem" "/etc/ssl/private/ailinux_fullchain.pem")
: "${CERT_FILES:=()}"
# In welchen Dateien nach ssl_certificate suchen (Globs erlaubt)
NGINX_CONF_GLOBS=(
  "${NGINX_CONF:-/etc/nginx/nginx.conf}"
  "/etc/nginx/conf.d/*.conf"
  "/etc/nginx/sites-enabled/*"
  "/root/ailinux-repo/nginx.conf"
)

# APT/Installationsverhalten
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a

# ===== Farben & Symbole =====
C_GREEN="\033[0;32m"; C_RED="\033[0;31m"; C_YELLOW="\033[0;33m"; C_RESET="\033[0m"
ok()   { echo -e "${C_GREEN}✅ OK:      ${*}${C_RESET}"; }
bad()  { echo -e "${C_RED}❌ FEHLER:  ${*}${C_RESET}"; STATUS=$(( STATUS | 1 )); }
warn() { echo -e "${C_YELLOW}⚠  WARNUNG: ${*}${C_RESET}"; STATUS=$(( STATUS | 2 )); }
info() { echo -e ">> $*"; }

# ===== Curl Optionen (robust) =====
CURL_OPTS=(--max-time 12 --connect-timeout 5 --retry 2 --retry-delay 1 --retry-connrefused -fSsL)

# ===== Preflight: Root & Abhängigkeiten =====
require_root() {
  if [ "${EUID:-$(id -u)}" -ne 0 ]; then
    info "Starte neu mit Root-Rechten..."
    exec sudo -E bash "$0" "$@"
  fi
}
install_missing_tools() {
  local missing=()
  command -v curl >/dev/null    || missing+=("curl")
  command -v openssl >/dev/null || missing+=("openssl")
  command -v dig >/dev/null 2>&1 || missing+=("bind9-dnsutils")
  if [ "${#missing[@]}" -gt 0 ]; then
    info "Installiere fehlende Pakete: ${missing[*]}"
    apt-get update -y
    if ! apt-get install -y "${missing[@]}"; then
      command -v dig >/dev/null 2>&1 || apt-get install -y dnsutils || true
    fi
    for c in curl openssl dig; do
      command -v "$c" >/dev/null 2>&1 || { bad "'$c' konnte nicht installiert werden."; exit 1; }
    done
  fi
}

# ===== Helpers =====
need() {
  command -v "$1" >/dev/null 2>&1 || { bad "Kommando '$1' nicht gefunden (sollte installiert sein)."; exit 1; }
}
uniq_lines() { awk '!seen[$0]++'; }

discover_certs_from_nginx() {
  local files=()
  shopt -s nullglob
  for pat in "${NGINX_CONF_GLOBS[@]}"; do
    for f in $pat; do
      [ -f "$f" ] || continue
      # grep ssl_certificate (nicht *_key), fange Pfad in 1. Gruppe
      while IFS= read -r p; do
        # Bereinigen (Semikolon, Quotes)
        p="${p%;}
"; p="${p//\"/}"; p="${p//\'/}"
        [ -f "$p" ] && files+=("$p")
      done < <(grep -Eo 'ssl_certificate\s+[^;]+' "$f" | awk '{print $2}')
    done
  done
  printf '%s\n' "${files[@]}" | uniq_lines
}

print_cert_file_info() {
  local file=$1
  if [ ! -f "$file" ]; then warn "Zertifikat nicht gefunden: $file"; return; fi

  local size mtime subject issuer end_date_str end_epoch now_epoch days_left fp
  size=$(stat -c %s "$file" 2>/dev/null || echo "?")
  mtime=$(date -R -r "$file" 2>/dev/null || echo "?")
  subject=$(openssl x509 -in "$file" -noout -subject 2>/dev/null | sed 's/^subject= //')
  issuer=$(openssl x509 -in "$file" -noout -issuer  2>/dev/null | sed 's/^issuer= //')
  end_date_str=$(openssl x509 -in "$file" -noout -enddate 2>/dev/null | cut -d= -f2)
  fp=$(openssl x509 -in "$file" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)

  echo "---- Zertifikat: $file"
  echo "     Größe:      ${size} B"
  echo "     mtime:      $mtime"
  echo "     Subject:    ${subject:-?}"
  echo "     Issuer:     ${issuer:-?}"
  echo "     SHA256 FP:  ${fp:-?}"
  if [ -n "${end_date_str:-}" ]; then
    end_epoch=$(date -d "$end_date_str" +%s 2>/dev/null || echo "")
    now_epoch=$(date +%s)
    if [ -n "$end_epoch" ]; then
      days_left=$(((end_epoch - now_epoch)/86400))
      echo "     Gültig bis: $end_date_str  (${days_left} Tage)"
      if [ "$days_left" -lt 0 ]; then
        bad "Zertifikat abgelaufen: $file"
      elif [ "$days_left" -lt "$MIN_CERT_DAYS" ]; then
        warn "Läuft bald ab (${days_left} Tage): $file"
      else
        ok "Zertifikat okay (${days_left} Tage): $file"
      fi
    else
      warn "Ablaufdatum konnte nicht geparst werden: $file"
    fi
  else
    warn "Kein Ablaufdatum lesbar: $file"
  fi
}

check_dns() {
  local domain=$1
  info "Prüfe DNS für $domain…"
  local a aaaa; a=$(dig +short "$domain" A || true); aaaa=$(dig +short "$domain" AAAA || true)
  if [ -z "$a$aaaa" ]; then bad "Keine A/AAAA-Records für $domain."; return; fi
  [ -n "$a" ] && while read -r ip; do [ -z "$ip" ]|| ok "$domain A: $ip"; done <<<"$a"
  [ -n "$aaaa" ] && while read -r ip6; do [ -z "$ip6" ]|| ok "$domain AAAA: $ip6"; done <<<"$aaaa"
}

check_cert_port() {
  local domain=$1 port=$2
  info "Prüfe SSL-Zertifikat live für $domain:$port…"
  local cert; cert=$(echo | timeout 8 openssl s_client -servername "$domain" -connect "$domain:$port" 2>/dev/null | openssl x509 -noout -issuer -enddate) || true
  if [ -z "$cert" ]; then bad "Keine Zertifikatsdaten von $domain:$port erhalten."; return; fi
  local issuer end_date_str end_epoch now_epoch days_left trusted=false
  issuer=$(sed -n 's/^issuer=\(.*\)$/\1/p' <<<"$cert")
  end_date_str=$(sed -n 's/^notAfter=\(.*\)$/\1/p' <<<"$cert")
  for t in "${TRUSTED_ISSUERS[@]}"; do [[ "$issuer" == *"$t"* ]] && trusted=true && break; done
  [[ $trusted == true ]] && ok "Aussteller vertrauenswürdig: $issuer" || warn "Unerwarteter Aussteller: $issuer"
  end_epoch=$(date -d "$end_date_str" +%s 2>/dev/null || echo "")
  now_epoch=$(date +%s)
  if [ -n "$end_epoch" ]; then
    days_left=$(((end_epoch - now_epoch)/86400))
    if [ "$days_left" -lt 0 ]; then bad "Zertifikat abgelaufen! (bis: $end_date_str)"
    elif [ "$days_left" -lt "$MIN_CERT_DAYS" ]; then warn "Zertifikat läuft bald ab: $days_left Tage (bis: $end_date_str)"
    else ok "Zertifikat gültig: $days_left Tage (bis: $end_date_str)"; fi
  else
    warn "Ablaufdatum live nicht auswertbar."
  fi
}

check_origin() {
  info "Prüfe direkte Verbindung zum Origin ($ORIGIN_IP:$ORIGIN_PORT)…"
  if curl --resolve "repo.ailinux.me:$ORIGIN_PORT:$ORIGIN_IP" -k "${CURL_OPTS[@]}" "https://repo.ailinux.me:$ORIGIN_PORT/" -o /dev/null; then
    ok "Origin-Server ist erreichbar."
  else
    bad "Origin $ORIGIN_IP:$ORIGIN_PORT antwortet nicht wie erwartet."
  fi
}

check_via_cf_headers() {
  local url=$1
  info "Prüfe Cloudflare-Header für $url…"
  local headers; headers=$(curl -I "${CURL_OPTS[@]}" "$url" || true)
  if echo "$headers" | grep -qiE '^cf-ray:|^server:\s*cloudflare'; then
    ok "Cloudflare-Proxy aktiv (CF-Header erkannt)."
  else
    warn "Keine klaren Cloudflare-Header erkannt."
  fi
}

check_mirror_content() {
  info "Prüfe Erreichbarkeit der Release-Datei…"
  local code; code=$(curl "${CURL_OPTS[@]}" -o /dev/null -w "%{http_code}" "$CHECK_URL" || echo "000")
  [ "$code" = "200" ] && ok "Test-URL erreichbar (HTTP 200)." || bad "Test-URL NICHT erreichbar (HTTP $code)."
  check_via_cf_headers "$CHECK_URL"
}

check_byhash() {
  local base="${CHECK_URL%/dists/*}/dists/stable/by-hash/SHA256"
  info "Prüfe By-Hash-Verzeichnis…"
  if curl -I "${CURL_OPTS[@]}" "$base/" >/dev/null 2>&1; then
    ok "By-Hash erreichbar."
  else
    warn "By-Hash nicht erreichbar – bitte Webserver/Sync prüfen."
  fi
}

check_stale_release() {
  info "Prüfe Alter der Release-Datei…"
  local lm; lm=$(curl -sI "${CURL_OPTS[@]}" "$CHECK_URL" | awk -F': ' 'tolower($1)=="last-modified"{print $2}' | tr -d '\r')
  if [ -n "$lm" ]; then
    local lm_epoch now_epoch diff_h
    lm_epoch=$(date -d "$lm" +%s); now_epoch=$(date +%s); diff_h=$(( (now_epoch - lm_epoch) / 3600 ))
    if [ "$diff_h" -gt 12 ]; then warn "Release zuletzt vor ${diff_h}h aktualisiert."
    else ok "Release frisch (vor ${diff_h}h)."; fi
  else
    warn "Kein Last-Modified-Header gefunden."
  fi
}

check_ipv6() {
  info "IPv6-Check auf $CHECK_URL…"
  if curl -6 -I "${CURL_OPTS[@]}" "$CHECK_URL" >/dev/null 2>&1; then
    ok "IPv6 erreichbar."
  else
    warn "IPv6 nicht erreichbar oder geblockt."
  fi
}

# ===== Hauptprogramm =====
STATUS=0
[ -e healt.sh ] || ln -sf "$(basename "$0")" healt.sh 2>/dev/null || true

echo "=== AILinux Repository Healthcheck ==="
date -R
echo "--------------------------------------"

require_root "$@"
install_missing_tools
need dig; need openssl; need curl

# 1) DNS + Live-Zertifikate
for d in "${DOMAINS[@]}"; do
  check_dns "$d"
  check_cert_port "$d" 443
done
check_cert_port "repo.ailinux.me" "$ORIGIN_PORT"

# 2) Origin + Mirror-Content
check_origin
check_mirror_content
check_byhash
check_stale_release
check_ipv6

# 3) Zertifikat-Dateien aus Config anzeigen
echo "--------------------------------------"
info "Zertifikate aus Konfiguration / Auto-Discovery:"
declare -a ALL_CERTS=()
if [ "${#CERT_FILES[@]}" -gt 0 ]; then
  ALL_CERTS+=("${CERT_FILES[@]}")
fi
while IFS= read -r cf; do
  ALL_CERTS+=("$cf")
done < <(discover_certs_from_nginx || true)

# deduplizieren
mapfile -t ALL_CERTS < <(printf '%s\n' "${ALL_CERTS[@]}" | uniq_lines)

if [ "${#ALL_CERTS[@]}" -eq 0 ]; then
  warn "Keine Zertifikatsdateien gefunden. Setze CERT_FILES oder prüfe NGINX_CONF_GLOBS."
else
  for certf in "${ALL_CERTS[@]}"; do
    print_cert_file_info "$certf"
  done
fi

echo "--------------------------------------"
case "$STATUS" in
  0) ok "System-Health: Alles in Ordnung." ;;
  1) bad "System-Health: FEHLER erkannt." ;;
  2) warn "System-Health: WARNUNGEN erkannt." ;;
  3) bad "System-Health: FEHLER & WARNUNGEN erkannt." ;;
esac

exit $STATUS


