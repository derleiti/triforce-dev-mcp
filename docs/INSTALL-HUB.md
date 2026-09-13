# TriForce Hub Server Installation

## Quick Install (One-Liner)

```bash
curl -fsSL https://raw.githubusercontent.com/derleiti/ailinux-ai-server-backend/master/scripts/install-hub.sh | bash
```

## Manual Installation

### 1. System Requirements

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git redis-server curl jq
```

### 2. Clone Repository

```bash
git clone https://github.com/derleiti/ailinux-ai-server-backend.git ~/triforce
cd ~/triforce
```

### 3. Python Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install aiofiles PyJWT  # Sometimes missing
```

### 4. Create Directories

```bash
mkdir -p logs config
sudo mkdir -p /var/tristar/{prompts,memory,tasks}
sudo chown -R $USER:$USER /var/tristar
```

### 5. Configuration

```bash
# Copy example config
cp config/triforce.env.example config/triforce.env

# Create local config (not in git!)
cat > config/.env << EOL
FEDERATION_NODE_ID=$(hostname)
JWT_SECRET=$(openssl rand -hex 32)
REDIS_URL=redis://localhost:6379
EOL
```

### 6. Redis

```bash
sudo systemctl enable --now redis-server
```

### 7. Systemd Service

```bash
sudo tee /etc/systemd/system/triforce.service << EOL
[Unit]
Description=TriForce Multi-LLM Backend
After=network.target redis-server.service

[Service]
LimitNOFILE=1048576
LimitNPROC=65535
Type=simple
User=$USER
WorkingDirectory=$HOME/triforce
EnvironmentFile=$HOME/triforce/config/triforce.env
EnvironmentFile=$HOME/triforce/config/.env
Environment="PATH=$HOME/triforce/.venv/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=$HOME/triforce/.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 9000 --timeout-keep-alive 75
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOL

sudo systemctl daemon-reload
sudo systemctl enable --now triforce
```

### 8. Verify

```bash
# Check status
sudo systemctl status triforce

# Check logs
journalctl -u triforce -f

# Test health
curl http://127.0.0.1:9000/health
```

---

## Federation Setup

To join the server federation (multi-hub cluster):

### 1. WireGuard VPN

Setup WireGuard to connect to main hub (10.10.0.0/24 network).

### 2. Add Node to Federation

On main hub, edit `app/services/server_federation.py`:

```python
FEDERATION_NODES = {
    "hetzner": { ... },
    "backup": { ... },
    "your-new-node": {
        "id": "your-hostname",
        "name": "New Hub",
        "vpn_ip": "10.10.0.X",  # Your VPN IP
        "port": 9000,
        "role": "node",
        "capabilities": ["chat", "ollama"],
        "ws_port": 9000
    }
}
```

### 3. Copy PSK

```bash
# From main hub
scp zombie@10.10.0.1:/home/zombie/workspace/triforce/config/federation_psk.key config/
```

### 4. Restart

```bash
sudo systemctl restart triforce
curl http://127.0.0.1:9000/v1/federation/status
```

---

## Troubleshooting

| Error | Solution |
|-------|----------|
| `ModuleNotFoundError: jwt` | `pip install PyJWT` |
| `ModuleNotFoundError: aiofiles` | `pip install aiofiles` |
| `Permission denied: /var/tristar` | `sudo chown -R $USER:$USER /var/tristar` |
| `Redis connection refused` | `sudo systemctl start redis-server` |
| `Port 9000 in use` | `sudo lsof -i :9000` then kill process |

## Ports

| Port | Service |
|------|---------|
| 9000 | API + WebSocket |
| 44433 | MCP Mesh Server |
| 6379 | Redis (local) |

