#!/usr/bin/env bash
set -euo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/applications"
BIN="${BIN_DIR}/triforce-workspace-gui"
CLI="${BIN_DIR}/triforce-workspace"
DESKTOP="${APP_DIR}/triforce-workspace.desktop"

mkdir -p "$BIN_DIR" "$APP_DIR" "${HOME}/.config"

cat > "$BIN" <<EOF
#!/usr/bin/env sh
set -eu
cd '$ROOT'
exec python3 -m local_workspace_client.gui "\$@"
EOF

cat > "$CLI" <<EOF
#!/usr/bin/env sh
set -eu
cd '$ROOT'
exec python3 -m local_workspace_client.client "\$@"
EOF
chmod 755 "$BIN" "$CLI"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=TriForce Local Workspace
Comment=Pair a local folder with the public TriForce MCP
Exec=$BIN --pair-url %u
Icon=triforce-control-center
Terminal=false
Categories=Development;Utility;
MimeType=x-scheme-handler/triforce-workspace;
NoDisplay=false
EOF
chmod 644 "$DESKTOP"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true
fi
if command -v xdg-mime >/dev/null 2>&1; then
  xdg-mime default triforce-workspace.desktop x-scheme-handler/triforce-workspace
fi

printf 'Installed TriForce Local Workspace helper\n'
printf 'GUI: %s\n' "$BIN"
printf 'Desktop entry: %s\n' "$DESKTOP"
printf 'Scheme handler: '
xdg-mime query default x-scheme-handler/triforce-workspace 2>/dev/null || true
