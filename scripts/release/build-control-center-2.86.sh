#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
ARCH="${ARCH:-$(dpkg --print-architecture)}"
CONTROL_REVISION="${CONTROL_REVISION:-2}"
DEBIAN_VERSION="${DEBIAN_VERSION:-1:${VERSION}-${CONTROL_REVISION}}"
FILE_VERSION="${DEBIAN_VERSION#*:}"
BACKEND_MIN="${BACKEND_MIN:-1:${VERSION}-9}"
PYTHON_BIN="${PYTHON_BIN:-python3.14}"
BUILD_ROOT="${BUILD_ROOT:-/tmp/triforce-control-center-${VERSION}}"
VENV="${GUI_VENV:-$BUILD_ROOT/venv}"
NUITKA_OUT="${GUI_OUT:-$BUILD_ROOT/nuitka}"
GUI="$NUITKA_OUT/main.dist"
OUT_DIR="${OUT_DIR:-$ROOT/dist/control-center}"
WORK="$(mktemp -d /tmp/triforce-control-deb.XXXXXX)"
PKG="$WORK/pkg"
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$ROOT" log -1 --format=%ct)}"

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT

[[ "$VERSION" == 2.86.* ]] || { echo "ERROR: unexpected VERSION: $VERSION" >&2; exit 1; }
[[ "$ARCH" == "amd64" ]] || { echo "ERROR: Control Center build currently supports amd64 only" >&2; exit 1; }

mkdir -p "$BUILD_ROOT" "$OUT_DIR"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --disable-pip-version-check --upgrade pip
"$VENV/bin/pip" install --disable-pip-version-check \
  'PyQt6==6.11.0' \
  'pydantic-settings==2.15.0' \
  'nuitka==4.2.1' ordered-set zstandard patchelf

QT_QPA_PLATFORM=offscreen PYTHONPATH="$ROOT" \
  "$VENV/bin/python" "$ROOT/control_center/main.py" --smoke-test

rm -rf "$NUITKA_OUT"
"$VENV/bin/python" -m nuitka \
  --mode=standalone \
  --enable-plugin=pyqt6 \
  --include-qt-plugins=platforms,imageformats,iconengines,wayland-decoration-client,wayland-graphics-integration-client,wayland-shell-integration \
  --include-package=control_center \
  --output-dir="$NUITKA_OUT" \
  --output-filename=triforce-control-center \
  "$ROOT/control_center/main.py"

test -x "$GUI/triforce-control-center"
QT_QPA_PLATFORM=offscreen "$GUI/triforce-control-center" --smoke-test

mkdir -p \
  "$PKG/DEBIAN" \
  "$PKG/opt/triforce-control-center" \
  "$PKG/usr/bin" \
  "$PKG/usr/share/applications" \
  "$PKG/usr/share/icons/hicolor/scalable/apps" \
  "$PKG/usr/share/polkit-1/actions" \
  "$PKG/usr/share/doc/triforce-control-center" \
  "$PKG/usr/share/lintian/overrides"

cp -a "$GUI/." "$PKG/opt/triforce-control-center/"
rm -rf \
  "$PKG/opt/triforce-control-center/PyQt6/Qt6/plugins/egldeviceintegrations" \
  "$PKG/opt/triforce-control-center/PyQt6/Qt6/plugins/platformthemes" \
  "$PKG/opt/triforce-control-center/PyQt6/Qt6/plugins/printsupport"
cp "$ROOT/packaging/triforce-control-center.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/triforce-control-center.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/triforce-control-center.svg"
cp "$ROOT/packaging/polkit/me.ailinux.triforce.control.policy" "$PKG/usr/share/polkit-1/actions/"
cp "$ROOT/LICENSE" "$PKG/usr/share/doc/triforce-control-center/copyright"

cat > "$PKG/usr/bin/triforce-control-center" <<'WRAP'
#!/bin/sh
exec /opt/triforce-control-center/triforce-control-center "$@"
WRAP
chmod 0755 "$PKG/usr/bin/triforce-control-center"

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: triforce-control-center
Version: $DEBIAN_VERSION
Architecture: $ARCH
Section: admin
Priority: optional
Depends: triforce-backend (>= $BACKEND_MIN), triforce-backend (<< 1:3.0), libc6, pkexec, systemd, libgl1, libegl1, libx11-6, libx11-xcb1, libxcb1, libxcb-cursor0, libxcb-glx0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-util1, libxcb-xfixes0, libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, libfontconfig1, libfreetype6, libwayland-client0, libwayland-cursor0, libwayland-egl1, libgbm1, libdrm2
Maintainer: Markus Leitermann <admin@ailinux.me>
Homepage: https://ailinux.me
Description: TriForce 2.86 Control Center
 Standalone PyQt6/Nuitka GUI for configuration, service control, setup tasks,
 logs and diagnostics. The backend remains managed independently by systemd.
CONTROL

cat > "$PKG/usr/share/lintian/overrides/triforce-control-center" <<'EOF_LINTIAN'
# The standalone Nuitka application is intentionally installed below /opt.
triforce-control-center: dir-or-file-in-opt [opt/triforce-control-center/*]
triforce-control-center: dir-or-file-in-opt [opt/triforce-control-center/]
EOF_LINTIAN

STAMP="$(date -u -d "@$SOURCE_DATE_EPOCH" -R)"
cat > "$PKG/usr/share/doc/triforce-control-center/changelog.Debian" <<EOF_CHANGELOG
triforce-control-center ($DEBIAN_VERSION) noble; urgency=medium

  * Detect source/package backend service mode from systemd runtime metadata.
  * Resolve source-mode configuration from the active service working tree.
  * Keep privileged operations behind the packaged PolicyKit helper.

 -- Markus Leitermann <admin@ailinux.me>  $STAMP
EOF_CHANGELOG
gzip -n -9 "$PKG/usr/share/doc/triforce-control-center/changelog.Debian"

# Scan textual payloads for common credential/private-key signatures.
if grep -RIlE --exclude='*.so' --exclude='triforce-control-center' \
  '(AIzaSy[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9]+|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)' \
  "$PKG" 2>/dev/null | grep -q .; then
  echo "ERROR: potential secret material found in Control Center package" >&2
  exit 1
fi

find "$PKG" -type d -exec chmod 0755 {} +
# Normalize mtimes so repeated packaging of the same Nuitka bundle is stable.
while IFS= read -r -d '' path; do
  touch -h -d "@$SOURCE_DATE_EPOCH" "$path"
done < <(find "$PKG" -print0)

DEB="$OUT_DIR/triforce-control-center_${FILE_VERSION}_${ARCH}.deb"
dpkg-deb --root-owner-group --build "$PKG" "$DEB" >/dev/null

echo "CONTROL_CENTER_DEB=$DEB"
echo "CONTROL_CENTER_VERSION=$DEBIAN_VERSION"
sha256sum "$DEB"
