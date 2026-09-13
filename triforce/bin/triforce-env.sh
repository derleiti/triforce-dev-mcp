#!/usr/bin/env bash
# TriForce Agent Environment Loader v3.0
# Sourced by all agent wrappers — nicht direkt ausfuehren
#
# v3.0 Änderungen:
#   - Account-Auth bevorzugt (OAuth-Token statt API-Key)
#   - API-Keys werden NICHT an CLI-Tools weitergegeben
#     (Claude/Gemini/Codex nutzen gespeicherte OAuth-Credentials)
#   - npm auto-update: einmal pro 24h pro Tool

export HOME=/home/zombie
export PATH="/home/zombie/.npm-global/bin:/usr/local/bin:/usr/bin:/bin"
export NODE_PATH="/home/zombie/.npm-global/lib/node_modules"

# XDG-Dirs: CLI-Tools finden Account-Credentials unter HOME
export XDG_CONFIG_HOME="/home/zombie/.config"
export XDG_DATA_HOME="/home/zombie/.local/share"
export XDG_CACHE_HOME="/home/zombie/.cache"

# TriForce Env laden (fuer MCP-Endpoints, sonstige Settings)
TRIFORCE_ENV="/home/zombie/workspace/triforce/config/triforce.env"
if [[ -f "$TRIFORCE_ENV" ]]; then
  set -a
  source "$TRIFORCE_ENV"
  set +a
fi

# Account-Auth: CLI-Tools nutzen OAuth-Credentials aus HOME.
# API-Keys werden entfernt damit die Tools nicht auf API-Billing fallen.
# Fallback: wenn OAuth-Token abgelaufen -> API-Key aus triforce.env benutzen.

# Claude: ein vorhandener Account-Login hat Vorrang. Claude Code verwaltet
# Access-Token-Refresh selbst; ein abgelaufenes accessToken ist daher kein
# Grund, still auf API-Key-Billing zurueckzufallen.
if [[ -s /home/zombie/.claude/.credentials.json ]]; then
  unset ANTHROPIC_API_KEY
else
  export ANTHROPIC_API_KEY
fi

# Antigravity CLI (agy): account/keyring auth only. Do not expose legacy
# Gemini API keys to CLI wrappers or silently fall back to API billing.
unset GEMINI_API_KEY
unset GOOGLE_AI_STUDIO_KEY
unset GOOGLE_GEMINI_KEY

# Codex: Account-Token aus ~/.codex/auth.json
unset OPENAI_API_KEY

# Arbeitsverzeichnis
export TRIFORCE_DIR="/home/zombie/workspace/triforce"
cd "$TRIFORCE_DIR" || exit 1

# npm auto-update: einmal pro 24h, non-blocking, im Hintergrund
# Lockfile-basiert: /tmp/triforce-npm-update-<pkg>.lock
_auto_update_npm() {
  local pkg="$1"
  local lock="/tmp/triforce-npm-update-${pkg//\//-}.lock"
  local now interval=86400  # 24h in Sekunden

  # Lockfile-Alter pruefen
  if [[ -f "$lock" ]]; then
    now=$(date +%s)
    local mtime
    mtime=$(stat -c %Y "$lock" 2>/dev/null || echo 0)
    if (( now - mtime < interval )); then
      return 0  # noch kein Update faellig
    fi
  fi

  # Update im Hintergrund (blockiert den Agent-Start nicht)
  (
    touch "$lock"
    npm install -g "${pkg}@latest" --silent --no-fund --no-audit \
      >> "/home/zombie/workspace/triforce/logs/npm-updates.log" 2>&1 \
      && echo "$(date -Iseconds) | updated: ${pkg}" \
        >> "/home/zombie/workspace/triforce/logs/npm-updates.log"
  ) &
  disown
}

# Agent-Logging (1 Zeile pro Start)
AGENT_LOG="/home/zombie/workspace/triforce/logs/agent-starts.log"
_log_agent_start() {
  local agent="$1" model="$2"
  echo "$(date -Iseconds) | ${agent} | model=${model:-default} | pid=$$ | args=${*:3}" >> "$AGENT_LOG" 2>/dev/null
}
