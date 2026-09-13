#!/bin/bash
#
# AILinux Client Repository Updater
# ==================================
# Kopiert .deb nach Repository und generiert Packages/Release
#
# Verwendung:
#   ./update-client-repo.sh                    # Aktualisiert mit bestehendem .deb
#   ./update-client-repo.sh /path/to/new.deb   # Aktualisiert mit neuem .deb
#

set -e

REPO_BASE="/home/zombie/workspace/triforce/docker/repository/repo/mirror/repo.ailinux.me"
CLIENT_RELEASES="/home/zombie/workspace/triforce/client-releases/latest"
POOL_DIR="$REPO_BASE/pool/main/a/aicoder"
DISTS_DIR="$REPO_BASE/dists/noble"
GPG_KEY_ID="ailinux"  # Falls signiert werden soll

echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║       AILinux Client Repository Updater                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

# 1. Pool-Struktur sicherstellen
mkdir -p "$POOL_DIR"
mkdir -p "$DISTS_DIR/main/binary-amd64"
mkdir -p "$DISTS_DIR/main/binary-i386"

# 2. Neues .deb kopieren (falls angegeben)
if [ -n "$1" ] && [ -f "$1" ]; then
    echo ">>> Kopiere neues Paket: $1"
    cp "$1" "$POOL_DIR/"
elif [ -d "$CLIENT_RELEASES" ]; then
    # Hole neuestes .deb aus client-releases
    LATEST_DEB=$(ls -t "$CLIENT_RELEASES"/*.deb 2>/dev/null | head -1)
    if [ -n "$LATEST_DEB" ]; then
        echo ">>> Kopiere aktuelles Paket: $(basename $LATEST_DEB)"
        cp "$LATEST_DEB" "$POOL_DIR/"
    fi
fi

# 3. Packages generieren
echo ">>> Generiere Packages für amd64..."
cd "$REPO_BASE"

# Packages für amd64
dpkg-scanpackages --arch amd64 pool/ > "$DISTS_DIR/main/binary-amd64/Packages"
gzip -9 -c "$DISTS_DIR/main/binary-amd64/Packages" > "$DISTS_DIR/main/binary-amd64/Packages.gz"
xz -c "$DISTS_DIR/main/binary-amd64/Packages" > "$DISTS_DIR/main/binary-amd64/Packages.xz" 2>/dev/null || true

# Leere Packages für i386 (keine 32-bit Pakete)
echo "" > "$DISTS_DIR/main/binary-i386/Packages"
gzip -9 -c "$DISTS_DIR/main/binary-i386/Packages" > "$DISTS_DIR/main/binary-i386/Packages.gz"

# 4. Release generieren
echo ">>> Generiere Release..."

RELEASE_FILE="$DISTS_DIR/Release"
DATE=$(date -Ru)

cat > "$RELEASE_FILE" << RELEASEEOF
Origin: AILinux
Label: AILinux Client Repository
Suite: noble
Codename: noble
Version: 24.04
Architectures: amd64 i386
Components: main
Description: AILinux Client packages for Ubuntu 24.04 Noble
Date: $DATE
RELEASEEOF

# Checksums hinzufügen
cd "$DISTS_DIR"

# NOTE: an earlier find/awk block here was removed. It forked md5sum/sha256sum
# per file and used awk's system() for the size, which returns the command's
# exit status (not the size) and leaks stat's output to stdout -- so it emitted
# a malformed MD5Sum/SHA256 section ahead of the correct ones below.
# The explicit loops that follow produce the real checksums.
echo "MD5Sum:" >> "$RELEASE_FILE"
for f in main/binary-amd64/Packages main/binary-amd64/Packages.gz main/binary-i386/Packages main/binary-i386/Packages.gz; do
    if [ -f "$f" ]; then
        SIZE=$(stat -c %s "$f")
        MD5=$(md5sum "$f" | cut -d' ' -f1)
        echo " $MD5 $SIZE $f" >> "$RELEASE_FILE"
    fi
done

echo "SHA256:" >> "$RELEASE_FILE"
for f in main/binary-amd64/Packages main/binary-amd64/Packages.gz main/binary-i386/Packages main/binary-i386/Packages.gz; do
    if [ -f "$f" ]; then
        SIZE=$(stat -c %s "$f")
        SHA=$(sha256sum "$f" | cut -d' ' -f1)
        echo " $SHA $SIZE $f" >> "$RELEASE_FILE"
    fi
done

# 5. Optional: GPG signieren
if command -v gpg &> /dev/null; then
    echo ">>> Signiere Repository..."
    # InRelease (inline signiert)
    gpg --default-key "$GPG_KEY_ID" --clearsign -o "$DISTS_DIR/InRelease" "$RELEASE_FILE" 2>/dev/null || \
        echo "   (GPG Signatur übersprungen - kein Schlüssel)"
    # Release.gpg (detached)
    gpg --default-key "$GPG_KEY_ID" -abs -o "$DISTS_DIR/Release.gpg" "$RELEASE_FILE" 2>/dev/null || true
fi

# 6. Zusammenfassung
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║  ✅ REPOSITORY AKTUALISIERT                                   ║"
echo "╠═══════════════════════════════════════════════════════════════╣"

# Zeige Pakete
echo "║  Pakete im Pool:"
for deb in "$POOL_DIR"/*.deb; do
    if [ -f "$deb" ]; then
        NAME=$(basename "$deb")
        SIZE=$(ls -lh "$deb" | awk '{print $5}')
        echo "║    • $NAME ($SIZE)"
    fi
done

echo "║"
echo "║  Download-URLs:"
echo "║    https://repo.ailinux.me/pool/main/a/aicoder/"
echo "║"
echo "║  APT Installation:"
echo "║    sudo apt update && sudo apt install aicoder"
echo "╚═══════════════════════════════════════════════════════════════╝"
