#!/usr/bin/env bash
################################################################################
# fix-cli-agents.sh - Installiert CLI-Tools für zombie User (nicht root!)
# AUSFÜHREN ALS: zombie (NICHT sudo!)
################################################################################
set -e

echo "=== CLI Agent Fix für User: $(whoami) ==="

# Prüfe dass wir NICHT root sind
if [[ $EUID -eq 0 ]]; then
   echo "ERROR: Dieses Script darf NICHT als root ausgeführt werden!"
   echo "Usage: ./fix-cli-agents.sh (als zombie)"
   exit 1
fi

BASE_DIR="/home/zombie/triforce"
TRIFORCE_DIR="$BASE_DIR/triforce"

# ============================================================================
# 1. npm global Verzeichnis für zombie einrichten
# ============================================================================
echo ""
echo "[1/6] Konfiguriere npm für User zombie..."

NPM_PREFIX="$HOME/.npm-global"
mkdir -p "$NPM_PREFIX"
npm config set prefix "$NPM_PREFIX"

# PATH erweitern (für aktuelle Session)
export PATH="$NPM_PREFIX/bin:$PATH"

# In .bashrc eintragen falls nicht vorhanden
if ! grep -q 'npm-global/bin' ~/.bashrc 2>/dev/null; then
    {
        echo ''
        echo '# npm global binaries'
        # literal shell code is appended to .bashrc
        # shellcheck disable=SC2016
        echo 'export PATH="$HOME/.npm-global/bin:$PATH"'
    } >> ~/.bashrc
    echo "[✓] PATH zu ~/.bashrc hinzugefügt"
fi

# ============================================================================
# 2. CLI-Tools für zombie installieren
# ============================================================================
echo ""
echo "[2/6] Installiere CLI-Tools für User zombie..."

echo "  → @anthropic-ai/claude-code..."
npm install -g @anthropic-ai/claude-code@latest 2>&1 | tail -3

echo "  → @google/gemini-cli..."
npm install -g @google/gemini-cli@latest 2>&1 | tail -3

echo "  → @openai/codex..."
npm install -g @openai/codex@latest 2>&1 | tail -3

echo "  → opencode-ai..."
npm install -g opencode-ai@latest 2>&1 | tail -3

# ============================================================================
# 3. Wrapper Scripts anpassen
# ============================================================================
echo ""
echo "[3/6] Aktualisiere Wrapper Scripts..."

# Claude Wrapper
cat > "$TRIFORCE_DIR/bin/claude-triforce" << 'EOF'
#!/bin/bash
set -euo pipefail

TRIFORCE_ROOT="/home/zombie/triforce/triforce"
BINARY="$HOME/.npm-global/bin/claude"

# Fallback
[[ ! -x "$BINARY" ]] && BINARY=$(command -v claude 2>/dev/null || echo "$BINARY")

export HOME="$TRIFORCE_ROOT/runtime/claude"
export XDG_CONFIG_HOME="$HOME"
export CLAUDE_CONFIG_DIR="$HOME"

mkdir -p "$HOME/.claude"

ARGS=("$@")

IS_MCP=false
for a in "${ARGS[@]:-}"; do
  [[ "$a" == "mcp" ]] && IS_MCP=true && break
done

if [[ "$IS_MCP" == "false" ]]; then
  if [[ ! " ${ARGS[*]:-} " =~ " --dangerously-skip-permissions " ]]; then
    ARGS+=("--dangerously-skip-permissions")
  fi
fi

[[ "${TRIFORCE_DEBUG:-false}" == "true" ]] && ARGS+=("--verbose")

CLAUDE_MD="$HOME/.claude/CLAUDE.md"
if [[ -f "$TRIFORCE_ROOT/prompts/claude.md" ]] && [[ ! -f "$CLAUDE_MD" ]]; then
  cp "$TRIFORCE_ROOT/prompts/claude.md" "$CLAUDE_MD"
fi

exec "$BINARY" "${ARGS[@]}"
EOF

# Codex Wrapper
cat > "$TRIFORCE_DIR/bin/codex-triforce" << 'EOF'
#!/bin/bash
set -euo pipefail

TRIFORCE_ROOT="/home/zombie/triforce/triforce"
BINARY="$HOME/.npm-global/bin/codex"

[[ ! -x "$BINARY" ]] && BINARY=$(command -v codex 2>/dev/null || echo "$BINARY")

export HOME="$TRIFORCE_ROOT/runtime/codex"
export XDG_CONFIG_HOME="$HOME"
export CODEX_HOME="$HOME/.codex"

mkdir -p "$HOME" "$CODEX_HOME"

TOKEN_FILE="$TRIFORCE_ROOT/secrets/mcp.token"
[[ -f "$TOKEN_FILE" ]] && export TRIFORCE_MCP_TOKEN=$(<"$TOKEN_FILE")

ARGS=("$@")

IS_MCP=false
for a in "${ARGS[@]:-}"; do
  [[ "$a" == "mcp" ]] && IS_MCP=true && break
done

if [[ "$IS_MCP" == "false" ]]; then
  if [[ ! " ${ARGS[*]:-} " =~ " -a " ]] && [[ ! " ${ARGS[*]:-} " =~ " --ask-for-approval " ]]; then
    ARGS=("-a" "never" "${ARGS[@]}")
  fi
fi

