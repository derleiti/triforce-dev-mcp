#!/usr/bin/env bash
# dep-heal.sh — Repariert APT/DPKG-Abhängigkeitsbrüche auf Ubuntu 24.04 (+ Neon-Mix)
# - keine Abhängigkeit auf lsb_release, fuser, lsof, awk etc.
# - vorsichtige Defaults (keine Recommends), Logfile & Backups
# - optional aggressiver Modus mit --force-overwrite
# - Spezialbehandlung für kglobalacceld ↔ libkf5globalaccel-bin

set -euo pipefail

DRY_RUN=0
DO_DIST=1
AGGRESSIVE=0
ALLOW_DESKTOP=0   # Server-freundlich per Default (keine Recommends)
AUTOREMOVE=0       # Destruktives Cleanup nur explizit
APT_COMMON_OPTS=(-y -o Dpkg::Options::=--force-confnew -o APT::Install-Recommends=false -o APT::Install-Suggests=false)

for a in "$@"; do
  case "$a" in
    --dry-run) DRY_RUN=1 ;;
    --no-dist) DO_DIST=0 ;;
    --aggressive) AGGRESSIVE=1 ;;
    --desktop-allow) ALLOW_DESKTOP=1 ;; # erlaubt Recommends (Plasma/Qt zieht breiter)
    --autoremove) AUTOREMOVE=1 ;;
    *) echo "Unbekannte Option: $a" >&2; exit 2 ;;
  esac
done

if [ "$ALLOW_DESKTOP" -eq 1 ]; then
  APT_COMMON_OPTS=(-y -o Dpkg::Options::=--force-confnew)
fi

APT_COMMON_STR=$(printf " %q" "${APT_COMMON_OPTS[@]}")
APT_COMMON_STR=${APT_COMMON_STR# }

log() { printf "\033[1;34m[dep-heal]\033[0m %s\n" "$*"; }
# run() intentionally accepts logged command strings for dry-run parity.
# shellcheck disable=SC2294
run() { if [ "$DRY_RUN" -eq 1 ]; then log "(dry-run) $*"; else eval "$@"; fi; }

[ "$(id -u)" -eq 0 ] || { echo "Bitte als root ausführen."; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BK="/root/apt-backup-$STAMP"
LOG="/var/log/dep-heal-$STAMP.log"
run "mkdir -p '$BK'"
run "cp -a /etc/apt/sources.list /etc/apt/sources.list.d '$BK' 2>/dev/null || true"
run "touch '$LOG'"

log "Backups in $BK, Log: $LOG"

# 1) Update & Grundaufräumung
log "Cache bereinigen und Paketlisten aktualisieren"
run "apt-get clean"
run "apt-get update 2>&1 | tee -a '$LOG'"

# 2) Hängende Konfigurationen fertigstellen
log "dpkg --configure -a"
run "dpkg --configure -a 2>&1 | tee -a '$LOG' || true"

# 3) Basis-Fix
log "apt-get -f install (Basisfix)"
set +e
apt-get "${APT_COMMON_OPTS[@]}" -f install 2>&1 | tee -a "$LOG"
EC=$?
set -e

if [ $EC -ne 0 ] && [ "$AGGRESSIVE" -eq 1 ]; then
  log "Fehler erkannt – versuche erneut mit --force-overwrite"
  run "apt-get $APT_COMMON_STR -o Dpkg::Options::=--force-overwrite -f install 2>&1 | tee -a '$LOG'"
fi

# 4) Spezieller Konfliktfix: libkf5globalaccel-bin ↔ kglobalacceld
has_pkg() { dpkg -l 2>/dev/null | awk 'NR>5 {print $2" "$1}' | grep -E "^$1 " >/dev/null 2>&1; }
if dpkg -l 2>/dev/null | grep -q '^.i  libkf5globalaccel-bin'; then
  if apt-cache policy kglobalacceld | grep -q 'Candidate:'; then
    log "Konfliktverdacht: libkf5globalaccel-bin ↔ kglobalacceld – entferne Altpaket und installiere neues."
    run "apt-mark unhold libkf5globalaccel-bin 2>/dev/null || true"
    run "apt-get -y remove libkf5globalaccel-bin 2>&1 | tee -a '$LOG' || true"
    if [ "$AGGRESSIVE" -eq 1 ]; then
      run "apt-get $APT_COMMON_STR -o Dpkg::Options::=--force-overwrite install kglobalacceld 2>&1 | tee -a '$LOG' || true"
    else
      run "apt-get $APT_COMMON_STR install kglobalacceld 2>&1 | tee -a '$LOG' || true"
    fi
    run "dpkg --configure -a 2>&1 | tee -a '$LOG' || true"
    run "apt-get $APT_COMMON_STR -f install 2>&1 | tee -a '$LOG'"
  fi
fi

# 5) Optional: Voll-Upgrade zum Glätten halber Stände
if [ "$DO_DIST" -eq 1 ]; then
  log "dist-upgrade (kann große Stände harmonisieren)"
  if [ "$AGGRESSIVE" -eq 1 ]; then
    run "apt-get $APT_COMMON_STR -o Dpkg::Options::=--force-overwrite dist-upgrade 2>&1 | tee -a '$LOG'"
  else
    run "apt-get $APT_COMMON_STR dist-upgrade 2>&1 | tee -a '$LOG'"
  fi
fi

# 6) Aufräumen & Abschluss
if [ "$AUTOREMOVE" -eq 1 ]; then
  log "autoremove --purge (explizit angefordert)"
  run "apt-get -y autoremove --purge 2>&1 | tee -a '$LOG' || true"
else
  log "autoremove übersprungen (mit --autoremove aktivieren)"
fi
run "apt-get $APT_COMMON_STR -f install 2>&1 | tee -a '$LOG' || true"

log "dpkg --audit (Prüfung)"
run "dpkg --audit || true"

log "Fertig. Details: $LOG"
