#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
"$ROOT/install-user.sh"
mkdir -p "$HOME/.config/systemd/user"
install -m 644 "$ROOT/ailinux-workspace-browser.service" "$HOME/.config/systemd/user/ailinux-workspace-browser.service"
systemctl --user daemon-reload
systemctl --user enable --now ailinux-workspace-browser.service
