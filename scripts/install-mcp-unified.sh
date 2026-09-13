#!/usr/bin/env bash
# =============================================================================
# TriForce MCP Unified Installer
# =============================================================================
# Installiert und konfiguriert alle Coding-AI-Tools mit TriForce MCP:
#   - Claude Code (Anthropic CLI)
#   - Codex CLI (OpenAI)
#   - Gemini CLI (Google)
#   - OpenCode
#
# Features:
#   - Erkennt automatisch Netzwerk-Kontext (localhost/LAN/VPN/extern)
#   - Kopiert Auth-Settings von root auf User
#   - Konfiguriert MCP-Endpunkt für jedes Tool
#   - Richtet Firewall ein (Ports 9000/9100/443)
#   - Einmaliges sudo-Login reicht
#
# Usage:
#   curl -sSL https://repo.ailinux.me/.../install-mcp-unified.sh | sudo bash
#   # oder lokal:
#   sudo bash /home/zombie/workspace/triforce/scripts/install-mcp-unified.sh [--user USER]
# =============================================================================

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────────────
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/workspace/triforce}"
ENV_FILE="${TRIFORCE_DIR}/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/var/log/triforce-install.log"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

# MCP Ports
MCP_INTERNAL_PORT=9000   # Direct backend (localhost/VPN - no auth)
MCP_AUTH_MARKER=9100     # Auth-trigger value (X-Forwarded-Port header only)
MCP_EXTERNAL_URL="https://api.ailinux.me/v1/mcp"  # via Apache HTTPS

# ── Parse Args ───────────────────────────────────────────────────────────────
TARGET_USER="${SUDO_USER:-${1:-zombie}}"
[[ "$1" == "--user" && -n "${2:-}" ]] && TARGET_USER="$2"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)

# ── Logging ──────────────────────────────────────────────────────────────────
exec > >(tee -a "$LOG_FILE") 2>&1

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
ok()   { echo "[$(date '+%H:%M:%S')] ✓ $*"; }
warn() { echo "[$(date '+%H:%M:%S')] ⚠ $*" >&2; }
err()  { echo "[$(date '+%H:%M:%S')] ✗ $*" >&2; exit 1; }

# ── Check root ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || err "Run with sudo: sudo bash $0"

log "=== TriForce MCP Unified Installer ==="
log "Target user: $TARGET_USER  Home: $TARGET_HOME"
log "TriForce dir: $TRIFORCE_DIR"

# ── Load env ─────────────────────────────────────────────────────────────────
if [[ -f "$ENV_FILE" ]]; then
    source "$ENV_FILE" 2>/dev/null || true
    ok "Loaded .env"
else
    warn ".env not found at $ENV_FILE"
fi

MCP_OAUTH_USER="${MCP_OAUTH_USER:-zombie}"
MCP_OAUTH_PASS="${MCP_OAUTH_PASS:-}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
GEMINI_API_KEY="${GEMINI_API_KEY:-}"
OPENAI_API_KEY="${OPENAI_API_KEY:-}"

# ── Network Context Detection ─────────────────────────────────────────────────
detect_network_context() {
    local ctx="external"

    # Check if MCP backend is reachable locally
    if curl -sf --max-time 2 "http://localhost:${MCP_INTERNAL_PORT}/health" >/dev/null 2>&1; then
        ctx="localhost"
    # Check VPN (wg0 with 10.10.0.x)
    elif ip addr show wg0 2>/dev/null | grep -q "10\.10\.0\."; then
        local vpn_ip
        vpn_ip=$(ip addr show wg0 2>/dev/null | grep "inet " | awk '{print $2}' | cut -d/ -f1)
        # Try master node via VPN
        if curl -sf --max-time 3 "http://10.10.0.1:${MCP_INTERNAL_PORT}/health" >/dev/null 2>&1; then
            ctx="vpn:${vpn_ip}"
        fi
    # Check LAN (192.168.x or 10.x)
    elif ip addr | grep -qE "inet 192\.168\.|inet 10\.(10|0|1)\."; then
        ctx="lan"
    fi
    echo "$ctx"
}

NET_CTX=$(detect_network_context)
log "Network context: $NET_CTX"

