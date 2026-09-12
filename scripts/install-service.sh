#!/usr/bin/env bash
set -Eeuo pipefail

[[ $EUID -eq 0 ]] || { echo "Usage: sudo $0 [--mode source|package]" >&2; exit 1; }

MODE="source"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || { echo "--mode requires source or package" >&2; exit 2; }
      MODE="$2"; shift 2 ;;
    --mode=*) MODE="${1#*=}"; shift ;;
    -h|--help)
      cat <<USAGE
Usage: sudo $0 [--mode source|package]

  source   Run TriForce from a checkout (default):
           /home/zombie/triforce -> /etc/systemd/system/triforce.service

  package  Run the repository package bundle:
           /opt/triforce -> /usr/lib/systemd/system/triforce.service
           Removes only the local /etc override after backing it up.
USAGE
      exit 0 ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

case "$MODE" in
  source|package) ;;
  *) echo "Invalid mode '$MODE' (expected source or package)" >&2; exit 2 ;;
esac

TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/triforce}"
LOCAL_UNIT="/etc/systemd/system/triforce.service"
PACKAGE_UNIT="/usr/lib/systemd/system/triforce.service"
BACKUP_DIR="/var/backups/triforce-service"
MODE_MARKER_DIR="/etc/triforce"
MODE_MARKER="$MODE_MARKER_DIR/source-mode.enabled"
mkdir -p "$BACKUP_DIR" "$MODE_MARKER_DIR"

backup_local_unit() {
  if [[ -f "$LOCAL_UNIT" ]]; then
    cp -a "$LOCAL_UNIT" "$BACKUP_DIR/triforce.service.$(date +%Y%m%d-%H%M%S)"
  fi
}

if [[ "$MODE" == "source" ]]; then
  candidates=(
    "$TRIFORCE_DIR/scripts/systemd/triforce.service"
    "$TRIFORCE_DIR/packaging/systemd/triforce.service"
    "$TRIFORCE_DIR/config/triforce.service"
  )
  source_unit=""
  for f in "${candidates[@]}"; do
    if [[ -f "$f" ]]; then source_unit="$f"; break; fi
  done
  [[ -n "$source_unit" ]] || { echo "No source-mode triforce.service found in $TRIFORCE_DIR" >&2; exit 1; }
  systemd-analyze verify "$source_unit"
  backup_local_unit
  install -m 0644 "$source_unit" "$LOCAL_UNIT"
  printf 'source\n' > "$MODE_MARKER"
  chmod 0644 "$MODE_MARKER"
  echo "Installed source-mode override: $LOCAL_UNIT -> $TRIFORCE_DIR"
else
  [[ -f "$PACKAGE_UNIT" ]] || { echo "Package unit missing: $PACKAGE_UNIT" >&2; exit 1; }
  systemd-analyze verify "$PACKAGE_UNIT"
  if [[ -f "$LOCAL_UNIT" ]]; then
    backup_local_unit
    rm -f "$LOCAL_UNIT"
    echo "Removed source-mode override; backup retained in $BACKUP_DIR"
  fi
  rm -f "$MODE_MARKER"
  echo "Using package-mode unit: $PACKAGE_UNIT"
fi

systemctl daemon-reload
systemctl enable --now triforce.service
systemctl --no-pager --full status triforce.service | sed -n '1,35p'
