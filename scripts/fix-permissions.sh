#!/usr/bin/env bash
################################################################################
# fix-permissions.sh - Corrects file permissions for AILinux Backend
# RUN AS ROOT: sudo ./fix-permissions.sh
################################################################################
set -Eeuo pipefail

# Dynamisch: User aus Verzeichnis-Owner ermitteln
OWNER="${TRIFORCE_USER:-${SUDO_USER:-zombie}}"
GROUP="${TRIFORCE_GROUP:-$OWNER}"
BASE_DIR="${TRIFORCE_DIR:-$(getent passwd "$OWNER" | cut -d: -f6)/triforce}"
DOCKER_GROUP="docker"

echo "=== AILinux Backend Permission Fix ==="
echo "Base: $BASE_DIR"
echo "Owner: $OWNER:$GROUP"

if [[ $EUID -ne 0 ]]; then
   echo "ERROR: Must be run as root!"
   exit 1
fi

[[ ! -d "$BASE_DIR" ]] && { echo "ERROR: $BASE_DIR not found!"; exit 1; }

cd "$BASE_DIR"

# 1. Base Ownership (OHNE node_modules, .venv, .git - die dauern ewig)
echo "[1/5] Setting base ownership to ${OWNER}:${GROUP} (excluding large dirs)..."
find . -maxdepth 1 ! -name "node_modules" ! -name ".venv" ! -name ".git" ! -name "docker" -exec chown -R "${OWNER}:${GROUP}" {} \;

# 2. Executable Scripts
echo "[2/5] Setting executable permissions..."
find . -path ./node_modules -prune -o -path ./.venv -prune -o -type f -name "*.sh" -exec chmod 755 {} \;
chmod 755 scripts/start-backend.sh 2>/dev/null || true

# 3. Docker & WordPress Directories
echo "[3/5] Setting Docker/WP permissions..."
if [[ -d "docker/wordpress/html" ]]; then
    echo "  -> Setting WordPress webroot ownership to 33:33..."
    chown -R 33:33 docker/wordpress/html
    find docker/wordpress/html -type d -exec chmod 755 {} \;
    find docker/wordpress/html -type f -exec chmod 644 {} \;
fi

if [[ -d "docker/repository" ]]; then
    chown -R "${OWNER}:${DOCKER_GROUP}" docker/repository
fi

# 4. Runtime & Logs
echo "[4/5] Setting runtime permissions..."
for dir in logs runtime config/agents; do
    if [[ -d "$dir" ]]; then
        chown -R "${OWNER}:${GROUP}" "$dir"
        chmod -R 775 "$dir"
    fi
done

# 5. Secure Sensitive Files
echo "[5/5] Securing secrets..."
find . -path ./node_modules -prune -o -path ./.venv -prune -o -name ".env" -exec chmod 600 {} \;
find . -path ./node_modules -prune -o -path ./.venv -prune -o -name "*.key" -exec chmod 600 {} \;
find config -type f \( -name "*.env" -o -name "*.key" -o -name "*.pem" \) -exec chmod 600 {} \; 2>/dev/null || true

echo "=== Done ==="
echo "Restart the backend: sudo systemctl restart triforce.service"
