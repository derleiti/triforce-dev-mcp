#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
cd "$ROOT_DIR"

RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
HOME_DIR="$(getent passwd "$RUN_USER" 2>/dev/null | cut -d: -f6 || true)"
if [[ -z "$HOME_DIR" ]]; then
  if [[ "$RUN_USER" == "$(id -un)" ]]; then
    HOME_DIR="${HOME:-$ROOT_DIR}"
  else
    HOME_DIR="$ROOT_DIR"
  fi
fi
VENV_DIR="$ROOT_DIR/.venv"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_FILE="$ROOT_DIR/.env.example"
TRIFORCE_ENV_TEMPLATE="$ROOT_DIR/config/triforce.env.template"
TRIFORCE_ENV_FILE="$ROOT_DIR/config/triforce.env"
SYSTEMD_TEMPLATE="$ROOT_DIR/scripts/systemd/triforce.service"
SYSTEMD_DOCKER_TEMPLATE="$ROOT_DIR/scripts/systemd/triforce-docker.service"
FIRST_STARTUP_MARKER="$ROOT_DIR/.state/first-startup.done"

API_HOST="127.0.0.1"
API_PORT="9100"
DOMAIN="example.com"
TIMEZONE="Europe/Berlin"
LOG_LEVEL="INFO"
ENABLE_DOCKER_SERVICE="false"
ENABLE_FEDERATION_SERVICE="false"   # federation_node.py existiert nicht mehr; Node laeuft ueber triforce.service
INSTALL_SYSTEMD="true"
START_SYSTEMD="true"
INSTALL_SHORTCUT="true"
SKIP_DEPS="false"
NON_INTERACTIVE="false"
FIRST_STARTUP="false"
HOST_EXPLICIT="false"
PORT_EXPLICIT="false"
DOMAIN_EXPLICIT="false"
TIMEZONE_EXPLICIT="false"
LOG_LEVEL_EXPLICIT="false"
HEALTH_TIMEOUT=45

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { printf "%b[%s]%b %s\n" "$BLUE" "INFO" "$NC" "$*"; }
ok() { printf "%b[%s]%b %s\n" "$GREEN" "OK" "$NC" "$*"; }
warn() { printf "%b[%s]%b %s\n" "$YELLOW" "WARN" "$NC" "$*"; }
err() { printf "%b[%s]%b %s\n" "$RED" "ERROR" "$NC" "$*"; }

usage() {
  cat <<USAGE
TriForce Installer (reliable + first-startup capable)

Usage:
  ./install.sh [options]

Options:
  --host <ip>                    API bind host (default: 127.0.0.1)
  --port <port>                  API port (default: 9100)
  --domain <domain>              Base domain for config/triforce.env
  --timezone <tz>                Timezone (default: Europe/Berlin)
  --log-level <level>            LOG_LEVEL in .env (default: INFO)
  --enable-docker-service        Install/enable triforce-docker.service
  --enable-federation-service    Unsupported legacy flag (fails safely)
  --skip-systemd                 Do not install/start systemd services
  --skip-deps                    Skip pip upgrade + requirements install (requires existing venv for start)
  --no-shortcut                  Do not create desktop shortcut
  --no-start                     Install units but do not start/enable
  --first-startup                Mark and run first-startup initialization
  --non-interactive              No prompts
  --health-timeout <seconds>     Maximum time to wait for healthy backend (default: 45)
  --update                       Rerun after git pull (preserves existing config)
  -h, --help                     Show this help
USAGE
}

require_file() {
  local f="$1"
  [[ -f "$f" ]] || { err "Missing required file: $f"; exit 1; }
}

