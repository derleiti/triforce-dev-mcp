#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_ROOT=$(dirname "$SCRIPT_DIR")
ROOT="${TRIFORCE_DOCKER_ROOT:-$DEFAULT_ROOT}"
ENV_FILE="${TRIFORCE_CONFIG_FILE:-/etc/triforce/triforce.env}"
COMPOSE="$ROOT/docker-compose.yml"
ACTION="${1:-status}"
PROFILE="${2:-all}"
case "$ACTION" in validate|status|up|down|restart|pull|logs) ;; *) echo "invalid action" >&2; exit 64;; esac
case "$PROFILE" in all|redis|wordpress|flarum|searxng|n8n|repository|mailserver) ;; *) echo "invalid profile" >&2; exit 64;; esac
[ -f "$COMPOSE" ] || { echo "compose missing: $COMPOSE" >&2; exit 66; }
[ -f "$ENV_FILE" ] || { echo "config missing: $ENV_FILE" >&2; exit 66; }
command -v docker >/dev/null 2>&1 || { echo "docker missing" >&2; exit 69; }
set -- docker compose --env-file "$ENV_FILE" -f "$COMPOSE"
if [ "$PROFILE" = all ]; then
  set -- "$@" --profile redis --profile wordpress --profile flarum --profile searxng --profile n8n --profile repository --profile mailserver
else
  set -- "$@" --profile "$PROFILE"
fi
case "$ACTION" in
  validate) set -- "$@" config --quiet ;;
  status) set -- "$@" ps --all ;;
  up) set -- "$@" up -d --remove-orphans ;;
  down) set -- "$@" down ;;
  restart) set -- "$@" restart ;;
  pull) set -- "$@" pull ;;
  logs) set -- "$@" logs --tail "${DOCKER_LOG_TAIL:-100}" ;;
esac
exec "$@"
