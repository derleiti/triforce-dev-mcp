#!/usr/bin/env bash
set -euo pipefail
[[ $EUID == 0 ]] || { echo 'requires root for overlay fixture' >&2; exit 1; }
SOURCE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_PARENT="$SOURCE_ROOT/repo/.mirror-transactions"
mkdir -p "$TMP_PARENT"
FIXTURE="$(mktemp -d "$TMP_PARENT/fixture-XXXXXXXX")"
GPG_TMP="$(mktemp -d /tmp/mirror-fixture-gpg-XXXXXXXX)"
ACTIVE_STAGE=""
cleanup() {
    if [[ -n "$ACTIVE_STAGE" ]] && mountpoint -q "$ACTIVE_STAGE"; then umount "$ACTIVE_STAGE"; fi
    rm -rf "$FIXTURE" "$GPG_TMP"
}
trap cleanup EXIT
mkdir -p "$FIXTURE/repo/mirror/example.test/apt/pool" "$FIXTURE/repo/mirror/example.test/apt/dists/stable/main/binary-amd64" "$FIXTURE/log"
chmod 700 "$GPG_TMP"
cp "$SOURCE_ROOT/mirror-generations.py" "$SOURCE_ROOT/validate-mirror-packages.py" "$SOURCE_ROOT/validate-signed-metadata.py" "$FIXTURE/"
export GNUPGHOME="$GPG_TMP"
gpg --batch --pinentry-mode loopback --passphrase '' --quick-gen-key 'Mirror Fixture Test <fixture@example.test>' ed25519 sign 0 >/dev/null 2>&1
gpg --export > "$FIXTURE/repo/mirror/ailinux-archive-key.gpg"
write_index() {
    local mirror="$1" name="$2" payload="$3"
    local repo="$mirror/example.test/apt"
    local fname="pool/$name"
    printf '%s' "$payload" > "$repo/$fname"
    cat > "$repo/dists/stable/main/binary-amd64/Packages" <<EOF
Package: sample
Version: 1.0
Architecture: amd64
Filename: $fname
Size: $(stat -c '%s' "$repo/$fname")
SHA256: $(sha256sum "$repo/$fname" | cut -d' ' -f1)

EOF
    local suite="$repo/dists/stable"
    {
      echo 'Origin: Fixture'
      echo 'Suite: stable'
      echo 'SHA256:'
      printf ' %s %s %s\n' "$(sha256sum "$suite/main/binary-amd64/Packages" | cut -d' ' -f1)" "$(stat -c '%s' "$suite/main/binary-amd64/Packages")" 'main/binary-amd64/Packages'
    } > "$suite/Release"
    gpg --batch --yes --clearsign -o "$suite/InRelease" "$suite/Release"
    gpg --batch --yes --detach-sign -o "$suite/Release.gpg" "$suite/Release"
}
write_index "$FIXTURE/repo/mirror" 'sample-1.deb' 'old-payload'
python3 -B "$FIXTURE/mirror-generations.py" bootstrap --repo-dir "$FIXTURE/repo"
ORIGINAL_GENERATION="$(readlink "$FIXTURE/repo/mirror")"
source "$SOURCE_ROOT/update-mirror.sh"
REPO_ROOT="$FIXTURE"
LIVE_MIRROR_ROOT="$FIXTURE/repo/mirror"
ATOMIC_GENERATOR="$FIXTURE/mirror-generations.py"
LOGFILE="$FIXTURE/log/atomic-test.log"
step_verify_local_repo() { return 0; }
prepare() {
    local id="$1"
    ATOMIC_TXN_DIR="$FIXTURE/repo/.mirror-transactions/$id"
    mkdir -p "$ATOMIC_TXN_DIR"/{upper,work,merged}
    mount -t overlay overlay -o "lowerdir=$(readlink -f "$LIVE_MIRROR_ROOT"),upperdir=$ATOMIC_TXN_DIR/upper,workdir=$ATOMIC_TXN_DIR/work" "$ATOMIC_TXN_DIR/merged"
    ACTIVE_STAGE="$ATOMIC_TXN_DIR/merged"
    MIRROR_ROOT="$ACTIVE_STAGE"
    ATOMIC_STAGE_ACTIVE=1
    export MIRROR_ROOT
}
finish() {
    umount "$ACTIVE_STAGE"
    ACTIVE_STAGE=''
    rm -rf "$ATOMIC_TXN_DIR"
}
prepare 'txn-success'
write_index "$MIRROR_ROOT" 'sample-2.deb' 'new-payload'
step_publish_atomic_mirror
[[ "$(cat "$LIVE_MIRROR_ROOT/example.test/apt/pool/sample-2.deb")" == 'new-payload' ]]
[[ "$(cat "$FIXTURE/repo/$ORIGINAL_GENERATION/example.test/apt/pool/sample-1.deb")" == 'old-payload' ]]
SUCCESS_GENERATION="$(readlink "$LIVE_MIRROR_ROOT")"
finish
printf 'PASS valid candidate published atomically and original remained unchanged\n'
prepare 'txn-fail-public'
write_index "$MIRROR_ROOT" 'sample-3.deb' 'third-payload'
step_verify_local_repo() { return 1; }
if step_publish_atomic_mirror; then echo 'FAIL expected public check failure' >&2; exit 1; fi
[[ "$(readlink "$LIVE_MIRROR_ROOT")" == "$SUCCESS_GENERATION" ]]
[[ "$(cat "$LIVE_MIRROR_ROOT/example.test/apt/pool/sample-2.deb")" == 'new-payload' ]]
[[ ! -e "$LIVE_MIRROR_ROOT/example.test/apt/pool/sample-3.deb" ]]
finish
printf 'PASS failed public check restored prior generation\n'
prepare 'txn-fail-index'
write_index "$MIRROR_ROOT" 'sample-4.deb' 'fourth-payload'
sed -i 's/^SHA256: .*/SHA256: 0000000000000000000000000000000000000000000000000000000000000000/' "$MIRROR_ROOT/example.test/apt/dists/stable/main/binary-amd64/Packages"
step_verify_local_repo() { return 0; }
if step_publish_atomic_mirror; then echo 'FAIL expected hash check failure' >&2; exit 1; fi
[[ "$(readlink "$LIVE_MIRROR_ROOT")" == "$SUCCESS_GENERATION" ]]
finish
printf 'PASS invalid candidate rejected before switching live\n'
prepare 'txn-prune-truncated'
# Corrupt only the COW upper copy; the published lower tree stays valid.
printf 'damaged' > "$MIRROR_ROOT/example.test/apt/pool/sample-2.deb"
python3 -B "$SOURCE_ROOT/prune-truncated-packages.py" --mirror-root "$MIRROR_ROOT"
[[ ! -e "$MIRROR_ROOT/example.test/apt/pool/sample-2.deb" ]]
[[ "$(cat "$LIVE_MIRROR_ROOT/example.test/apt/pool/sample-2.deb")" == 'new-payload' ]]
finish
printf 'PASS quarantine removes incomplete staging artifact without changing live file\n'
echo ATOMIC_MIRROR_FIXTURE_OK
