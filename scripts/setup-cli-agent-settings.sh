#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility helper for the current TriForce CLI-agent layout.
# Legacy versions of this script wrote stale root-only configs and obsolete
# model names. Current TriForce owns agent configuration under config/agents.
TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
AGENT_DIR="$TRIFORCE_DIR/config/agents"

[[ -d "$TRIFORCE_DIR" ]] || { echo "TriForce directory not found: $TRIFORCE_DIR" >&2; exit 1; }
mkdir -p "$AGENT_DIR"

echo "Current TriForce agent configuration: $AGENT_DIR"
find "$AGENT_DIR" -maxdepth 3 -type f -printf '  %p\n' 2>/dev/null | sort || true

echo
echo "This compatibility script no longer overwrites /root/.claude, /root/.codex,"
echo "legacy root-only agent configs or obsolete runtime directories."
echo "Use setup-cli-agents.sh / install-cli-tools.sh and the TriForce config/agents layer instead."
