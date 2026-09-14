#!/bin/bash
# ============================================================================
# TriForce Release Creator
# Creates release tarball and updates update.ailinux.me/server/
# ============================================================================

set -e

TRIFORCE_DIR="${TRIFORCE_DIR:-/home/zombie/workspace/triforce}"
UPDATE_DIR="/var/www/update.ailinux.me/server"

# Get version from config or argument
VERSION="${1:-}"
if [ -z "$VERSION" ]; then
    VERSION=$(grep -oP "VERSION\s*=\s*['\"]?\K[0-9]+\.[0-9]+" "${TRIFORCE_DIR}/app/config.py" 2>/dev/null || echo "2.80")
fi

echo "=== Creating TriForce Release v${VERSION} ==="

# Create tarball
RELEASE_FILE="triforce-${VERSION}.tar.gz"
cd "$TRIFORCE_DIR"

tar --exclude='.venv' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='logs/*.log' \
    --exclude='client-deploy/ailinux-client' \
    --exclude='docker/repository/repo/mirror' \
    --exclude='docker/repository/data' \
    --exclude='.backups' \
    -czf "${UPDATE_DIR}/releases/${RELEASE_FILE}" \
    app/ config/ scripts/ requirements.txt README.md CHANGELOG.md

# Create SHA256
cd "${UPDATE_DIR}/releases"
sha256sum "${RELEASE_FILE}" > "${RELEASE_FILE}.sha256"
SHA256=$(cat "${RELEASE_FILE}.sha256" | cut -d' ' -f1)
SIZE=$(stat -c%s "${RELEASE_FILE}")

# Update symlink
ln -sf "${UPDATE_DIR}/releases/${RELEASE_FILE}" "${UPDATE_DIR}/current/triforce-latest.tar.gz"

# Update server manifest
cat > "${UPDATE_DIR}/manifest.json" << EOF
{
  "version": "$(date +%Y-%m-%d)",
  "server": {
    "version": "${VERSION}",
    "codename": "TriStar",
    "channel": "stable",
    "date": "$(date +%Y-%m-%d)",
    "downloads": {
      "tarball": {
        "url": "https://update.ailinux.me/server/releases/${RELEASE_FILE}",
        "size": ${SIZE},
        "sha256": "${SHA256}"
      }
    },
    "changelog": "https://update.ailinux.me/server/CHANGELOG.md",
    "requirements": {
      "python": "3.10+",
      "os": "Ubuntu 22.04+ / Debian 12+"
    }
  },
  "sync": {
    "script_url": "https://update.ailinux.me/server/scripts/hub-sync.sh",
    "check_interval": "hourly"
  }
}
EOF

# Copy changelog
cp "${TRIFORCE_DIR}/CHANGELOG.md" "${UPDATE_DIR}/"

echo ""
echo "✅ Release v${VERSION} created"
echo "   Tarball: ${UPDATE_DIR}/releases/${RELEASE_FILE}"
echo "   Size: $(numfmt --to=iec $SIZE)"
echo "   SHA256: ${SHA256}"
echo ""
echo "All federation hubs will auto-sync within 1 hour."
