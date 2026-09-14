#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${TRIFORCE_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
DOCKER_DIR="${TRIFORCE_DOCKER_DIR:-$ROOT/docker}"
ENV_FILE="${TRIFORCE_ENV_FILE:-$ROOT/config/triforce.env}"
[[ -d "$DOCKER_DIR" ]] || { echo "Docker directory not found: $DOCKER_DIR" >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "Environment file not readable: $ENV_FILE" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker not found" >&2; exit 127; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required" >&2; exit 1; }
cd "$DOCKER_DIR"
exec env -i \
  HOME="${HOME:-/home/zombie}" \
  USER="${USER:-zombie}" \
  PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
  DOCKER_HOST="${DOCKER_HOST:-}" \
  XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
  docker compose --env-file "$ENV_FILE" "$@"
