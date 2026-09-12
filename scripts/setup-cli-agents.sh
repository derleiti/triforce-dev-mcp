#!/usr/bin/env bash
#
# ============================================================================
# CLI Coding Agents Setup Script v1.0
# ============================================================================
# Installiert und konfiguriert CLI Coding Agents mit MCP Support
#
# Agents: Claude Code, OpenAI Codex, Google Gemini CLI, OpenCode
# MCP Server: http://localhost:9100/mcp (TriForce)
#
# Author: TriStar/Nova AI System
# Date: 2025-12-06
# ============================================================================

set -euo pipefail

# ============================================================================
# KONSTANTEN
# ============================================================================
readonly SCRIPT_VERSION="1.0.0"
SCRIPT_NAME=$(basename "$0")
readonly SCRIPT_NAME
readonly BASE_DIR="/home/zombie/triforce"
readonly NPM_GLOBAL_DIR="/root/.npm-global"
readonly MCP_URL="http://localhost:9100/mcp"

# npm Pakete
readonly NPM_PACKAGES=(
    "@anthropic-ai/claude-code"
    "@openai/codex"
    "@google/gemini-cli"
    "opencode-ai"
)

# Agent Namen
readonly AGENTS=("claude" "codex" "gemini" "opencode")

# ============================================================================
# FARBEN
# ============================================================================
readonly RED='\033[0;31m'
readonly GREEN='\033[0;32m'
readonly YELLOW='\033[0;33m'
readonly BLUE='\033[0;34m'
readonly CYAN='\033[0;36m'
readonly BOLD='\033[1m'
readonly NC='\033[0m' # No Color

# ============================================================================
# FLAGS
# ============================================================================
DRY_RUN=0
VERBOSE=0
MODE="install"

# ============================================================================
# LOGGING FUNKTIONEN
# ============================================================================
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[✓]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

log_step() {
    echo -e "\n${CYAN}${BOLD}==> $1${NC}"
}

run_cmd() {
    if [ "$VERBOSE" -eq 1 ]; then
        log_info "Executing: $*"
    fi
    if [ "$DRY_RUN" -eq 0 ]; then
        # Commands are intentionally assembled as shell snippets by the legacy call sites.
        # shellcheck disable=SC2294
        eval "$@"
    else
        echo -e "${YELLOW}[DRY-RUN]${NC} Would execute: $*"
    fi
}

# ============================================================================
# HILFE
# ============================================================================
print_help() {
    cat << EOF
${BOLD}CLI Coding Agents Setup Script v${SCRIPT_VERSION}${NC}

Installiert und konfiguriert CLI Coding Agents (Claude, Codex, Gemini, OpenCode)
mit MCP Server Integration für das TriForce/TriStar System.

${BOLD}USAGE:${NC}
    ./${SCRIPT_NAME} [OPTIONS]

${BOLD}OPTIONS:${NC}
    --help, -h      Diese Hilfe anzeigen
    --dry-run       Zeigt Befehle ohne Ausführung
    --verbose, -v   Ausführliche Ausgabe
    --update        Aktualisiert installierte Pakete
    --uninstall     Entfernt alle Installationen
    --status        Zeigt Installationsstatus

${BOLD}BEISPIELE:${NC}
    ./${SCRIPT_NAME}              # Normale Installation
    ./${SCRIPT_NAME} --dry-run    # Simulation
    ./${SCRIPT_NAME} --update     # Updates installieren
    ./${SCRIPT_NAME} --uninstall  # Deinstallation

${BOLD}NACH DER INSTALLATION:${NC}
    1. API-Keys in ${BASE_DIR}/auth/.env.agents eintragen
    2. Agents einloggen:
       - claude login
       - codex login  
       - gemini (OAuth beim Start)
       - opencode auth login
    3. Test: ${BASE_DIR}/bin/claude-triforce "Hello"

EOF
}

# ============================================================================
# ARGUMENT PARSING
# ============================================================================
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --help|-h)
                print_help
                exit 0
                ;;
            --dry-run)
                DRY_RUN=1
                shift
                ;;
            --verbose|-v)
                VERBOSE=1
                shift
                ;;
            --update)
                MODE="update"
                shift
                ;;
            --uninstall)
                MODE="uninstall"
                shift
                ;;
            --status)
                MODE="status"
                shift
                ;;
            *)
                log_error "Unbekannte Option: $1"
                print_help
                exit 1
                ;;
        esac
    done
}

