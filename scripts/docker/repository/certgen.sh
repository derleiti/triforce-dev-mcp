#!/usr/bin/env bash
# AILinux repository certificate compatibility entrypoint.
# Certificates for web/API/mail/repository are renewed centrally using the
# scoped CLOUDFLARE_DNS_API_TOKEN. Do not rewrite Certbot credentials here,
# install Snap, log environment secrets or duplicate private keys into Git.
set -Eeuo pipefail
REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../../.." && pwd)"
exec "${REPO_ROOT}/scripts/cf-update-certs.sh" "$@"
