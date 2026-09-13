#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
BIN="${BIN_DIR}/ailinux-workspace-browser"
DESKTOP="${APP_DIR}/ailinux-workspace-browser.desktop"
mkdir -p "$BIN_DIR" "$APP_DIR"
cat > "$BIN" <<EOT
#!/usr/bin/env sh
set -eu
cd '$ROOT'
exec '$ROOT/node_modules/.bin/electron' '$ROOT' "\$@"
EOT
chmod 755 "$BIN"
cat > "$DESKTOP" <<EOT
[Desktop Entry]
Type=Application
Name=AILinux Workspace Browser
Comment=Persistent TriForce workspace executor
Exec=$BIN %u
Icon=applications-development
Terminal=false
Categories=Development;Utility;
MimeType=x-scheme-handler/ailinux-workspace;
StartupNotify=true
EOT
chmod 644 "$DESKTOP"
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true; fi
if command -v xdg-mime >/dev/null 2>&1; then xdg-mime default ailinux-workspace-browser.desktop x-scheme-handler/ailinux-workspace || true; fi
printf 'Installed: %s\n' "$BIN"
printf 'Protocol: %s\n' "$(xdg-mime query default x-scheme-handler/ailinux-workspace 2>/dev/null || true)"
