#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TRIFORCE_DIR="${TRIFORCE_DIR:-$(cd -- "$SCRIPT_DIR/../.." && pwd)}"
ENV_FILE="${TRIFORCE_ENV_FILE:-$TRIFORCE_DIR/config/triforce.env}"
STACKS=(wordpress flarum searxng n8n repository mailserver)

usage() {
  echo "Usage: $0 {start|stop|restart|status|logs|config|pull|apply} [stack|all]" >&2
  echo "Stacks: ${STACKS[*]}" >&2
  exit 2
}

[[ -r "$ENV_FILE" ]] || { echo "Environment file not readable: $ENV_FILE" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "docker not found" >&2; exit 127; }
docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is required" >&2; exit 1; }

compose() {
  local stack=$1; shift
  local dir="$TRIFORCE_DIR/docker/$stack"
  [[ -f "$dir/docker-compose.yml" ]] || { echo "Missing compose file: $dir/docker-compose.yml" >&2; return 1; }
  (
    cd "$dir"
    env -i \
      HOME="${HOME:-/home/zombie}" \
      USER="${USER:-zombie}" \
      PATH="${PATH:-/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin}" \
      DOCKER_HOST="${DOCKER_HOST:-}" \
      XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-}" \
      docker compose --env-file "$ENV_FILE" "$@"
  )
}

ensure_wordpress_redis_config() {
  # The Redis Object Cache drop-in is loaded before normal plugins, so the
  # password must be defined in wp-config.php itself. No secret value is
  # persisted: the helper writes only a getenv_docker() reference.
  compose wordpress exec -T wordpress_fpm php /usr/local/share/ailinux/ensure-redis-config.php
}

do_action() {
  local stack=$1 action=$2
  echo "==> $stack: $action"
  case "$action" in
    start|restart|apply)
      compose "$stack" up -d --remove-orphans
      [[ "$stack" != wordpress ]] || ensure_wordpress_redis_config
      ;;
    stop)          compose "$stack" stop ;;
    status)        compose "$stack" ps ;;
    logs)          compose "$stack" logs --tail=100 ;;
    config)        compose "$stack" config --quiet ;;
    pull)          compose "$stack" pull ;;
    *) usage ;;
  esac
}

ACTION=${1:-}
STACK=${2:-all}
[[ -n "$ACTION" ]] || usage
case "$ACTION" in start|stop|restart|status|logs|config|pull|apply) ;; *) usage ;; esac

if [[ "$STACK" == all ]]; then
  for s in "${STACKS[@]}"; do do_action "$s" "$ACTION"; done
else
  found=0
  for s in "${STACKS[@]}"; do [[ "$s" == "$STACK" ]] && found=1; done
  [[ $found -eq 1 ]] || usage
  do_action "$STACK" "$ACTION"
fi
