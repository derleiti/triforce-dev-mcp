#!/usr/bin/env bash
# Compatibility entry point. Certificate issuance and credentials are owned by
# scripts/cf-update-certs.sh (Cloudflare DNS API token only).
# Never load historical Global API Key .env files or enable shell xtrace.
set -Eeuo pipefail
exec "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)/cf-update-certs.sh" "$@"
