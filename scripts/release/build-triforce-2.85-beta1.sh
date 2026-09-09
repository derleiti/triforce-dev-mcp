#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="$(tr -d '\n' < "$ROOT/VERSION")"
ARCH="$(dpkg --print-architecture)"
OUT="${OUT_DIR:-$ROOT/dist/2.85-beta1}"
RUNTIME="${BACKEND_RUNTIME:-/tmp/triforce-2.85-backend-runtime}"
GUI="${GUI_BUNDLE:-/tmp/triforce-2.85-nuitka/main.dist}"
WORK="$(mktemp -d /tmp/triforce-deb.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "$VERSION" == "2.85.0~beta1" ]] || fail "unexpected VERSION: $VERSION"
[[ -x "$RUNTIME/bin/python" ]] || fail "backend runtime missing: $RUNTIME"
[[ -x "$GUI/triforce-control-center" ]] || fail "GUI bundle missing: $GUI"

secret_scan(){
  local root="$1"
  if grep -RIlE --exclude='*.pyc' --exclude='LICENSE*' '(AIzaSy[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9]+|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)' "$root" 2>/dev/null | grep -q .; then
    echo "Potential secret material found:" >&2
    grep -RIlE --exclude='*.pyc' '(AIzaSy[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{20,}|sk-or-v1-[A-Za-z0-9]+|BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY)' "$root" 2>/dev/null >&2 || true
    return 1
  fi
}

make_control(){
  local pkg="$WORK/control" deb="$OUT/triforce-control-center_${VERSION}_${ARCH}.deb"
  mkdir -p "$pkg/DEBIAN" "$pkg/opt/triforce-control-center" "$pkg/usr/bin" \
    "$pkg/usr/share/applications" "$pkg/usr/share/icons/hicolor/scalable/apps" \
    "$pkg/usr/share/polkit-1/actions" "$pkg/usr/share/doc/triforce-control-center"
  cp -a "$GUI/." "$pkg/opt/triforce-control-center/"
  cp "$ROOT/packaging/triforce-control-center.desktop" "$pkg/usr/share/applications/"
  cp "$ROOT/packaging/triforce-control-center.svg" "$pkg/usr/share/icons/hicolor/scalable/apps/triforce-control-center.svg"
  cp "$ROOT/packaging/polkit/me.ailinux.triforce.control.policy" "$pkg/usr/share/polkit-1/actions/"
  cp "$ROOT/LICENSE" "$pkg/usr/share/doc/triforce-control-center/copyright"
  cat > "$pkg/usr/bin/triforce-control-center" <<'EOF'
#!/bin/sh
exec /opt/triforce-control-center/triforce-control-center "$@"
EOF
  chmod 755 "$pkg/usr/bin/triforce-control-center"
  cat > "$pkg/DEBIAN/control" <<EOF
Package: triforce-control-center
Version: $VERSION
Architecture: $ARCH
Section: admin
Priority: optional
Depends: triforce-backend (= $VERSION), pkexec, systemd
Maintainer: Markus Leitermann <admin@ailinux.me>
Homepage: https://ailinux.me
Description: Compiled PyQt6 Control Center for TriForce
 Standalone Nuitka-built GUI for configuration, service control, setup tasks,
 logs and diagnostics. The backend remains managed by systemd.
EOF
  find "$pkg" -type d -exec chmod 755 {} +
  secret_scan "$pkg"
  dpkg-deb --root-owner-group --build "$pkg" "$deb" >/dev/null
  echo "$deb"
}

