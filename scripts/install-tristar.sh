#!/usr/bin/env bash
#
# TriStar v2.80 Installation Script
# Installs and configures the TriStar Chain Orchestration System
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRISTAR_BASE="/var/tristar"
SYSTEMD_DIR="/etc/systemd/system"

echo "╔═══════════════════════════════════════════════════╗"
echo "║      TriStar v2.80 Installation Script            ║"
echo "║      Multi-LLM Chain Orchestration System         ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""

# Check if running as root or with sudo
if [ "$EUID" -ne 0 ]; then
    echo "Please run with sudo: sudo ./install-tristar.sh"
    exit 1
fi

echo "→ Creating TriStar directories..."
mkdir -p "${TRISTAR_BASE}"/{projects,logs,reports,prompts,agents,autoprompts/{profiles,projects}}

echo "→ Setting permissions..."
chown -R zombie:docker "${TRISTAR_BASE}"
chmod -R 755 "${TRISTAR_BASE}"

echo "→ Copying prompt files..."
if [ -d "${SCRIPT_DIR}/var/tristar/prompts" ]; then
    cp -r "${SCRIPT_DIR}/var/tristar/prompts/"* "${TRISTAR_BASE}/prompts/" 2>/dev/null || true
fi

# Copy from local /var/tristar if they exist
if [ -f "/var/tristar/prompts/bootstrap.txt" ]; then
    echo "  Prompts already installed"
else
    echo "  Creating default prompts..."
    # Bootstrap prompt was already created by the script
fi

echo "→ Installing systemd services..."
for service in "${SCRIPT_DIR}/systemd/"*.service; do
    if [ -f "$service" ]; then
        name=$(basename "$service")
        echo "  Installing $name..."
        cp "$service" "${SYSTEMD_DIR}/"
    fi
done

echo "→ Reloading systemd..."
systemctl daemon-reload

echo "→ Creating tristar CLI symlink..."
ln -sf "${SCRIPT_DIR}/bin/tristar" /usr/local/bin/tristar
chmod +x "${SCRIPT_DIR}/bin/tristar"

echo "→ Installing Python dependencies..."
if [ -f "${SCRIPT_DIR}/requirements.txt" ]; then
    pip3 install -q aiofiles pyyaml rich httpx 2>/dev/null || true
fi

echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║           Installation Complete!                   ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""
echo "Available services:"
echo "  - tristar-kernel.service     (Main orchestration engine)"
echo "  - gemini-lead.service        (Lead coordinator)"
echo "  - claude-mcp.service         (Coding worker)"
echo "  - deepseek-mcp.service       (Algorithm worker)"
echo "  - qwen-mcp.service           (Multilingual worker)"
echo "  - mistral-mcp.service        (Security reviewer)"
echo "  - cogito-mcp.service         (Reasoning reviewer)"
echo "  - kimi-mcp.service           (Research lead)"
echo "  - nova-mcp.service           (Admin agent)"
echo "  - codex-mcp.service          (Code reviewer)"
echo ""
echo "To start TriStar:"
echo "  sudo systemctl start tristar-kernel"
echo ""
echo "To start all agents:"
echo "  sudo systemctl start gemini-lead claude-mcp deepseek-mcp"
echo ""
echo "CLI usage:"
echo "  tristar chain \"Your task here\""
echo "  tristar status"
echo "  tristar --help"
echo ""
echo "API endpoints:"
echo "  https://api.ailinux.me/v1/tristar/status"
echo "  https://api.ailinux.me/v1/tristar/chain/start"
echo ""