# ============================================================================
# VERZEICHNISSTRUKTUR
# ============================================================================
setup_directories() {
    log_step "Erstelle Verzeichnisstruktur"
    
    local DIRS=(
        "${BASE_DIR}/bin"
        "${BASE_DIR}/config/agents/claude/.claude"
        "${BASE_DIR}/config/agents/codex/.codex"
        "${BASE_DIR}/config/agents/gemini/.gemini"
        "${BASE_DIR}/config/agents/opencode/.local/share/opencode"
        "${BASE_DIR}/config/mcp"
        "${BASE_DIR}/config/prompts"
        "${BASE_DIR}/auth"
        "${BASE_DIR}/logs"
    )
    
    for dir in "${DIRS[@]}"; do
        run_cmd "mkdir -p '${dir}'"
        log_success "Verzeichnis: ${dir}"
    done
}

# ============================================================================
# NPM SETUP
# ============================================================================
setup_npm() {
    log_step "Konfiguriere npm"
    
    # npm global Verzeichnis erstellen
    run_cmd "mkdir -p '${NPM_GLOBAL_DIR}'"
    
    # npm prefix setzen
    local current_prefix
    current_prefix=$(npm config get prefix 2>/dev/null || echo "")
    
    if [ "$current_prefix" != "$NPM_GLOBAL_DIR" ]; then
        run_cmd "npm config set prefix '${NPM_GLOBAL_DIR}'"
        log_success "npm prefix gesetzt: ${NPM_GLOBAL_DIR}"
    else
        log_info "npm prefix bereits korrekt"
    fi
    
    # PATH in .bashrc/.profile hinzufügen
    local profile_file="/root/.bashrc"
    local path_export="export PATH=\"${NPM_GLOBAL_DIR}/bin:\$PATH\""
    
    if ! grep -q "${NPM_GLOBAL_DIR}/bin" "$profile_file" 2>/dev/null; then
        run_cmd "echo '' >> '${profile_file}'"
        run_cmd "echo '# npm global packages' >> '${profile_file}'"
        run_cmd "echo '${path_export}' >> '${profile_file}'"
        log_success "PATH in ${profile_file} hinzugefügt"
    fi
    
    # PATH für aktuelle Session setzen
    export PATH="${NPM_GLOBAL_DIR}/bin:$PATH"
}

# ============================================================================
# PAKETE INSTALLIEREN
# ============================================================================
install_packages() {
    log_step "Installiere npm Pakete"
    
    for pkg in "${NPM_PACKAGES[@]}"; do
        log_info "Installiere: ${pkg}"
        run_cmd "npm install -g '${pkg}'"
        log_success "Installiert: ${pkg}"
    done
}

update_packages() {
    log_step "Aktualisiere npm Pakete"
    
    for pkg in "${NPM_PACKAGES[@]}"; do
        log_info "Aktualisiere: ${pkg}"
        run_cmd "npm update -g '${pkg}'"
        log_success "Aktualisiert: ${pkg}"
    done
}

# ============================================================================
# SYMLINKS ERSTELLEN
# ============================================================================
create_symlinks() {
    log_step "Erstelle Symlinks"
    
    # Binary Symlinks
    local binaries=(
        "claude:claude"
        "codex:codex"
        "gemini:gemini"
        "opencode:opencode"
    )
    
    for entry in "${binaries[@]}"; do
        local name="${entry%%:*}"
        local bin="${entry##*:}"
        local src="${NPM_GLOBAL_DIR}/bin/${bin}"
        local dst="${BASE_DIR}/bin/${name}"
        
        if [ -f "$src" ] || [ -L "$src" ]; then
            run_cmd "ln -sf '${src}' '${dst}'"
            log_success "Symlink: ${dst} -> ${src}"
        else
            log_warn "Binary nicht gefunden: ${src}"
        fi
    done
}

