#!/bin/bash
# ============================================================================
# TriForce Release Publisher
# One-command release: bump version, create release, push to update server
# Usage: ./publish-release.sh [patch|minor|major] [message]
# ============================================================================

set -e

TRIFORCE_DIR="/home/zombie/workspace/triforce"
cd "$TRIFORCE_DIR"

# Get bump type
BUMP="${1:-patch}"
MSG="${2:-Release update}"

# Read current version
CURRENT=$(cat VERSION 2>/dev/null || echo "2.80.0")
IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# Bump version
case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch|*) PATCH=$((PATCH + 1)) ;;
esac

NEW_VERSION="$MAJOR.$MINOR.$PATCH"
echo "$NEW_VERSION" > VERSION

echo "=== Publishing TriForce $NEW_VERSION ==="
echo "Previous: $CURRENT"
echo "Message: $MSG"
echo ""

# Git commit
git add -A
git commit -m "v$NEW_VERSION: $MSG" || true
git tag -a "v$NEW_VERSION" -m "$MSG" 2>/dev/null || true

# Create release
$TRIFORCE_DIR/scripts/create-release.sh "$NEW_VERSION"

# Show status
echo ""
echo "=== Release Published ==="
echo "Version: $NEW_VERSION"
echo "Manifest: https://update.ailinux.me/manifest.json"
echo "Download: https://update.ailinux.me/releases/$NEW_VERSION.tar.gz"
echo ""
echo "Nodes will auto-update within 30 minutes, or run manually:"
echo "  /home/zombie/workspace/triforce/scripts/triforce-update.sh --restart"
