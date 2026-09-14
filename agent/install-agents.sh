#!/bin/bash
################################################################################
# install-agents.sh - Installiert Codex MCP Agents in alle Docker Stacks
################################################################################
set -e

BASE_DIR="/home/zombie/workspace/triforce"
AGENT_DIR="${BASE_DIR}/agent"

echo "=== Codex MCP Agent Installation ==="
echo ""

# Funktion: Build Agent Image
build_agent() {
  local stack_dir="$1"
  local container_name="$2"
  
  echo "[*] Building agent image for ${stack_dir}..."
  cd "${BASE_DIR}/${stack_dir}"
  docker compose build codex-agent 2>/dev/null || {
    echo "[!] Build failed - check if codex-agent service exists in docker-compose.yml"
    return 1
  }
  echo "[✓] Build successful"
}

# Funktion: Start Agent
start_agent() {
  local stack_dir="$1"
  local container_name="$2"
  
  echo "[*] Starting agent in ${stack_dir}..."
  cd "${BASE_DIR}/${stack_dir}"
  docker compose up -d codex-agent 2>/dev/null || {
    echo "[!] Start failed"
    return 1
  }
  echo "[✓] Agent started: ${container_name}"
}

# Funktion: Verify MCP Connection
verify_mcp() {
  local container_name="$1"
  
  echo "[*] Verifying MCP connection from ${container_name}..."
  sleep 3
  docker exec "${container_name}" curl -sf http://host.docker.internal:9100/v1/mcp/status 2>/dev/null && {
    echo "[✓] MCP connection verified!"
    return 0
  } || {
    echo "[!] MCP connection failed - check if backend is running on port 9100"
    return 1
  }
}

echo ""
echo "=== Available Stacks ==="
echo "1. wordpress (container: wordpress_codex_agent)"
echo "2. ailinux-repo (container: repo_codex_agent)"
echo "3. mailserver (container: mail_codex_agent)"
echo ""

case "${1:-all}" in
  wordpress)
    build_agent "wordpress" "wordpress_codex_agent"
    start_agent "wordpress" "wordpress_codex_agent"
    verify_mcp "wordpress_codex_agent"
    ;;
  ailinux-repo|repo)
    build_agent "ailinux-repo" "repo_codex_agent"
    start_agent "ailinux-repo" "repo_codex_agent"
    verify_mcp "repo_codex_agent"
    ;;
  mailserver|mail)
    build_agent "mailserver" "mail_codex_agent"
    start_agent "mailserver" "mail_codex_agent"
    verify_mcp "mail_codex_agent"
    ;;
  all)
    echo "Installing agents in all stacks..."
    for stack in wordpress ailinux-repo mailserver; do
      echo ""
      echo "=== Stack: ${stack} ==="
      build_agent "${stack}" "${stack}_codex_agent" || true
      start_agent "${stack}" "${stack}_codex_agent" || true
    done
    ;;
  verify)
    echo "Verifying all agent containers..."
    for container in wordpress_codex_agent repo_codex_agent mail_codex_agent; do
      verify_mcp "${container}" || true
    done
    ;;
  *)
    echo "Usage: $0 [wordpress|ailinux-repo|mailserver|all|verify]"
    exit 1
    ;;
esac

echo ""
echo "=== Installation Complete ==="
echo "View logs: docker logs -f <container_name>"
