#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_PATH="${REPO_PATH:-$SCRIPT_DIR}"
MIRROR_PATH="${MIRROR_PATH:-$REPO_PATH/repo/mirror}"
LOGFILE="${LOGFILE:-/var/log/ailinux/postmirror.log}"
SUMMARY="${SUMMARY:-$MIRROR_PATH/mirror-summary.html}"
mkdir -p "$MIRROR_PATH"
html_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
HEALTH_OUT=""
if [[ -x "$REPO_PATH/repo-health.sh" ]]; then HEALTH_OUT="$("$REPO_PATH/repo-health.sh" "$MIRROR_PATH" 2>/dev/null || true)"; fi
{
  echo "<!doctype html><html lang='de'><head><meta charset='utf-8'><title>AILinux Mirror - Summary</title></head><body>"
  echo "<h1>AILinux Mirror - Zusammenfassung</h1><p>Stand: $(date '+%Y-%m-%d %H:%M:%S')</p>"
  echo "<h2>Signatur-Status</h2><pre><code>"
  if [[ -n "$HEALTH_OUT" ]]; then printf "%s\n" "$HEALTH_OUT" | html_escape; else echo "Kein repo-health Output verfuegbar."; fi
  echo "</code></pre><h2>Letzte Ereignisse</h2><pre><code>"
  if [[ -f "$LOGFILE" ]]; then tail -n 200 "$LOGFILE" | html_escape; else echo "Kein postmirror.log vorhanden."; fi
  echo "</code></pre></body></html>"
} > "$SUMMARY"
echo "Summary written: $SUMMARY"