# ============================================================================
# KONFIGURATIONEN ERSTELLEN
# ============================================================================
create_configs() {
    log_step "Erstelle Konfigurationsdateien"
    
    local cfg_base="${BASE_DIR}/config/agents"
    
    # -------------------------------------------------------------------------
    # Claude Code Konfiguration
    # -------------------------------------------------------------------------
    log_info "Erstelle Claude Code Konfiguration"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${cfg_base}/claude/.claude/.claude.json" << 'EOFCLAUDE'
{
  "mcpServers": {
    "triforce": {
      "type": "http",
      "url": "http://localhost:9100/mcp"
    }
  }
}
EOFCLAUDE

        cat > "${cfg_base}/claude/.claude/settings.local.json" << 'EOFSETTINGS'
{
  "permissions": {
    "allow_file_access": true,
    "allow_shell_commands": true
  },
  "model": "claude-sonnet-4-20250514",
  "output_format": "text"
}
EOFSETTINGS
    fi
    log_success "Claude Konfiguration erstellt"

    # -------------------------------------------------------------------------
    # Codex Konfiguration
    # -------------------------------------------------------------------------
    log_info "Erstelle Codex Konfiguration"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${cfg_base}/codex/.codex/config.toml" << 'EOFCODEX'
# OpenAI Codex CLI Configuration
# MCP Server Integration für TriForce

[defaults]
model = "gpt-5-codex"
approval_mode = "full-auto"

[sandbox]
enabled = false

[[mcp.servers]]
name = "triforce"
transport = "http"
url = "http://localhost:9100/mcp"
EOFCODEX
    fi
    log_success "Codex Konfiguration erstellt"

    # -------------------------------------------------------------------------
    # Gemini CLI Konfiguration
    # -------------------------------------------------------------------------
    log_info "Erstelle Gemini CLI Konfiguration"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${cfg_base}/gemini/.gemini/settings.json" << 'EOFGEMINI'
{
  "mcpServers": {
    "triforce": {
      "type": "sse",
      "url": "http://localhost:9100/mcp/sse"
    }
  },
  "theme": "dark",
  "sandboxMode": "yolo",
  "outputFormat": "text"
}
EOFGEMINI
    fi
    log_success "Gemini Konfiguration erstellt"

    # -------------------------------------------------------------------------
    # OpenCode Konfiguration
    # -------------------------------------------------------------------------
    log_info "Erstelle OpenCode Konfiguration"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${cfg_base}/opencode/.local/share/opencode/config.json" << 'EOFOPENCODE'
{
  "mcpServers": {
    "triforce": {
      "type": "http",
      "url": "http://localhost:9100/mcp"
    }
  },
  "provider": "anthropic",
  "model": "claude-sonnet-4-20250514",
  "outputFormat": "text"
}
EOFOPENCODE
    fi
    log_success "OpenCode Konfiguration erstellt"

    # -------------------------------------------------------------------------
    # Globale MCP Konfiguration
    # -------------------------------------------------------------------------
    log_info "Erstelle globale MCP Konfiguration"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${BASE_DIR}/config/mcp/triforce-mcp.json" << 'EOFMCP'
{
  "version": "1.0",
  "name": "triforce",
  "description": "TriForce MCP Server für CLI Agents",
  "endpoints": {
    "http": "http://localhost:9100/mcp",
    "sse": "http://localhost:9100/mcp/sse"
  },
  "capabilities": [
    "tools",
    "memory",
    "web_search",
    "code_execution"
  ]
}
EOFMCP
    fi
    log_success "MCP Konfiguration erstellt"
}

# ============================================================================
# AUTH DATEI ERSTELLEN
# ============================================================================
create_auth_file() {
    log_step "Erstelle Authentifizierungs-Datei"
    
    local auth_file="${BASE_DIR}/auth/.env.agents"
    
    if [ ! -f "$auth_file" ] || [ "$DRY_RUN" -eq 1 ]; then
        if [ "$DRY_RUN" -eq 0 ]; then
            cat > "$auth_file" << 'EOFAUTH'
# ============================================================================
# API Keys für CLI Coding Agents
# ============================================================================
# WICHTIG: Diese Datei enthält sensitive Daten!
# Niemals in Git committen!
# ============================================================================

# Anthropic (Claude Code)
export ANTHROPIC_API_KEY=""

# OpenAI (Codex)
export OPENAI_API_KEY=""

# Google (Gemini CLI)
export GOOGLE_API_KEY=""
export GEMINI_API_KEY=""

# OpenCode (nutzt Anthropic oder eigenen Key)
export OPENCODE_API_KEY=""

# ============================================================================
# TriForce MCP Server
# ============================================================================
export MCP_SERVER_URL="http://localhost:9100/mcp"
export TRIFORCE_API_KEY=""
EOFAUTH
            chmod 600 "$auth_file"
        fi
        log_success "Auth-Datei erstellt: ${auth_file}"
        log_warn "Bitte API-Keys in ${auth_file} eintragen!"
    else
        log_info "Auth-Datei existiert bereits"
    fi
    
    # .gitignore für auth
    if [ "$DRY_RUN" -eq 0 ]; then
        echo "*.env*" > "${BASE_DIR}/auth/.gitignore"
        echo ".env.agents" >> "${BASE_DIR}/auth/.gitignore"
    fi
}