# Determine MCP URL based on context
case "$NET_CTX" in
    localhost)
        MCP_URL="http://localhost:${MCP_INTERNAL_PORT}/mcp"
        MCP_URL_SSE="http://localhost:${MCP_INTERNAL_PORT}/mcp/sse"
        MCP_URL_V1="http://localhost:${MCP_INTERNAL_PORT}/v1/mcp"
        AUTH_NEEDED=false
        ;;
    vpn:*)
        MASTER_VPN_IP="${NET_CTX#vpn:}"
        # If we're the master, use localhost; otherwise use master via VPN
        if [[ "$MASTER_VPN_IP" == "10.10.0.1" ]]; then
            MCP_URL="http://localhost:${MCP_INTERNAL_PORT}/mcp"
        else
            MCP_URL="http://10.10.0.1:${MCP_INTERNAL_PORT}/mcp"
        fi
        MCP_URL_SSE="${MCP_URL%/mcp}/mcp/sse"
        MCP_URL_V1="${MCP_URL%/mcp}/v1/mcp"
        AUTH_NEEDED=false
        ;;
    lan)
        MCP_URL="${MCP_EXTERNAL_URL%/v1/mcp}/mcp"
        MCP_URL_SSE="${MCP_EXTERNAL_URL%/v1/mcp}/mcp/sse"
        MCP_URL_V1="$MCP_EXTERNAL_URL"
        AUTH_NEEDED=true
        ;;
    *)
        MCP_URL="${MCP_EXTERNAL_URL%/v1/mcp}/mcp"
        MCP_URL_SSE="${MCP_EXTERNAL_URL%/v1/mcp}/mcp/sse"
        MCP_URL_V1="$MCP_EXTERNAL_URL"
        AUTH_NEEDED=true
        ;;
esac

log "MCP URL: $MCP_URL  Auth needed: $AUTH_NEEDED"