upsert_env_key() {
  local file="$1"
  local key="$2"
  local value="$3"
  local tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$value" '
    BEGIN { done=0 }
    $0 ~ "^" k "=" { print k "=" v; done=1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$file" > "$tmp"
  chmod --reference="$file" "$tmp"
  mv "$tmp" "$file"
}

# Existing installations retain all values unless a flag explicitly changes them.
ensure_env_key() {
  local file="$1" key="$2" value="$3"
  if ! grep -q "^${key}=" "$file"; then
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

secure_config() {
  local file="$1"
  [[ -f "$file" && ! -L "$file" ]] || { err "Invalid config: $file"; return 1; }
  run_sudo chown "$RUN_USER:$RUN_USER" "$file"
  run_sudo chmod 600 "$file"
  [[ -r "$file" ]] || { err "Config not readable by installer user: $file"; return 1; }
}

run_sudo() {
  if [[ "$EUID" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --host)
        [[ $# -ge 2 ]] || { err "Missing --host value"; exit 2; }
        API_HOST="$2"; shift 2 ; HOST_EXPLICIT="true" ;;
      --port)
        [[ $# -ge 2 ]] || { err "Missing --port value"; exit 2; }
        API_PORT="$2"; shift 2 ; PORT_EXPLICIT="true" ;;
      --domain)
        [[ $# -ge 2 ]] || { err "Missing --domain value"; exit 2; }
        DOMAIN="$2"; shift 2 ; DOMAIN_EXPLICIT="true" ;;
      --timezone)
        [[ $# -ge 2 ]] || { err "Missing --timezone value"; exit 2; }
        TIMEZONE="$2"; shift 2 ; TIMEZONE_EXPLICIT="true" ;;
      --log-level)
        [[ $# -ge 2 ]] || { err "Missing --log-level value"; exit 2; }
        LOG_LEVEL="$2"; shift 2 ; LOG_LEVEL_EXPLICIT="true" ;;
      --enable-docker-service)
        ENABLE_DOCKER_SERVICE="true"; shift ;;
      --enable-federation-service)
        ENABLE_FEDERATION_SERVICE="true"; shift ;;
      --disable-federation-service)
        ENABLE_FEDERATION_SERVICE="false"; shift ;;   # Rueckwaerts-Kompatibilitaet (jetzt Default)
      --skip-systemd)
        INSTALL_SYSTEMD="false"; shift ;;
      --skip-deps)
        SKIP_DEPS="true"; shift ;;
      --no-shortcut)
        INSTALL_SHORTCUT="false"; shift ;;
      --no-start)
        START_SYSTEMD="false"; shift ;;
      --first-startup)
        FIRST_STARTUP="true"; shift ;;
      --non-interactive)
        NON_INTERACTIVE="true"; shift ;;
      --update)
        NON_INTERACTIVE="true"; shift ;;
      --health-timeout)
        [[ $# -ge 2 ]] || { err "Missing --health-timeout value"; exit 2; }
        HEALTH_TIMEOUT="$2"; shift 2 ;;
      -h|--help)
        usage; exit 0 ;;
      *)
        err "Unknown option: $1"
        usage
        exit 2 ;;
    esac
  done
}

print_header() {
  echo ""
  echo "TriForce Installer"
  echo "=================="
  echo "root_dir:    $ROOT_DIR"
  echo "run_user:    $RUN_USER"
  echo "api:         http://${API_HOST}:${API_PORT}"
  echo "domain:      $DOMAIN"
  echo "timezone:    $TIMEZONE"
  echo ""
}

prepare_dirs() {
  mkdir -p "$ROOT_DIR/logs" "$ROOT_DIR/data" "$ROOT_DIR/.state"
  run_sudo chown "$RUN_USER:$RUN_USER" "$ROOT_DIR/logs" "$ROOT_DIR/data" "$ROOT_DIR/.state"
  run_sudo mkdir -p /var/tristar/{prompts,logs,memory,agents}
  run_sudo chown "$RUN_USER:$RUN_USER" /var/tristar
  run_sudo chown "$RUN_USER:$RUN_USER" "$ROOT_DIR/logs"
  ok "Directories prepared"
}