# ============================================================================
# WRAPPER SCRIPTS ERSTELLEN
# ============================================================================
create_wrappers() {
    log_step "Erstelle Wrapper-Scripts"
    
    local bin_dir="${BASE_DIR}/bin"
    
    # -------------------------------------------------------------------------
    # Claude Triforce Wrapper
    # -------------------------------------------------------------------------
    log_info "Erstelle claude-triforce Wrapper"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${bin_dir}/claude-triforce" << 'EOFWRAPPER'
#!/bin/bash
# Claude Code TriForce Wrapper
set -euo pipefail

BASE_DIR="/home/zombie/triforce"
AGENT_NAME="claude"
LOG_FILE="${BASE_DIR}/logs/${AGENT_NAME}.log"

# HOME auf Agent-Config setzen
export HOME="${BASE_DIR}/config/agents/${AGENT_NAME}"

# API Keys laden
if [ -f "${BASE_DIR}/bin/load-chatgpt-env.sh" ]; then
    source "${BASE_DIR}/bin/load-chatgpt-env.sh"
elif [ -f "${BASE_DIR}/auth/.env.agents" ]; then
    source "${BASE_DIR}/auth/.env.agents"
fi

# Logging
echo "--- $(date '+%Y-%m-%d %H:%M:%S') | claude-triforce | Args: $* ---" >> "$LOG_FILE"

# Binary Pfad
CLAUDE_BIN="${BASE_DIR}/bin/claude"

# STDIN Check und Ausführung
if [ -t 0 ]; then
    # Kein STDIN - normale Ausführung
    exec "$CLAUDE_BIN" -p --output-format text "$@" 2>> "$LOG_FILE"
else
    # STDIN vorhanden - pipen
    exec "$CLAUDE_BIN" -p --output-format text "$@" < /dev/stdin 2>> "$LOG_FILE"
fi
EOFWRAPPER
    fi

    # -------------------------------------------------------------------------
    # Codex Triforce Wrapper
    # -------------------------------------------------------------------------
    log_info "Erstelle codex-triforce Wrapper"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${bin_dir}/codex-triforce" << 'EOFWRAPPER'
#!/bin/bash
# Codex TriForce Wrapper
set -euo pipefail

BASE_DIR="/home/zombie/triforce"
AGENT_NAME="codex"
LOG_FILE="${BASE_DIR}/logs/${AGENT_NAME}.log"

# HOME auf Agent-Config setzen
export HOME="${BASE_DIR}/config/agents/${AGENT_NAME}"

# API Keys laden
if [ -f "${BASE_DIR}/bin/load-chatgpt-env.sh" ]; then
    source "${BASE_DIR}/bin/load-chatgpt-env.sh"
elif [ -f "${BASE_DIR}/auth/.env.agents" ]; then
    source "${BASE_DIR}/auth/.env.agents"
fi

# Logging
echo "--- $(date '+%Y-%m-%d %H:%M:%S') | codex-triforce | Args: $* ---" >> "$LOG_FILE"

# Binary Pfad
CODEX_BIN="${BASE_DIR}/bin/codex"

# STDIN Check und Ausführung
if [ -t 0 ]; then
    exec "$CODEX_BIN" exec --full-auto --dangerously-bypass-approvals-and-sandbox "$@" 2>> "$LOG_FILE"
else
    exec "$CODEX_BIN" exec --full-auto --dangerously-bypass-approvals-and-sandbox "$@" < /dev/stdin 2>> "$LOG_FILE"
fi
EOFWRAPPER
    fi

    # -------------------------------------------------------------------------
    # Gemini Triforce Wrapper
    # -------------------------------------------------------------------------
    log_info "Erstelle gemini-triforce Wrapper"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${bin_dir}/gemini-triforce" << 'EOFWRAPPER'
#!/bin/bash
# Gemini CLI TriForce Wrapper
set -euo pipefail

BASE_DIR="/home/zombie/triforce"
AGENT_NAME="gemini"
LOG_FILE="${BASE_DIR}/logs/${AGENT_NAME}.log"

# HOME auf Agent-Config setzen
export HOME="${BASE_DIR}/config/agents/${AGENT_NAME}"

# API Keys laden
if [ -f "${BASE_DIR}/bin/load-chatgpt-env.sh" ]; then
    source "${BASE_DIR}/bin/load-chatgpt-env.sh"
elif [ -f "${BASE_DIR}/auth/.env.agents" ]; then
    source "${BASE_DIR}/auth/.env.agents"
fi

# Logging
echo "--- $(date '+%Y-%m-%d %H:%M:%S') | gemini-triforce | Args: $* ---" >> "$LOG_FILE"

# Binary Pfad
GEMINI_BIN="${BASE_DIR}/bin/gemini"

# STDIN Check und Ausführung
if [ -t 0 ]; then
    exec "$GEMINI_BIN" --yolo --output-format text "$@" 2>> "$LOG_FILE"
else
    exec "$GEMINI_BIN" --yolo --output-format text "$@" < /dev/stdin 2>> "$LOG_FILE"
fi
EOFWRAPPER
    fi

    # -------------------------------------------------------------------------
    # OpenCode Triforce Wrapper
    # -------------------------------------------------------------------------
    log_info "Erstelle opencode-triforce Wrapper"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "${bin_dir}/opencode-triforce" << 'EOFWRAPPER'
#!/bin/bash
# OpenCode TriForce Wrapper
set -euo pipefail

BASE_DIR="/home/zombie/triforce"
AGENT_NAME="opencode"
LOG_FILE="${BASE_DIR}/logs/${AGENT_NAME}.log"

# HOME auf Agent-Config setzen
export HOME="${BASE_DIR}/config/agents/${AGENT_NAME}"

# XDG Dirs setzen für OpenCode
export XDG_DATA_HOME="${HOME}/.local/share"
export XDG_CONFIG_HOME="${HOME}/.config"

# API Keys laden
if [ -f "${BASE_DIR}/bin/load-chatgpt-env.sh" ]; then
    source "${BASE_DIR}/bin/load-chatgpt-env.sh"
elif [ -f "${BASE_DIR}/auth/.env.agents" ]; then
    source "${BASE_DIR}/auth/.env.agents"
fi

# Logging
echo "--- $(date '+%Y-%m-%d %H:%M:%S') | opencode-triforce | Args: $* ---" >> "$LOG_FILE"

# Binary Pfad
OPENCODE_BIN="${BASE_DIR}/bin/opencode"

# STDIN Check und Ausführung
if [ -t 0 ]; then
    exec "$OPENCODE_BIN" run "$@" 2>> "$LOG_FILE"
else
    exec "$OPENCODE_BIN" run "$@" < /dev/stdin 2>> "$LOG_FILE"
fi
EOFWRAPPER
    fi

    # Alle Wrapper ausführbar machen
    run_cmd "chmod +x '${bin_dir}/'*-triforce"
    
    log_success "Alle Wrapper-Scripts erstellt"
}

