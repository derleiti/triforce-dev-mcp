#!/usr/bin/env bash

# Export AILinux public signing key for client distribution
# This should be run after generating or updating the signing key

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
REPO_ROOT="${REPO_ROOT:-$SCRIPT_DIR}"

# Signing key: shared signing-key.env if available, else built-in default.
# Keep the fallback in sync with signing-key.env (see that file for details).
[[ -r "${REPO_ROOT}/signing-key.env" ]] && . "${REPO_ROOT}/signing-key.env"
SIGNING_KEY_ID="${SIGNING_KEY_ID:-59FAE19560F5E25B}"
MIRROR_OUTPUT_ROOT="${MIRROR_OUTPUT_ROOT:-${REPO_ROOT}/repo/mirror}"
OUTPUT_FILE="${OUTPUT_FILE:-${MIRROR_OUTPUT_ROOT}/ailinux-archive-key.gpg}"

# Use repository's GNUPGHOME if available, otherwise fall back to default
DEFAULT_GNUPGHOME="${REPO_ROOT}/etc/gnupg"
if [[ -d "$DEFAULT_GNUPGHOME" && -r "$DEFAULT_GNUPGHOME" && -w "$DEFAULT_GNUPGHOME" ]]; then
  export GNUPGHOME="$DEFAULT_GNUPGHOME"
fi

echo "===[ Exporting AILinux Public Key ]==="
echo "Key ID: $SIGNING_KEY_ID"
echo "Output: $OUTPUT_FILE"
echo ""

# Check if key exists
if ! gpg --list-keys "$SIGNING_KEY_ID" >/dev/null 2>&1; then
  echo "❌ ERROR: Key $SIGNING_KEY_ID not found in GPG keyring"
  echo "Available keys:"
  gpg --list-keys
  exit 1
fi

# Export public key
echo "→ Exporting public key..."
TMP_KEY=$(mktemp)
trap "rm -f $TMP_KEY" EXIT

gpg --export "$SIGNING_KEY_ID" > "$TMP_KEY"

# Verify exported key
if ! gpg --no-default-keyring --keyring "$TMP_KEY" --list-keys "$SIGNING_KEY_ID" >/dev/null 2>&1; then
  echo "❌ ERROR: Exported key is invalid"
  exit 1
fi

# Create output directory if needed
mkdir -p "$(dirname "$OUTPUT_FILE")"

# Install the key, plus every published alias.
#
# All of these are reachable over HTTPS, so any copy left behind becomes a
# stale key that clients may install and then fail to verify with. Keep this
# list in sync with the paths add-ailinux-repo.sh and the docs hand out.
PUBLISHED_KEYS=(
  "$OUTPUT_FILE"
  # name clients install as (KEYRING_PATH in add-ailinux-repo.sh)
  "${MIRROR_OUTPUT_ROOT}/ailinux-archive-keyring.gpg"
  # compatibility aliases for older install instructions
  "${MIRROR_OUTPUT_ROOT}/ailinux-mirror-signing-key.gpg"
  # direct root downloads
  "${REPO_ROOT}/repo/ailinux-archive-key.gpg"
  "${REPO_ROOT}/repo/ailinux-mirror-signing-key.gpg"
  # copy served from inside the mirrored AILinux tree
  "${MIRROR_OUTPUT_ROOT}/repo.ailinux.me/ailinux-archive-key.gpg"
)

for dest in "${PUBLISHED_KEYS[@]}"; do
  if install -Dm0644 "$TMP_KEY" "$dest" 2>/dev/null; then
    echo "  ✓ $dest"
  else
    echo "  ⚠ could not write $dest (permissions?)" >&2
  fi
done

echo "✓ Key exported successfully"
echo ""
echo "Key details:"
gpg --no-default-keyring --keyring "$OUTPUT_FILE" --list-keys "$SIGNING_KEY_ID"

echo ""
echo "===[ Success! ]==="
echo "Public key is ready for distribution at:"
echo "  $OUTPUT_FILE"
echo ""
echo "Clients can install it with:"
echo "  curl -fsSL \"https://repo.ailinux.me/mirror/ailinux-archive-key.gpg\" | sudo tee /usr/share/keyrings/ailinux-archive-keyring.gpg >/dev/null"
