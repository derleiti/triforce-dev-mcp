#!/usr/bin/env bash
# ============================================================================
# AILinux Repository Permission Healer
# ============================================================================
# Fixes file permissions and ownership after Docker apt-mirror runs.
# Docker creates files as root, this script fixes ownership for host access.
#
# Usage:
#   ./heal-perms.sh           # Fix permissions (uses sudo for chown)
#   sudo ./heal-perms.sh      # Run as root (no sudo needed internally)
# ============================================================================

set -euo pipefail

# Auto-detect script directory as REPO root
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO="${REPO:-$SCRIPT_DIR}"
MIR="$REPO/repo/mirror"
GNUP="$REPO/etc/gnupg"

# Get current user (even if running with sudo)
CURRENT_USER="${REPO_MIRROR_OWNER:-${SUDO_USER:-$(stat -c %U "$REPO")}}"
CURRENT_GROUP="$(id -gn "$CURRENT_USER")"

echo "=============================================="
echo "  AILinux Permission Healer"
echo "=============================================="
echo "Repository: $REPO"
echo "Mirror:     $MIR"
echo "Target:     ${CURRENT_USER}:${CURRENT_GROUP}"
echo ""

# Run a command as root (directly if we already are, else via sudo)
as_root() {
    if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

# Chown only the entries that are not already owned correctly.
#
# The mirror holds ~180k inodes but virtually none change between runs, so an
# unconditional `chown -R` rewrites every inode for nothing. Filtering with
# `! -user/! -group` and batching with `-exec ... +` keeps this near-instant.
do_chown() {
    local target="$1"
    as_root find "$target" \( ! -user "$CURRENT_USER" -o ! -group "$CURRENT_GROUP" \) \
        -exec chown "${CURRENT_USER}:${CURRENT_GROUP}" {} + 2>/dev/null || true
}

echo "[1/6] Fixing Mirror ownership..."
if [[ -d "$MIR" ]]; then
    do_chown "$MIR"
    echo "      -> Changed to ${CURRENT_USER}:${CURRENT_GROUP}"
else
    mkdir -p "$MIR"
    echo "      -> Created (was missing)"
fi

echo "[2/6] Fixing Mirror permissions..."
# Directories 755, files 644 (world-readable for NGINX).
#
# Only touch entries whose mode is already wrong, and batch with `-exec ... +`.
# `-exec ... \;` forks one chmod per path, which on this tree (~180k inodes)
# costs minutes per run; this variant is ~2000x faster and does the same work.
find "$MIR" -type d ! -perm 755 -exec chmod 755 {} + 2>/dev/null || true
find "$MIR" -type f ! -perm 644 -exec chmod 644 {} + 2>/dev/null || true
echo "      -> dirs=755, files=644"

echo "[3/6] Fixing GNUPG home..."
mkdir -p "$GNUP"
chmod 700 "$GNUP"
find "$GNUP" -type f ! -perm 600 -exec chmod 600 {} + 2>/dev/null || true
echo "      -> dir=700, files=600"

echo "[4/6] Fixing key and index files..."
[[ -f "$MIR/ailinux-archive-key.gpg" ]] && chmod 644 "$MIR/ailinux-archive-key.gpg"
[[ -f "$MIR/index.html" ]] && chmod 644 "$MIR/index.html"
echo "      -> 644"

echo "[5/6] Making scripts executable..."
find "$REPO" -maxdepth 1 -type f -name "*.sh" ! -perm 755 -exec chmod 755 {} + 2>/dev/null || true
echo "      -> 755"

echo "[6/6] Setting up log directory..."
mkdir -p "$REPO/log"
touch "$REPO/log/update-mirror.log"
as_root chown "${CURRENT_USER}:${CURRENT_GROUP}" "$REPO/log/update-mirror.log"
chmod 664 "$REPO/log/update-mirror.log"
echo "      -> 664"

echo ""
echo "Done! Permissions fixed for user: ${CURRENT_USER}"