# ============================================================================
# SYSTEM PROMPT ERSTELLEN
# ============================================================================
create_system_prompt() {
    log_step "Erstelle System-Prompt"
    
    local prompt_file="${BASE_DIR}/config/prompts/cli-agent-system.txt"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        cat > "$prompt_file" << 'EOFPROMPT'
SYSTEM: TriStar/TriForce CLI-Agent im AILinux MCP-System.

MCP-ENDPOINT: http://localhost:9100/mcp (lokal, niedrige Latenz)
API-ENDPOINT: http://localhost:9100 (Backend)

ZIELE: Effiziente Ausführung von Coding-Tasks mit MCP-Tool-Nutzung.

VERFÜGBARE MCP-TOOLS:
- tristar_memory_search: Suche im Shared Memory
- tristar_memory_store: Speichere Erkenntnisse
- web_search: Web-Recherche via Brave/Tavily
- codebase_search: Durchsuche Backend-Code
- cli-agents_call: Rufe andere Agents auf

REGELN:
1. Nutze MCP-Tools für persistente Informationen
2. Koordiniere mit anderen Agents bei komplexen Tasks
3. Speichere wichtige Erkenntnisse im Memory
4. Antworte präzise und strukturiert
5. Bei Fehlern: Logge und versuche Alternative

OUTPUT: Text-Format, keine interaktiven Prompts.
EOFPROMPT
    fi
    log_success "System-Prompt erstellt: ${prompt_file}"
}

