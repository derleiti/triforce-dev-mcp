#!/usr/bin/env bash
set -Eeuo pipefail
# TriForce Docker Wrapper - loads the canonical environment before Compose v2.

DOCKER_DIR="${TRIFORCE_DOCKER_DIR:-${HOME}/triforce/docker}"
ENV_FILE="${TRIFORCE_ENV_FILE:-${HOME}/triforce/config/triforce.env}"

[[ -d "$DOCKER_DIR" ]] || { echo "Docker directory not found: $DOCKER_DIR" >&2; exit 1; }
[[ -r "$ENV_FILE" ]] || { echo "Environment file not readable: $ENV_FILE" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker not found" >&2; exit 127; }

docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 plugin is required" >&2; exit 1; }

unset MYSQL_ROOT_PASSWORD WORDPRESS_DB_PASSWORD SEARXNG_SECRET_KEY
unset MYSQL_PASSWORD WORDPRESS_DB_HOST WORDPRESS_DB_USER

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

cd "$DOCKER_DIR" || exit 1
exec docker compose "$@"