ensure_env_files() {
  require_file "$ENV_EXAMPLE_FILE"
  if [[ ! -f "$ENV_FILE" ]]; then
    install -m 600 "$ENV_EXAMPLE_FILE" "$ENV_FILE"
    ok "Created .env from .env.example"
  else
    log ".env exists: preserving existing configuration"
  fi
  secure_config "$ENV_FILE"

  local key value explicit
  for key in TRIFORCE_BIND_HOST TRIFORCE_API_PORT LOG_LEVEL TRIFORCE_DOMAIN TRIFORCE_TIMEZONE; do
    case "$key" in
      TRIFORCE_BIND_HOST) value="$API_HOST"; explicit="$HOST_EXPLICIT" ;;
      TRIFORCE_API_PORT) value="$API_PORT"; explicit="$PORT_EXPLICIT" ;;
      LOG_LEVEL) value="$LOG_LEVEL"; explicit="$LOG_LEVEL_EXPLICIT" ;;
      TRIFORCE_DOMAIN) value="$DOMAIN"; explicit="$DOMAIN_EXPLICIT" ;;
      TRIFORCE_TIMEZONE) value="$TIMEZONE"; explicit="$TIMEZONE_EXPLICIT" ;;
    esac
    if [[ "$explicit" == true ]]; then upsert_env_key "$ENV_FILE" "$key" "$value"
    else ensure_env_key "$ENV_FILE" "$key" "$value"; fi
  done

  if [[ ! -f "$TRIFORCE_ENV_FILE" ]]; then
    require_file "$TRIFORCE_ENV_TEMPLATE"
    install -m 600 "$TRIFORCE_ENV_TEMPLATE" "$TRIFORCE_ENV_FILE"
    ok "Created config/triforce.env from template"
  else
    log "config/triforce.env exists: preserving Federation secrets and node settings"
  fi
  secure_config "$TRIFORCE_ENV_FILE"
  if [[ "$DOMAIN_EXPLICIT" == true ]]; then upsert_env_key "$TRIFORCE_ENV_FILE" DOMAIN "$DOMAIN"
  else ensure_env_key "$TRIFORCE_ENV_FILE" DOMAIN "$DOMAIN"; fi
  if [[ "$TIMEZONE_EXPLICIT" == true ]]; then upsert_env_key "$TRIFORCE_ENV_FILE" TZ "$TIMEZONE"
  else ensure_env_key "$TRIFORCE_ENV_FILE" TZ "$TIMEZONE"; fi
  ensure_env_key "$TRIFORCE_ENV_FILE" INSTALL_DIR "$ROOT_DIR"
  if [[ "$HOST_EXPLICIT" == true ]]; then upsert_env_key "$TRIFORCE_ENV_FILE" SERVER_HOST "$API_HOST"; fi
  if [[ "$PORT_EXPLICIT" == true ]]; then upsert_env_key "$TRIFORCE_ENV_FILE" SERVER_PORT "$API_PORT"; fi
  secure_config "$ENV_FILE"
  secure_config "$TRIFORCE_ENV_FILE"
  # Health probes use effective values from the canonical config, not installer defaults.
  if [[ "$HOST_EXPLICIT" != true ]]; then
    API_HOST="$(sed -n 's/^SERVER_HOST=//p' "$TRIFORCE_ENV_FILE" | tail -n 1 | tr -d '\r\"' | tr -d "'" )"
    API_HOST="${API_HOST:-127.0.0.1}"
  fi
  if [[ "$PORT_EXPLICIT" != true ]]; then
    API_PORT="$(sed -n 's/^SERVER_PORT=//p' "$TRIFORCE_ENV_FILE" | tail -n 1 | tr -d '\r\"' | tr -d "'" )"
    API_PORT="${API_PORT:-9100}"
  fi
  [[ "$API_PORT" =~ ^[0-9]+$ ]] && (( API_PORT >= 1 && API_PORT <= 65535 )) || { err "Invalid configured SERVER_PORT"; return 1; }
  ok "Environment files configured without resetting existing secrets"
}

