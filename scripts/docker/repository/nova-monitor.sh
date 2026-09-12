#!/usr/bin/env bash
set -euo pipefail

# 🧠 Nova AI Repo Monitor v2
# Übersicht, Healthcheck, Farben, AI Style

REPO_BASE="/root/ailinux-repo/repo/mirror"
LOG_PATH=$(find "$REPO_BASE" -name "live-log-*.txt" -print0 | xargs -0 ls -1t 2>/dev/null | head -n1 || true)
SIGN_COUNT=$(find "$REPO_BASE" -name InRelease | wc -l)
MISSING_INRELEASE=$(find "$REPO_BASE" -type d -exec test ! -f "{}/InRelease" \; -print)
DISK_USAGE=$(du -sh "$REPO_BASE" 2>/dev/null | awk '{print $1}')

CYAN="\e[36m"
GREEN="\e[32m"
YELLOW="\e[33m"
RED="\e[31m"
RESET="\e[0m"

echo -e "${CYAN}===[ Nova AI Repo Monitor ]==="
echo "📅 Datum: $(date '+%Y-%m-%d %H:%M:%S')"
echo "📁 Mirror Pfad: $REPO_BASE"
echo "🧠 Live-Log: ${LOG_PATH:-Kein Log gefunden}"
echo -e "=====================================${RESET}"
echo ""

echo -e "${CYAN}--- Signaturprüfung ---${RESET}"
echo -e "✅ InRelease-Dateien gefunden: ${GREEN}${SIGN_COUNT}${RESET}"
echo ""

echo -e "${YELLOW}--- Fehlende InRelease Dateien ---${RESET}"
if [ -z "$MISSING_INRELEASE" ]; then
  echo -e "${GREEN}✓ Alle Verzeichnisse korrekt signiert.${RESET}"
else
  echo "$MISSING_INRELEASE"
fi
echo ""

echo -e "${CYAN}--- Letzte Log-Zeilen ---${RESET}"
if [ -n "${LOG_PATH:-}" ] && [ -f "$LOG_PATH" ]; then
  tail -n 20 "$LOG_PATH"
else
  echo -e "${RED}⚠ Kein gültiges Live-Log vorhanden.${RESET}"
fi
echo ""

echo -e "${CYAN}--- Speicherplatz Mirror ---${RESET}"
echo -e "💾 Verwendet: ${DISK_USAGE:-Unbekannt}"
echo ""

echo -e "${CYAN}--- Nova KI Systemstatus ---${RESET}"
if [ "$SIGN_COUNT" -gt 80 ]; then
  echo -e "${GREEN}🧠 Nova sagt: SYSTEM CLEAN & OPTIMIZED!${RESET}"
else
  echo -e "${RED}⚠ Nova sagt: HEAL EMPFOHLEN!${RESET}"
fi

echo ""