make_backend(){
  local pkg="$WORK/backend" deb="$OUT/triforce-backend_${VERSION}_${ARCH}.deb"
  mkdir -p "$pkg/DEBIAN" "$pkg/opt/triforce" "$pkg/usr/lib/triforce" "$pkg/usr/bin" \
    "$pkg/usr/lib/systemd/system" "$pkg/usr/share/doc/triforce-backend"
  # Explicit source payload: no production .env/auth/data/logs/backups/.git/docker volumes.
  cp -a "$ROOT/app" "$pkg/opt/triforce/app"
  mkdir -p "$pkg/opt/triforce/scripts" "$pkg/opt/triforce/config" "$pkg/opt/triforce/bin" "$pkg/opt/triforce/packaging/systemd"
  cp "$ROOT/scripts/start-triforce.sh" "$pkg/opt/triforce/scripts/"
  cp "$ROOT/bin/triforce-control" "$pkg/opt/triforce/bin/"
  cp "$ROOT/config/triforce.env.example" "$pkg/opt/triforce/config/"
  cp "$ROOT/packaging/systemd/triforce.service" "$pkg/opt/triforce/packaging/systemd/"
  cp "$ROOT/VERSION" "$ROOT/LICENSE" "$ROOT/requirements.txt" "$ROOT/requirements-lock-2.85-beta1.txt" "$pkg/opt/triforce/"
  cp -a "$RUNTIME" "$pkg/opt/triforce/runtime"
  # Remove build-only caches/tooling from shipped runtime while preserving imports.
  find "$pkg/opt/triforce/runtime" -type d -name '__pycache__' -prune -exec rm -rf {} + || true
  rm -rf "$pkg/opt/triforce/runtime/lib/python3.14/site-packages/pytest"* \
         "$pkg/opt/triforce/runtime/lib/python3.14/site-packages/_pytest" \
         "$pkg/opt/triforce/runtime/lib/python3.14/site-packages/black"* \
         "$pkg/opt/triforce/runtime/lib/python3.14/site-packages/mypy"* \
         "$pkg/opt/triforce/runtime/lib/python3.14/site-packages/ruff"* 2>/dev/null || true
  cp "$ROOT/packaging/triforce-admin-helper.py" "$pkg/usr/lib/triforce/triforce-admin-helper"
  chmod 755 "$pkg/usr/lib/triforce/triforce-admin-helper" "$pkg/opt/triforce/scripts/start-triforce.sh" "$pkg/opt/triforce/bin/triforce-control"
  cp "$ROOT/packaging/systemd/triforce.service" "$pkg/usr/lib/systemd/system/triforce.service"
  cat > "$pkg/usr/bin/triforce-control" <<'EOF'
#!/bin/sh
export PYTHONPATH=/opt/triforce
exec /opt/triforce/runtime/bin/python /opt/triforce/bin/triforce-control "$@"
EOF
  chmod 755 "$pkg/usr/bin/triforce-control"
  cp "$ROOT/LICENSE" "$pkg/usr/share/doc/triforce-backend/copyright"
  cat > "$pkg/DEBIAN/control" <<EOF
Package: triforce-backend
Version: $VERSION
Architecture: $ARCH
Section: net
Priority: optional
Depends: python3.14, systemd
Suggests: redis-server
Maintainer: Markus Leitermann <admin@ailinux.me>
Homepage: https://ailinux.me
Description: TriForce 2.85 Beta 1 AI backend
 Headless FastAPI/MCP backend with an isolated prebuilt Python runtime.
 No Python packages are installed into the system interpreter at package time.
EOF
  cat > "$pkg/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
/usr/lib/triforce/triforce-admin-helper runtime-init
/usr/lib/triforce/triforce-admin-helper config-init
/bin/systemctl daemon-reload || true
# Deliberately do not start or enable the service. Those are independent choices.
exit 0
EOF
  cat > "$pkg/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
  /bin/systemctl stop triforce.service 2>/dev/null || true
fi
exit 0
EOF
  cat > "$pkg/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
/bin/systemctl daemon-reload 2>/dev/null || true
# /etc/triforce and /var/lib/triforce are intentionally retained, including purge.
exit 0
EOF
  chmod 755 "$pkg/DEBIAN/postinst" "$pkg/DEBIAN/prerm" "$pkg/DEBIAN/postrm"
  find "$pkg" -type d -exec chmod 755 {} +
  secret_scan "$pkg"
  dpkg-deb --root-owner-group --build "$pkg" "$deb" >/dev/null
  echo "$deb"
}

BACKEND_DEB="$(make_backend)"
CONTROL_DEB="$(make_control)"
sha256sum "$BACKEND_DEB" "$CONTROL_DEB" > "$OUT/SHA256SUMS"
cat > "$OUT/BUILD-MANIFEST.txt" <<EOF
TriForce product: 2.85 Beta 1
Debian version: $VERSION
Architecture: $ARCH
Build OS: $(. /etc/os-release; echo "$PRETTY_NAME")
Python: $(python3.14 --version 2>&1)
Backend runtime lock: requirements-lock-2.85-beta1.txt
GUI: Nuitka standalone, PyQt6 6.11.0
Backend package: $(basename "$BACKEND_DEB")
Control package: $(basename "$CONTROL_DEB")
EOF
cat "$OUT/BUILD-MANIFEST.txt"
cat "$OUT/SHA256SUMS"