install_python_deps() {
  if [[ "$SKIP_DEPS" == "true" ]]; then
    warn "Skipping dependency install as requested"
    return
  fi

  log "Ensuring virtual environment"
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created"
  fi

  log "Upgrading pip"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip

  log "Installing requirements"
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"

  "$VENV_DIR/bin/python" -m pip check
  ok "Python dependencies installed and validated"
}

render_unit() {
  local template="$1"
  local out="$2"

  sed -e "s|User=zombie|User=${RUN_USER}|g" \
      -e "s|Group=zombie|Group=${RUN_USER}|g" \
      -e "s|-o zombie -g zombie|-o ${RUN_USER} -g ${RUN_USER}|g" \
      -e "s|/home/zombie/triforce|${ROOT_DIR}|g" \
      -e "s|/home/zombie/workspace/triforce|${ROOT_DIR}|g" \
      "$template" > "$out"
}

install_systemd_units() {
  if [[ "$INSTALL_SYSTEMD" != "true" ]]; then
    warn "Skipping systemd install as requested"
    return
  fi

  require_file "$SYSTEMD_TEMPLATE"

  local tmp_triforce tmp_docker=""
  tmp_triforce="$(mktemp)"
  render_unit "$SYSTEMD_TEMPLATE" "$tmp_triforce"

  if [[ "$ENABLE_DOCKER_SERVICE" == "true" ]]; then
    require_file "$SYSTEMD_DOCKER_TEMPLATE"
    tmp_docker="$(mktemp)"
    render_unit "$SYSTEMD_DOCKER_TEMPLATE" "$tmp_docker"
  fi



  run_sudo install -m 644 "$tmp_triforce" /etc/systemd/system/triforce.service
  if [[ "$ENABLE_DOCKER_SERVICE" == "true" ]]; then
    run_sudo install -m 644 "$tmp_docker" /etc/systemd/system/triforce-docker.service
  fi

  run_sudo systemctl daemon-reload
  run_sudo systemd-analyze verify /etc/systemd/system/triforce.service || { err "Generated unit failed verification"; return 1; }

  if [[ "$START_SYSTEMD" == "true" ]]; then
    # Existing drop-ins may contain stale WorkingDirectory/ExecStart values.
    local effective_dir effective_exec
    effective_dir="$(systemctl show triforce.service -p WorkingDirectory --value)"
    effective_exec="$(systemctl show triforce.service -p ExecStart --value)"
    if [[ "$effective_dir" != "$ROOT_DIR" ]]; then
      err "Systemd drop-in overrides WorkingDirectory: $effective_dir (expected $ROOT_DIR)"
      err "Inspect: systemctl cat triforce.service; no unsafe override will be removed automatically"
      return 1
    fi
    if [[ "$effective_exec" != *"${ROOT_DIR}/scripts/start-triforce.sh"* ]]; then
      err "Systemd ExecStart points to a different backend: $effective_exec"
      return 1
    fi
    run_sudo systemctl reset-failed triforce.service
    run_sudo systemctl enable triforce.service
    run_sudo systemctl restart triforce.service
    local i code probe_host="$API_HOST"
    [[ "$probe_host" == "0.0.0.0" || "$probe_host" == "::" ]] && probe_host="127.0.0.1"
    for ((i=0; i<HEALTH_TIMEOUT; i++)); do
      if ! systemctl is-active --quiet triforce.service; then
        sleep 1; continue
      fi
      code="$(curl --noproxy '*' -sS -o /dev/null -w '%{http_code}' --max-time 2 "http://${probe_host}:${API_PORT}/health" 2>/dev/null || true)"
      if [[ "$code" == 200 ]]; then ok "Backend healthcheck HTTP 200"; break; fi
      sleep 1
    done
    if [[ "${code:-}" != 200 ]]; then
      err "Backend did not become healthy; check journalctl -u triforce -n 80"
      return 1
    fi

    if [[ "$ENABLE_DOCKER_SERVICE" == "true" ]]; then
      run_sudo systemctl enable --now triforce-docker.service
    fi

  else
    log "Systemd units installed but not enabled or started (--no-start)"
  fi

  rm -f "$tmp_triforce"
  [[ -z "$tmp_docker" ]] || rm -f "$tmp_docker"
  ok "Systemd units installed"
}