# ============================================================================
# STATUS ANZEIGEN
# ============================================================================
show_status() {
    log_step "Installationsstatus"
    
    echo ""
    echo -e "${BOLD}npm Pakete:${NC}"
    for pkg in "${NPM_PACKAGES[@]}"; do
        local name="${pkg##*/}"
        if npm list -g "$pkg" &>/dev/null; then
            local version
            version=$(npm list -g "$pkg" 2>/dev/null | grep "$name" | head -1 | sed 's/.*@//' || echo "?")
            echo -e "  ${GREEN}✓${NC} ${name} (${version})"
        else
            echo -e "  ${RED}✗${NC} ${name} (nicht installiert)"
        fi
    done
    
    echo ""
    echo -e "${BOLD}Wrapper-Scripts:${NC}"
    for agent in "${AGENTS[@]}"; do
        local wrapper="${BASE_DIR}/bin/${agent}-triforce"
        if [ -x "$wrapper" ]; then
            echo -e "  ${GREEN}✓${NC} ${agent}-triforce"
        else
            echo -e "  ${RED}✗${NC} ${agent}-triforce"
        fi
    done
    
    echo ""
    echo -e "${BOLD}Konfigurationen:${NC}"
    local configs=(
        "${BASE_DIR}/config/agents/claude/.claude/.claude.json"
        "${BASE_DIR}/config/agents/codex/.codex/config.toml"
        "${BASE_DIR}/config/agents/gemini/.gemini/settings.json"
        "${BASE_DIR}/config/agents/opencode/.local/share/opencode/config.json"
    )
    for cfg in "${configs[@]}"; do
        if [ -f "$cfg" ]; then
            echo -e "  ${GREEN}✓${NC} ${cfg##*/}"
        else
            echo -e "  ${RED}✗${NC} ${cfg##*/}"
        fi
    done
    
    echo ""
    echo -e "${BOLD}MCP Server:${NC}"
    if curl -s "${MCP_URL}" &>/dev/null; then
        echo -e "  ${GREEN}✓${NC} ${MCP_URL} erreichbar"
    else
        echo -e "  ${RED}✗${NC} ${MCP_URL} nicht erreichbar"
    fi
}

# ============================================================================
# DEINSTALLATION
# ============================================================================
uninstall() {
    log_step "Deinstallation"
    
    log_warn "Dies entfernt alle CLI-Agent Installationen!"
    
    if [ "$DRY_RUN" -eq 0 ]; then
        read -p "Fortfahren? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Abgebrochen."
            exit 0
        fi
    fi
    
    # npm Pakete entfernen
    log_info "Entferne npm Pakete..."
    for pkg in "${NPM_PACKAGES[@]}"; do
        run_cmd "npm uninstall -g '${pkg}' || true"
    done
    
    # Verzeichnisse entfernen
    log_info "Entferne Verzeichnisse..."
    run_cmd "rm -rf '${BASE_DIR}/bin/'*-triforce"
    run_cmd "rm -rf '${BASE_DIR}/config/agents'"
    run_cmd "rm -rf '${BASE_DIR}/logs/'*.log"
    
    log_success "Deinstallation abgeschlossen"
    log_warn "Auth-Dateien wurden NICHT entfernt: ${BASE_DIR}/auth/"
}

# ============================================================================
# HAUPTFUNKTION
# ============================================================================
main() {
    parse_args "$@"
    
    echo -e "${BOLD}${CYAN}"
    echo "╔════════════════════════════════════════════════════════════════╗"
    echo "║       CLI Coding Agents Setup Script v${SCRIPT_VERSION}                   ║"
    echo "║       TriStar/TriForce MCP Integration                        ║"
    echo "╚════════════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
    
    case "$MODE" in
        install)
            setup_directories
            setup_npm
            install_packages
            create_symlinks
            create_configs
            create_auth_file
            create_wrappers
            create_system_prompt
            
            echo ""
            log_success "Installation abgeschlossen!"
            echo ""
            echo -e "${BOLD}Nächste Schritte:${NC}"
            echo "1. API-Keys eintragen: ${YELLOW}nano ${BASE_DIR}/auth/.env.agents${NC}"
            echo "2. Shell neu laden:    ${YELLOW}source /root/.bashrc${NC}"
            echo "3. Agents einloggen:"
            echo "   - ${YELLOW}claude login${NC}"
            echo "   - ${YELLOW}codex login${NC}"
            echo "   - ${YELLOW}gemini${NC} (OAuth beim Start)"
            echo "   - ${YELLOW}opencode auth login${NC}"
            echo "4. Test: ${YELLOW}${BASE_DIR}/bin/claude-triforce \"Hello\"${NC}"
            ;;
        update)
            setup_npm
            update_packages
            create_symlinks
            log_success "Update abgeschlossen!"
            ;;
        uninstall)
            uninstall
            ;;
        status)
            show_status
            ;;
    esac
}

# ============================================================================
# SCRIPT AUSFÜHREN
# ============================================================================
main "$@"
