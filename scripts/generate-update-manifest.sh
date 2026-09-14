#!/bin/bash
# ============================================================================
# TriForce Update Manifest Generator
# Generates manifest.json with version info, file hashes, and update metadata
# ============================================================================

set -e

TRIFORCE_DIR="/home/zombie/workspace/triforce"
UPDATE_DIR="/var/www/update.ailinux.me"
VERSION_FILE="$TRIFORCE_DIR/VERSION"
MANIFEST_FILE="$UPDATE_DIR/manifest.json"

# Get version
if [[ -f "$VERSION_FILE" ]]; then
    VERSION=$(cat "$VERSION_FILE")
else
    VERSION="2.80.$(date +%Y%m%d)"
fi

# Get git commit
GIT_COMMIT=$(cd "$TRIFORCE_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
GIT_BRANCH=$(cd "$TRIFORCE_DIR" && git branch --show-current 2>/dev/null || echo "master")

# Important directories to track
TRACKED_DIRS="app config scripts"

# Generate file list with hashes
echo "Generating file hashes..."
FILE_LIST="["
FIRST=true

for dir in $TRACKED_DIRS; do
    if [[ -d "$TRIFORCE_DIR/$dir" ]]; then
        while IFS= read -r -d '' file; do
            rel_path="${file#$TRIFORCE_DIR/}"
            hash=$(sha256sum "$file" | cut -d' ' -f1)
            size=$(stat -c%s "$file")
            
            if [[ "$FIRST" == "true" ]]; then
                FIRST=false
            else
                FILE_LIST="$FILE_LIST,"
            fi
            FILE_LIST="$FILE_LIST
    {\"path\": \"$rel_path\", \"hash\": \"$hash\", \"size\": $size}"
        done < <(find "$TRIFORCE_DIR/$dir" -type f -name "*.py" -print0 2>/dev/null)
    fi
done

FILE_LIST="$FILE_LIST
  ]"

# Generate manifest
cat > "$MANIFEST_FILE" << EOF
{
  "version": "$VERSION",
  "git_commit": "$GIT_COMMIT",
  "git_branch": "$GIT_BRANCH",
  "timestamp": "$(date -Iseconds)",
  "release_date": "$(date +%Y-%m-%d)",
  "update_url": "https://update.ailinux.me/releases/$VERSION.tar.gz",
  "checksum_url": "https://update.ailinux.me/releases/$VERSION.sha256",
  "changelog_url": "https://update.ailinux.me/releases/$VERSION.changelog",
  "min_version": "2.70",
  "files": $FILE_LIST
}
EOF

echo "✅ Manifest generated: $MANIFEST_FILE"
echo "   Version: $VERSION"
echo "   Commit: $GIT_COMMIT"
echo "   Files: $(echo "$FILE_LIST" | grep -c '"path"')"
