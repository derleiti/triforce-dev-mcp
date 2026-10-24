#!/bin/bash
set -Eeuo pipefail
node="${1:?usage: $0 backup|zombie-pc}"
case "$node" in
 backup) address=10.10.0.3 ;;
 zombie-pc) address=10.10.0.2 ;;
 *) echo "invalid host" >&2; exit 2 ;;
esac
test "$(hostname)" = "$node"
ip -4 -o addr show dev wg0 | grep -Fq "$address/"
test -x /lib/systemd/systemd-socket-proxyd
curl -fsS --max-time 3 http://127.0.0.1:11434/api/version >/dev/null
base=/etc/systemd/system
backup=/var/backups/ailinux/triforce-ollama-wg-20261023
sudo install -d -m 0700 "$backup"
for u in triforce-ollama-wg.socket triforce-ollama-wg.service; do
 if [[ -f "$base/$u" ]]; then
   sudo cp -an "$base/$u" "$backup/$u"
 fi
done
cat >/tmp/triforce-ollama-wg.socket <<EOF
[Unit]
Description=WireGuard-only Ollama ingress for TriForce
After=wg-quick@wg0.service
Wants=wg-quick@wg0.service

[Socket]
ListenStream=$address:11434
FreeBind=yes
NoDelay=true
Service=triforce-ollama-wg.service

[Install]
WantedBy=sockets.target
EOF
cat >/tmp/triforce-ollama-wg.service <<'EOF'
[Unit]
Description=Ollama VPN socket proxy to local Ollama
After=ollama.service
Requires=ollama.service

[Service]
Type=simple
ExecStart=/lib/systemd/systemd-socket-proxyd 127.0.0.1:11434
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
EOF
sudo install -m 0644 /tmp/triforce-ollama-wg.socket "$base/triforce-ollama-wg.socket"
sudo install -m 0644 /tmp/triforce-ollama-wg.service "$base/triforce-ollama-wg.service"
sudo systemctl daemon-reload
sudo systemctl enable --now triforce-ollama-wg.socket
sudo systemctl is-active --quiet triforce-ollama-wg.socket
curl -fsS --max-time 3 "http://$address:11434/api/version"
printf '\n'
curl -fsS --max-time 3 http://127.0.0.1:11434/api/version
printf '\n'
sudo ss -lntp | grep 11434