# ── Get OAuth Bearer Token ─────────────────────────────────────────────────────
get_bearer_token() {
    if [[ "$AUTH_NEEDED" == "false" ]]; then
        echo ""
        return
    fi
    local token_file="${TRIFORCE_DIR}/auth/token.json"
    # Try to get existing valid token
    if [[ -f "$token_file" ]]; then
        local token
        token=$(python3 -c "
import json, time
d = json.load(open('$token_file'))
t = d.get('access_token', '')
exp = d.get('expires_at', 0)
print(t if exp > time.time() else '')
" 2>/dev/null || echo "")
        [[ -n "$token" ]] && echo "$token" && return
    fi
    # Get new token via OAuth
    local base_url="${MCP_EXTERNAL_URL%/v1/mcp}"
    local token
    token=$(curl -sf -X POST "$base_url/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "grant_type=password&username=${MCP_OAUTH_USER}&password=${MCP_OAUTH_PASS}&client_id=installer" \
        2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
    echo "$token"
}

BEARER_TOKEN=$(get_bearer_token)
[[ -n "$BEARER_TOKEN" ]] && ok "Bearer token obtained" || log "No bearer token (internal access mode)"

# ── Dependency Check ──────────────────────────────────────────────────────────
log "Checking dependencies..."
which curl  >/dev/null || apt-get install -y curl  -qq
which node  >/dev/null || {
    log "Installing Node.js..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - -qq
    apt-get install -y nodejs -qq
}
which npm   >/dev/null || apt-get install -y npm -qq
which python3 >/dev/null || apt-get install -y python3 python3-pip -qq
which bun   >/dev/null || {
    log "Installing Bun..."
    curl -fsSL https://bun.sh/install | bash -s -- --no-profile 2>/dev/null || true
    export PATH="$HOME/.bun/bin:$PATH"
    ln -sf "$HOME/.bun/bin/bun" /usr/local/bin/bun 2>/dev/null || true
}
ok "Dependencies ready"

# ── Helper: copy root config to user ──────────────────────────────────────────
copy_to_user() {
    local src="$1"
    local dst_rel="$2"  # relative to $TARGET_HOME
    local dst="${TARGET_HOME}/${dst_rel}"

    [[ -e "$src" ]] || return 0
    mkdir -p "$(dirname "$dst")"
    cp -r "$src" "$dst"
    chown -R "${TARGET_USER}:${TARGET_USER}" "$dst"
    ok "Copied $(basename "$src") → $dst"
}

# Helper: write config as user
write_user_config() {
    local path="${TARGET_HOME}/$1"
    local content="$2"
    mkdir -p "$(dirname "$path")"
    echo "$content" > "$path"
    chown "${TARGET_USER}:${TARGET_USER}" "$path"
    chmod 600 "$path"
    ok "Written: $path"
}

# Helper: append to user's .bashrc/.zshrc/.profile
add_env_to_shell() {
    local var="$1"
    local val="$2"
    for rc in .bashrc .zshrc .profile; do
        local rcfile="${TARGET_HOME}/${rc}"
        [[ -f "$rcfile" ]] || continue
        grep -q "export ${var}=" "$rcfile" && \
            sed -i "s|export ${var}=.*|export ${var}=\"${val}\"|" "$rcfile" || \
            echo "export ${var}=\"${val}\"" >> "$rcfile"
    done
}

# ── Install Claude Code ────────────────────────────────────────────────────────
install_claude_code() {
    log "=== Installing Claude Code ==="

    # Install globally
    if ! which claude >/dev/null 2>&1; then
        npm install -g @anthropic-ai/claude-code 2>/dev/null || \
        npm install -g @anthropic-ai/claude-code --force 2>/dev/null || \
        warn "Claude Code install failed via npm, trying npx..."
    fi

    CLAUDE_BIN=$(which claude 2>/dev/null || echo "")
    [[ -z "$CLAUDE_BIN" ]] && warn "Claude Code binary not found" && return 1

    # Root config: ~/.claude.json
    ROOT_CLAUDE_JSON="/root/.claude.json"
    USER_CLAUDE_JSON="${TARGET_HOME}/.claude.json"

    # Copy root's .claude.json (contains auth, project settings) to user
    if [[ -f "$ROOT_CLAUDE_JSON" ]] && [[ "$(id -u $TARGET_USER)" != "0" ]]; then
        # Merge: use user's existing config as base, add root's MCP servers
        if [[ -f "$USER_CLAUDE_JSON" ]]; then
            python3 -c "
import json, os, sys

root = json.load(open('$ROOT_CLAUDE_JSON'))
user = json.load(open('$USER_CLAUDE_JSON'))

# Merge MCP servers from root to user's current project
for proj_path, proj_data in root.get('projects', {}).items():
    if proj_path not in user.setdefault('projects', {}):
        user['projects'][proj_path] = proj_data
    else:
        # Merge MCP servers
        root_mcp = proj_data.get('mcpServers', {})
        user['projects'][proj_path].setdefault('mcpServers', {}).update(root_mcp)

# Copy auth settings
for key in ('primaryApiKey', 'oauthAccount'):
    if key in root and key not in user:
        user[key] = root[key]

json.dump(user, open('$USER_CLAUDE_JSON', 'w'), indent=2)
print('Merged successfully')
" 2>/dev/null && ok "Merged Claude config (root→user)" || \
            cp "$ROOT_CLAUDE_JSON" "$USER_CLAUDE_JSON" && ok "Copied Claude config"
        else
            cp "$ROOT_CLAUDE_JSON" "$USER_CLAUDE_JSON"
        fi
        chown "${TARGET_USER}:${TARGET_USER}" "$USER_CLAUDE_JSON"
    fi

    # Copy root's .claude/ directory (plugins, settings)
    [[ -d "/root/.claude" ]] && copy_to_user "/root/.claude" ".claude"

    # Configure MCP server for current project
    python3 -c "
import json, os

cfg_path = os.path.expanduser('${TARGET_HOME}/.claude.json')
os.makedirs(os.path.dirname(cfg_path), exist_ok=True)

try:
    cfg = json.load(open(cfg_path)) if os.path.exists(cfg_path) else {}
except:
    cfg = {}

cfg.setdefault('projects', {})
proj_dir = '${TARGET_HOME}'
cfg['projects'].setdefault(proj_dir, {})
cfg['projects'][proj_dir].setdefault('mcpServers', {})

# Set TriForce MCP server
cfg['projects'][proj_dir]['mcpServers']['triforce'] = {
    'type': 'http',
    'url': '${MCP_URL_V1}'
}

# Global MCP for all projects
cfg['projects'][proj_dir]['hasTrustDialogAccepted'] = True

with open(cfg_path, 'w') as f:
    json.dump(cfg, f, indent=2)
print('OK')
" && ok "Claude Code MCP configured → $MCP_URL_V1" || warn "Failed to configure Claude Code MCP"

    # Copy triforce agent config
    copy_to_user "${TRIFORCE_DIR}/config/agents/claude/.claude.json" ".claude/triforce.json"

    # Set API key
    [[ -n "$ANTHROPIC_API_KEY" ]] && add_env_to_shell "ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"

    ok "Claude Code installed and configured"
}

# ── Install Codex CLI ──────────────────────────────────────────────────────────
install_codex() {
    log "=== Installing Codex CLI ==="

    # Install
    if ! which codex >/dev/null 2>&1; then
        npm install -g @openai/codex 2>/dev/null || warn "Codex install failed"
    fi

    CODEX_CONFIG_DIR="${TARGET_HOME}/.codex"
    mkdir -p "$CODEX_CONFIG_DIR"

    # MCP config with bearer auth if needed
    local mcp_section=""
    if [[ "$AUTH_NEEDED" == "true" && -n "$BEARER_TOKEN" ]]; then
        mcp_section="[[mcp.servers]]
name = \"triforce\"
transport = \"http\"
url = \"${MCP_URL}\"
[mcp.servers.headers]
Authorization = \"Bearer ${BEARER_TOKEN}\""
    elif [[ "$AUTH_NEEDED" == "true" && -n "$MCP_OAUTH_PASS" ]]; then
        local b64creds
        b64creds=$(echo -n "${MCP_OAUTH_USER}:${MCP_OAUTH_PASS}" | base64 -w0)
        mcp_section="[[mcp.servers]]
name = \"triforce\"
transport = \"http\"
url = \"${MCP_URL}\"
[mcp.servers.headers]
Authorization = \"Basic ${b64creds}\""
    else
        mcp_section="[[mcp.servers]]
name = \"triforce\"
transport = \"http\"
url = \"${MCP_URL}\""
    fi

    cat > "${CODEX_CONFIG_DIR}/config.toml" << TOML
# Codex CLI - TriForce MCP Configuration
# MCP Internal Port: ${MCP_INTERNAL_PORT} (no auth)
# MCP External:      ${MCP_EXTERNAL_URL} (auth via 443/Apache)

[defaults]
model = "gpt-4o"
approval_mode = "full-auto"

[sandbox]
enabled = false

${mcp_section}
TOML
    chown -R "${TARGET_USER}:${TARGET_USER}" "$CODEX_CONFIG_DIR"

    # Copy root's codex config if it exists
    [[ -d "/root/.codex" ]] && copy_to_user "/root/.codex" ".codex"

    [[ -n "$OPENAI_API_KEY" ]] && add_env_to_shell "OPENAI_API_KEY" "$OPENAI_API_KEY"

    ok "Codex configured → $MCP_URL"
}

# ── Install Gemini CLI ─────────────────────────────────────────────────────────
install_gemini_cli() {
    log "=== Installing Gemini CLI ==="

    # Check/install
    if ! which gemini >/dev/null 2>&1; then
        npm install -g @google/gemini-cli 2>/dev/null || \
        npm install -g @google-labs/gemini-cli 2>/dev/null || \
        warn "Gemini CLI install failed via npm"
    fi

    GEMINI_CONFIG_DIR="${TARGET_HOME}/.gemini"
    mkdir -p "$GEMINI_CONFIG_DIR"

    # Build settings.json with auth if needed
    if [[ "$AUTH_NEEDED" == "true" && -n "$BEARER_TOKEN" ]]; then
        cat > "${GEMINI_CONFIG_DIR}/settings.json" << JSON
{
  "mcpServers": {
    "triforce": {
      "type": "sse",
      "url": "${MCP_URL_SSE}",
      "headers": {
        "Authorization": "Bearer ${BEARER_TOKEN}"
      }
    }
  },
  "theme": "dark",
  "sandboxMode": "yolo",
  "outputFormat": "text"
}
JSON
    elif [[ "$AUTH_NEEDED" == "true" && -n "$MCP_OAUTH_PASS" ]]; then
        local b64creds
        b64creds=$(echo -n "${MCP_OAUTH_USER}:${MCP_OAUTH_PASS}" | base64 -w0)
        cat > "${GEMINI_CONFIG_DIR}/settings.json" << JSON
{
  "mcpServers": {
    "triforce": {
      "type": "sse",
      "url": "${MCP_URL_SSE}",
      "headers": {
        "Authorization": "Basic ${b64creds}"
      }
    }
  },
  "theme": "dark",
  "sandboxMode": "yolo",
  "outputFormat": "text"
}
JSON
    else
        cat > "${GEMINI_CONFIG_DIR}/settings.json" << JSON
{
  "mcpServers": {
    "triforce": {
      "type": "sse",
      "url": "${MCP_URL_SSE}"
    }
  },
  "theme": "dark",
  "sandboxMode": "yolo",
  "outputFormat": "text"
}
JSON
    fi

    chown -R "${TARGET_USER}:${TARGET_USER}" "$GEMINI_CONFIG_DIR"

    # Copy root's gemini config
    [[ -d "/root/.gemini" ]] && copy_to_user "/root/.gemini" ".gemini"

    [[ -n "$GEMINI_API_KEY" ]] && add_env_to_shell "GEMINI_API_KEY" "$GEMINI_API_KEY"

    ok "Gemini CLI configured → $MCP_URL_SSE"
}

# ── Install OpenCode ───────────────────────────────────────────────────────────
install_opencode() {
    log "=== Installing OpenCode ==="

    # Install via npm/bun
    if ! which opencode >/dev/null 2>&1; then
        npm install -g opencode-ai 2>/dev/null || \
        bun install -g opencode-ai 2>/dev/null || \
        npx -y opencode-ai --version >/dev/null 2>&1 || \
        warn "OpenCode install failed"
    fi

    OPENCODE_CONFIG_DIR="${TARGET_HOME}/.config/opencode"
    mkdir -p "$OPENCODE_CONFIG_DIR"

    # Build config based on auth context
    local mcp_url_for_opencode="$MCP_URL"
    local auth_headers=""
    if [[ "$AUTH_NEEDED" == "true" && -n "$BEARER_TOKEN" ]]; then
        auth_headers='"headers": {"Authorization": "Bearer '"$BEARER_TOKEN"'"}'
    elif [[ "$AUTH_NEEDED" == "true" && -n "$MCP_OAUTH_PASS" ]]; then
        local b64creds
        b64creds=$(echo -n "${MCP_OAUTH_USER}:${MCP_OAUTH_PASS}" | base64 -w0)
        auth_headers='"headers": {"Authorization": "Basic '"$b64creds"'"}'
    fi

    python3 -c "
import json

config = {
    '\$schema': 'https://opencode.ai/config.json',
    'model': 'anthropic/claude-sonnet-4-20250514',
    'small_model': 'google/gemini-2.0-flash',
    'username': 'TriForce',
    'theme': 'dracula',
    'autoupdate': 'notify',
    'share': 'manual',
    'mcp': {
        'triforce': {
            'type': 'remote',
            'url': '${MCP_URL}/',
            'enabled': True,
            'timeout': 30000
        }
    },
    'provider': {
        'anthropic': {
            'options': {'apiKey': '{env:ANTHROPIC_API_KEY}'},
            'whitelist': ['claude-sonnet-4-20250514', 'claude-3-5-haiku-20241022']
        },
        'google': {
            'options': {'apiKey': '{env:GEMINI_API_KEY}'},
            'whitelist': ['gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-2.5-pro']
        },
        'openai': {
            'options': {'apiKey': '{env:OPENAI_API_KEY}'},
            'whitelist': ['gpt-4o', 'gpt-4o-mini']
        },
        'groq': {
            'options': {'apiKey': '{env:GROQ_API_KEY}'},
            'whitelist': ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant']
        }
    },
    'permission': {
        'edit': 'allow',
        'bash': 'allow',
        'webfetch': 'allow'
    }
}

# Add auth headers if needed
if '${AUTH_NEEDED}' == 'true' and '${BEARER_TOKEN:-}':
    config['mcp']['triforce']['headers'] = {'Authorization': 'Bearer ${BEARER_TOKEN:-}'}
elif '${AUTH_NEEDED}' == 'true' and '${MCP_OAUTH_PASS:-}':
    import base64
    creds = base64.b64encode(b'${MCP_OAUTH_USER}:${MCP_OAUTH_PASS:-}'.encode()).decode()
    config['mcp']['triforce']['headers'] = {'Authorization': f'Basic {creds}'}

json.dump(config, open('${OPENCODE_CONFIG_DIR}/config.json', 'w'), indent=2)
print('OK')
" && ok "OpenCode configured → $MCP_URL" || warn "OpenCode config failed"

    chown -R "${TARGET_USER}:${TARGET_USER}" "$OPENCODE_CONFIG_DIR"

    # Copy existing triforce opencode config
    copy_to_user "${TRIFORCE_DIR}/config/agents/opencode/.config/opencode/config.json" \
        ".config/opencode/config.json.triforce-template"

    ok "OpenCode installed and configured"
}

# ── Setup Firewall ─────────────────────────────────────────────────────────────
setup_firewall() {
    log "=== Setting up Firewall ==="
    local fw_script="${SCRIPT_DIR}/setup-port-firewall.sh"
    [[ -x "$fw_script" ]] || chmod +x "$fw_script"
    bash "$fw_script" --auto && ok "Firewall configured" || warn "Firewall setup had issues"
}

# ── Setup Proxy Token Rotation ─────────────────────────────────────────────────
setup_token_rotation() {
    log "=== Setting up Proxy Token Rotation ==="
    local rot_script="${SCRIPT_DIR}/rotate-proxy-token.sh"
    chmod +x "$rot_script"

    # Create systemd timer for token rotation
    cat > /etc/systemd/system/triforce-proxy-token.service << 'SERVICE'
[Unit]
Description=TriForce Rotating Proxy Auth Token
After=network.target

[Service]
Type=oneshot
ExecStart=/home/zombie/workspace/triforce/scripts/rotate-proxy-token.sh
StandardOutput=journal
StandardError=journal
SERVICE

    cat > /etc/systemd/system/triforce-proxy-token.timer << 'TIMER'
[Unit]
Description=TriForce Proxy Token Rotation (every 4 minutes)
After=network.target

[Timer]
OnBootSec=30
OnUnitActiveSec=4min
Persistent=true

[Install]
WantedBy=multi-user.target
TIMER

    systemctl daemon-reload
    systemctl enable triforce-proxy-token.timer
    systemctl start triforce-proxy-token.timer
    ok "Proxy token rotation timer enabled (every 4 minutes)"
}

# ── Copy Agent Configs from triforce/config/agents ─────────────────────────────
sync_agent_configs() {
    log "=== Syncing agent configs from TriForce ==="
    local agents_dir="${TRIFORCE_DIR}/config/agents"

    # Claude: .claude.json (project config template)
    [[ -f "${agents_dir}/claude/.claude.json" ]] && \
        copy_to_user "${agents_dir}/claude/.claude.json" ".claude/triforce-project.json"

    # Codex
    [[ -d "${agents_dir}/codex/.codex" ]] && \
        copy_to_user "${agents_dir}/codex/.codex/config.toml" ".codex/config.triforce.toml"

    # Gemini
    [[ -f "${agents_dir}/gemini/.gemini/settings.json" ]] && \
        copy_to_user "${agents_dir}/gemini/.gemini/settings.json" ".gemini/settings.triforce.json"

    ok "Agent configs synced"
}

# ── Auth Settings: root → user ────────────────────────────────────────────────
copy_auth_from_root() {
    log "=== Copying auth settings root → $TARGET_USER ==="
    [[ "$(id -u $TARGET_USER)" == "0" ]] && return  # skip if target IS root

    # Claude auth
    [[ -f "/root/.claude.json" ]] && copy_to_user "/root/.claude.json" ".claude.json"

    # npm auth (if using private registry)
    [[ -f "/root/.npmrc" ]] && {
        grep -v "authToken\|_auth\|password" /root/.npmrc > "${TARGET_HOME}/.npmrc" 2>/dev/null || true
        chown "${TARGET_USER}:${TARGET_USER}" "${TARGET_HOME}/.npmrc"
    }

    # Copy env vars to user's shell config (API keys)
    local shell_rc="${TARGET_HOME}/.bashrc"
    [[ -f "${TARGET_HOME}/.zshrc" ]] && shell_rc="${TARGET_HOME}/.zshrc"

    cat >> "$shell_rc" << ENVRC

# TriForce MCP Config (added by install-mcp-unified.sh)
export TRIFORCE_MCP_URL="${MCP_URL_V1}"
export TRIFORCE_MCP_PORT="${MCP_INTERNAL_PORT}"
ENVRC

    [[ -n "$ANTHROPIC_API_KEY" ]] && add_env_to_shell "ANTHROPIC_API_KEY" "$ANTHROPIC_API_KEY"
    [[ -n "$GEMINI_API_KEY"    ]] && add_env_to_shell "GEMINI_API_KEY"    "$GEMINI_API_KEY"
    [[ -n "$OPENAI_API_KEY"    ]] && add_env_to_shell "OPENAI_API_KEY"    "$OPENAI_API_KEY"

    chown "${TARGET_USER}:${TARGET_USER}" "$shell_rc"
    ok "Auth settings copied to $TARGET_USER"
}

# ── Restart Backend with new port ──────────────────────────────────────────────
restart_backend() {
    log "=== Restarting TriForce backend on port $MCP_INTERNAL_PORT ==="

    # Kill old process if on wrong port (9100)
    if ss -tlnp | grep -q ":9100 "; then
        log "Found old process on 9100, killing..."
        fuser -k 9100/tcp 2>/dev/null || pkill -f "uvicorn.*9100" 2>/dev/null || true
        sleep 2
    fi

    # Start backend
    local start_script="${SCRIPT_DIR}/start-backend.sh"
    if [[ -f "$start_script" ]]; then
        log "Starting backend via $start_script..."
        sudo -u "$TARGET_USER" bash "$start_script" &
        sleep 5

        # Verify
        if curl -sf --max-time 5 "http://localhost:${MCP_INTERNAL_PORT}/health" >/dev/null 2>&1; then
            ok "Backend running on port $MCP_INTERNAL_PORT"
        else
            warn "Backend might not be ready yet (check logs: journalctl -u triforce)"
        fi
    else
        warn "start-backend.sh not found, please restart manually"
    fi
}

# ── Summary ────────────────────────────────────────────────────────────────────
print_summary() {
    echo ""
    echo "╔══════════════════════════════════════════════════════════════╗"
    echo "║         TriForce MCP Installation Complete                   ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Network context:  $NET_CTX"
    echo "║  MCP URL:          $MCP_URL"
    echo "║  Auth required:    $AUTH_NEEDED"
    echo "║  Target user:      $TARGET_USER"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Port Architecture:                                          ║"
    echo "║   9000 → MCP backend (internal: localhost + VPN + Docker)   ║"
    echo "║   9100 → Auth marker (X-Forwarded-Port, NOT listening)      ║"
    echo "║    443 → Public HTTPS (Apache Docker)                       ║"
    echo "╠══════════════════════════════════════════════════════════════╣"
    echo "║  Installed Tools:                                            ║"
    which claude   >/dev/null 2>&1 && echo "║   ✓ Claude Code  → $MCP_URL_V1" || echo "║   ✗ Claude Code (install failed)"
    which codex    >/dev/null 2>&1 && echo "║   ✓ Codex CLI   → $MCP_URL"     || echo "║   ✗ Codex CLI (install failed)"
    which gemini   >/dev/null 2>&1 && echo "║   ✓ Gemini CLI  → $MCP_URL_SSE" || echo "║   ✗ Gemini CLI (install failed)"
    which opencode >/dev/null 2>&1 && echo "║   ✓ OpenCode    → $MCP_URL"     || echo "║   ✗ OpenCode (install failed)"
    echo "╚══════════════════════════════════════════════════════════════╝"
    echo ""
    echo "Next steps:"
    echo "  1. Reload shell:  source ~/.bashrc"
    echo "  2. Test MCP:      curl http://localhost:${MCP_INTERNAL_PORT}/health"
    echo "  3. Test Claude:   claude --mcp-debug"
    echo ""
}

# ── Main ───────────────────────────────────────────────────────────────────────
main() {
    log "Starting installation at $TIMESTAMP"

    install_claude_code   || warn "Claude Code install had issues"
    install_codex         || warn "Codex install had issues"
    install_gemini_cli    || warn "Gemini CLI install had issues"
    install_opencode      || warn "OpenCode install had issues"

    copy_auth_from_root
    sync_agent_configs
    setup_firewall
    setup_token_rotation
    restart_backend
    print_summary

    log "=== Installation complete! Log: $LOG_FILE ==="
}

main "$@"
