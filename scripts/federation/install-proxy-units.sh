#!/usr/bin/env bash
set -euo pipefail

MODE=install
BIND_IP=""
BACKEND="${TRIFORCE_FEDERATION_BACKEND:-127.0.0.1:9100}"

usage() {
  echo "Usage: $0 [--print] [--bind-ip WG_IP] [--backend HOST:PORT]" >&2
}

while (($#)); do
  case "$1" in
    --print) MODE=print; shift ;;
    --bind-ip) BIND_IP="${2:?missing bind ip}"; shift 2 ;;
    --backend) BACKEND="${2:?missing backend}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

if [[ -z "$BIND_IP" ]]; then
  BIND_IP="$(ip -4 -o addr show dev wg0 2>/dev/null | awk '{split($4,a,"/"); print a[1]; exit}')"
fi
[[ "$BIND_IP" =~ ^10\.10\.0\.[0-9]{1,3}$ ]] || { echo "ERROR: no valid wg0 federation IPv4 found" >&2; exit 1; }
[[ "$BACKEND" =~ ^(127\.0\.0\.1|172\.17\.0\.1):[0-9]{2,5}$ ]] || { echo "ERROR: unsafe backend target: $BACKEND" >&2; exit 1; }

render_socket() {
cat <<UNIT
[Unit]
Description=TriForce Federation WireGuard listener
Wants=network-online.target wg-quick@wg0.service
After=network-online.target wg-quick@wg0.service

[Socket]
ListenStream=${BIND_IP}:9100
Accept=no
NoDelay=true

[Install]
WantedBy=sockets.target
UNIT
}

render_service() {
cat <<UNIT
[Unit]
Description=TriForce Federation proxy to local backend
Requires=triforce-federation-proxy.socket
After=triforce.service

[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd ${BACKEND}
PrivateNetwork=no
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
UNIT
}

if [[ "$MODE" == print ]]; then
  echo '### triforce-federation-proxy.socket'
  render_socket
  echo '### triforce-federation-proxy.service'
  render_service
  exit 0
fi

[[ $EUID -eq 0 ]] || { echo "ERROR: install mode requires root" >&2; exit 1; }
install -d -m 0755 /etc/systemd/system
render_socket > /etc/systemd/system/triforce-federation-proxy.socket
render_service > /etc/systemd/system/triforce-federation-proxy.service
chmod 0644 /etc/systemd/system/triforce-federation-proxy.socket /etc/systemd/system/triforce-federation-proxy.service
systemctl daemon-reload
systemctl enable triforce-federation-proxy.socket >/dev/null

echo "Installed federation socket on ${BIND_IP}:9100 -> ${BACKEND}."
echo "No service restart was performed."