[[ "${TRIFORCE_DEBUG:-false}" == "true" ]] && ARGS+=("--log-level" "DEBUG" "--print-logs")

exec "$BINARY" "${ARGS[@]}"
EOF

# Gemini Wrapper
cat > "$TRIFORCE_DIR/bin/gemini-triforce" << 'EOF'
#!/bin/bash
set -euo pipefail

TRIFORCE_ROOT="/home/zombie/triforce/triforce"
BINARY="$HOME/.npm-global/bin/gemini"

[[ ! -x "$BINARY" ]] && BINARY=$(command -v gemini 2>/dev/null || echo "$BINARY")

export HOME="$TRIFORCE_ROOT/runtime/gemini"
export XDG_CONFIG_HOME="$HOME"

mkdir -p "$HOME/.gemini"

ARGS=("$@")

IS_MCP=false
for a in "${ARGS[@]:-}"; do
  [[ "$a" == "mcp" ]] && IS_MCP=true && break
done

if [[ "$IS_MCP" == "false" ]]; then
  if [[ ! " ${ARGS[*]:-} " =~ " -y " ]] && [[ ! " ${ARGS[*]:-} " =~ " --yolo " ]]; then
    ARGS+=("-y")
  fi
fi

[[ "${TRIFORCE_DEBUG:-false}" == "true" ]] && ARGS+=("--debug")

exec "$BINARY" "${ARGS[@]}"
EOF

chmod +x "$TRIFORCE_DIR/bin/"*-triforce

# ============================================================================
# 4. Runtime-Verzeichnisse erstellen mit korrekten Permissions
# ============================================================================
echo ""
echo "[4/6] Erstelle Runtime-Verzeichnisse..."

for agent in claude codex gemini opencode; do
    mkdir -p "$TRIFORCE_DIR/runtime/$agent/.${agent}"
    mkdir -p "$TRIFORCE_DIR/runtime/$agent/.config"
done

# Spezielle Verzeichnisse
mkdir -p "$TRIFORCE_DIR/runtime/codex/.codex"
mkdir -p "$TRIFORCE_DIR/runtime/claude/.claude"
mkdir -p "$TRIFORCE_DIR/runtime/gemini/.gemini"

# ============================================================================
# 5. MCP Config MANUELL schreiben (bypassed OAuth!)
# ============================================================================
echo ""
echo "[5/6] Konfiguriere MCP Server (localhost bypass, OHNE OAuth)..."

MCP_URL="http://127.0.0.1:9100/v1/mcp"

# Claude MCP Config
CLAUDE_CONFIG="$TRIFORCE_DIR/runtime/claude/.claude.json"
if [[ -f "$CLAUDE_CONFIG" ]]; then
    # Backup
    cp "$CLAUDE_CONFIG" "${CLAUDE_CONFIG}.bak"
fi

# Minimale Claude Config mit MCP
cat > "$CLAUDE_CONFIG" << EOFCLAUDE
{
  "mcpServers": {
    "triforce-mcp": {
      "type": "http",
      "url": "$MCP_URL"
    }
  },
  "bypassPermissions": true,
  "autoApprove": ["mcp"],
  "theme": "dark"
}
EOFCLAUDE

# Codex MCP Config (config.toml)
CODEX_CONFIG="$TRIFORCE_DIR/runtime/codex/.codex/config.toml"
mkdir -p "$(dirname "$CODEX_CONFIG")"
cat > "$CODEX_CONFIG" << EOFCODEX
# Codex CLI Configuration für TriForce
model = "gpt-4.1"
approval_policy = "auto-edit"

[mcp_servers.triforce-mcp]
type = "http"
url = "$MCP_URL"
EOFCODEX

# Gemini MCP Config
GEMINI_CONFIG="$TRIFORCE_DIR/runtime/gemini/.gemini/settings.json"
mkdir -p "$(dirname "$GEMINI_CONFIG")"
cat > "$GEMINI_CONFIG" << EOFGEMINI
{
  "mcpServers": {
    "triforce-mcp": {
      "httpUrl": "$MCP_URL"
    }
  },
  "theme": "Default Dark",
  "coreTools": {
    "googleSearch": true,
    "urlContext": true,
    "codeExecution": true
  }
}
EOFGEMINI

echo "[✓] MCP Configs geschrieben (ohne OAuth)"

# ============================================================================
# 6. Verifizierung
# ============================================================================
echo ""
echo "[6/6] Verifiziere Installation..."

echo ""
echo "Installierte CLI-Tools:"
for cmd in claude codex gemini opencode; do
    if command -v $cmd &>/dev/null; then
        version=$($cmd --version 2>/dev/null | head -1 || echo "OK")
        echo "  ✓ $cmd: $version"
    else
        echo "  ✗ $cmd: nicht gefunden (PATH: $NPM_PREFIX/bin)"
    fi
done

echo ""
echo "Wrapper Scripts:"
ls -la "$TRIFORCE_DIR/bin/"*-triforce 2>/dev/null || echo "  Keine gefunden"

echo ""
echo "=== Fix Complete ==="
echo ""
echo "Nächste Schritte:"
echo "  1. Neue Shell öffnen oder: source ~/.bashrc"
echo "  2. Test: $TRIFORCE_DIR/bin/claude-triforce mcp list"
echo "  3. Test: curl -s http://127.0.0.1:9100/v1/mcp/status"