install_desktop_shortcut() {
  if [[ "$INSTALL_SHORTCUT" != "true" ]]; then
    warn "Skipping desktop shortcut"
    return
  fi

  local web_url desktop_entry apps_dir desktop_dir
  web_url="http://localhost:${API_PORT}/"
  apps_dir="$HOME_DIR/.local/share/applications"
  desktop_dir="$HOME_DIR/Desktop"

  mkdir -p "$apps_dir"
  cat > "$apps_dir/triforce-webui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=TriForce Web UI
Comment=Open TriForce Web Interface
Exec=xdg-open ${web_url}
Terminal=false
Categories=Network;Development;
DESKTOP

  chmod 644 "$apps_dir/triforce-webui.desktop"

  if [[ -d "$desktop_dir" ]]; then
    cp "$apps_dir/triforce-webui.desktop" "$desktop_dir/TriForce Web UI.desktop"
    chmod +x "$desktop_dir/TriForce Web UI.desktop"
  fi

  run_sudo chown "$RUN_USER:$RUN_USER" "$apps_dir" 2>/dev/null || true
  ok "Desktop shortcut installed (${web_url})"
}

mark_first_startup_done() {
  if [[ "$FIRST_STARTUP" == "true" ]]; then
    mkdir -p "$(dirname "$FIRST_STARTUP_MARKER")"
    printf "installed_at=%s\n" "$(date -Iseconds)" > "$FIRST_STARTUP_MARKER"
    ok "First-startup marker written"
  fi
}

print_next_steps() {
  echo ""
  echo "Setup complete"
  echo "--------------"
  echo "Web UI:        http://localhost:${API_PORT}/"
  echo "API docs:      http://localhost:${API_PORT}/docs"
  echo "Health:        http://localhost:${API_PORT}/health"
  echo ""
  echo "Useful commands:"
  echo "  systemctl status triforce --no-pager -n 50"
  echo "  journalctl -u triforce -f"
  echo "  .venv/bin/python -m pytest -q"
  echo ""
}

main() {
  parse_args "$@"
  [[ "$API_PORT" =~ ^[0-9]+$ ]] && (( API_PORT >= 1 && API_PORT <= 65535 )) || { err "Invalid --port"; exit 2; }
  [[ "$HEALTH_TIMEOUT" =~ ^[0-9]+$ ]] && (( HEALTH_TIMEOUT >= 1 && HEALTH_TIMEOUT <= 600 )) || { err "Invalid --health-timeout"; exit 2; }
  [[ "$RUN_USER" != root ]] || { err "Run as a normal user (sudo is used only for system changes)"; exit 2; }
  [[ "$ENABLE_FEDERATION_SERVICE" != true ]] || { err "Legacy federation service unsupported; use triforce.service"; exit 2; }
  if [[ "$SKIP_DEPS" == true && ! -x "$VENV_DIR/bin/python" && "$START_SYSTEMD" == true ]]; then
    err "--skip-deps requires an existing .venv/bin/python when starting TriForce"; exit 2
  fi
  print_header

  if [[ "$NON_INTERACTIVE" != "true" && -t 0 && -t 1 ]]; then
    read -r -p "Proceed with installation? [y/N] " answer
    if [[ ! "$answer" =~ ^[Yy]$ ]]; then
      warn "Installation aborted"
      exit 0
    fi
  elif [[ "$NON_INTERACTIVE" != "true" ]]; then
    log "No interactive terminal detected; continuing automatically"
  fi

  prepare_dirs
  ensure_env_files
  install_python_deps
  install_systemd_units
  install_desktop_shortcut
  mark_first_startup_done
  print_next_steps
}

[[ "${TRIFORCE_INSTALL_LIB_ONLY:-0}" == 1 ]] || main "$@"
