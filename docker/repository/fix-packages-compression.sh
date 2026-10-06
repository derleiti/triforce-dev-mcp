#!/usr/bin/env bash
# ============================================================================
# Fix Packages Compression v2.0
# ============================================================================
# Regenerates compressed Packages files (.gz/.xz) for hash consistency
# This fixes issues where apt-mirror downloads may have different compression
# than what's listed in the Release file.
#
# The script dynamically discovers all repositories instead of hardcoding paths.
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${REPO_ROOT:-$SCRIPT_DIR}"
MIRROR_ROOT="${MIRROR_ROOT:-${REPO_ROOT}/repo/mirror}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()      { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }

echo "=============================================="
echo "  Fix Packages Compression v2.0"
echo "=============================================="
echo "Mirror Root: $MIRROR_ROOT"
echo ""

if [[ ! -d "$MIRROR_ROOT" ]]; then
    log_warn "Mirror root not found: $MIRROR_ROOT"
    exit 0
fi

# Counters
total_gz=0
total_xz=0
total_repos=0

# apt-mirror may create zero-byte .deb placeholders when an upstream Packages
# record contains a malformed Filename. A Debian package can never legitimately
# be empty, so these files are safe to remove before publishing metadata.
zero_debs=$(find "$MIRROR_ROOT" -type f -name '*.deb' -size 0c -print 2>/dev/null | wc -l)
if [[ "$zero_debs" -gt 0 ]]; then
    find "$MIRROR_ROOT" -type f -name '*.deb' -size 0c -delete
    log_warn "Removed $zero_debs zero-byte .deb placeholder(s) from mirror"
fi

# Find all Packages files dynamically
log "Searching for Packages files..."

while IFS= read -r -d '' pkg_file; do
    pkg_dir="$(dirname "$pkg_file")"
    rel_path="${pkg_file#$MIRROR_ROOT/}"

    # Skip if Packages file is empty
    if [[ ! -s "$pkg_file" ]]; then
        continue
    fi

    # APT aborts the complete package-list merge when even one stanza lacks a
    # Package: field. Some third-party repositories have shipped malformed
    # trailing records (notably NVIDIA CUDA). Never publish those records into
    # the AILinux mirror. Keep valid stanzas byte-for-byte at field level and
    # replace the index atomically before recompressing/signing.
    sanitized="${pkg_file}.sanitized.$$"
    bad_stanzas=$(awk 'BEGIN { RS=""; bad=0 } { if ($0 !~ /(^|\n)Package:[[:space:]]*[^[:space:]]/) bad++ } END { print bad }' "$pkg_file")
    if [[ "$bad_stanzas" -gt 0 ]]; then
        awk 'BEGIN { RS=""; ORS="\n\n" } /(^|\n)Package:[[:space:]]*[^[:space:]]/ { print }' "$pkg_file" > "$sanitized"
        if [[ ! -s "$sanitized" ]]; then
            rm -f "$sanitized"
            log_warn "Refusing to replace ${rel_path}: sanitizing removed every stanza"
            continue
        fi
        mv "$sanitized" "$pkg_file"
        log_warn "Removed $bad_stanzas malformed package stanza(s): $rel_path"
    else
        rm -f "$sanitized"
    fi

    ((total_repos+=1))

    # Regenerate .gz
    if gzip -9 -c "$pkg_file" > "${pkg_file}.gz.new" 2>/dev/null; then
        mv "${pkg_file}.gz.new" "${pkg_file}.gz"
        ((total_gz+=1))
    else
        rm -f "${pkg_file}.gz.new"
        log_warn "Failed to compress: ${rel_path}.gz"
    fi

    # Regenerate .xz
    if xz -9 -c "$pkg_file" > "${pkg_file}.xz.new" 2>/dev/null; then
        mv "${pkg_file}.xz.new" "${pkg_file}.xz"
        ((total_xz+=1))
    else
        rm -f "${pkg_file}.xz.new"
        log_warn "Failed to compress: ${rel_path}.xz"
    fi

done < <(find "$MIRROR_ROOT" -type f -name "Packages" -print0 2>/dev/null)

echo ""
log_ok "Processed $total_repos Packages files"
log_ok "Regenerated $total_gz .gz files"
log_ok "Regenerated $total_xz .xz files"

# Also handle Sources files for source packages
log "Searching for Sources files..."
total_sources=0

while IFS= read -r -d '' src_file; do
    if [[ ! -s "$src_file" ]]; then
        continue
    fi

    ((total_sources+=1))

    # Regenerate .gz
    if gzip -9 -c "$src_file" > "${src_file}.gz.new" 2>/dev/null; then
        mv "${src_file}.gz.new" "${src_file}.gz"
    fi

    # Regenerate .xz
    if xz -9 -c "$src_file" > "${src_file}.xz.new" 2>/dev/null; then
        mv "${src_file}.xz.new" "${src_file}.xz"
    fi

done < <(find "$MIRROR_ROOT" -type f -name "Sources" -print0 2>/dev/null)

if [[ $total_sources -gt 0 ]]; then
    log_ok "Processed $total_sources Sources files"
fi

# Handle Contents files
log "Searching for Contents files..."
total_contents=0

while IFS= read -r -d '' contents_file; do
    if [[ ! -s "$contents_file" ]]; then
        continue
    fi

    ((total_contents+=1))

    # Regenerate .gz only (Contents usually only has .gz)
    if gzip -9 -c "$contents_file" > "${contents_file}.gz.new" 2>/dev/null; then
        mv "${contents_file}.gz.new" "${contents_file}.gz"
    fi

done < <(find "$MIRROR_ROOT" -type f -name "Contents-*" ! -name "*.gz" ! -name "*.xz" -print0 2>/dev/null)

if [[ $total_contents -gt 0 ]]; then
    log_ok "Processed $total_contents Contents files"
fi

echo ""
echo "=============================================="
log_ok "Compression fix completed"
echo "=============================================="
