# Codex MCP Agent Sidecar

> Current TriForce baseline: **2.86.4**. This directory documents the legacy/container-side Codex MCP agent integration. New capability discovery and endpoint execution should follow the canonical MCP/Loom model described in the repository root README. Do not treat historical port examples here as production defaults without checking current deployment configuration.

Autostarting Codex MCP Agent für alle Docker Stacks (wordpress, ailinux-repo, mailserver).

## Struktur

```
agent/
├── Dockerfile              # Agent Container Image
├── bin/
│   ├── codex-triforce      # Wrapper Script
│   └── keep-alive.sh       # Health Check Loop
├── config/mcp/
│   └── triforce-mcp.json   # MCP Endpoint Config
├── compose-patches/        # Service-Definitionen für jeden Stack
│   ├── wordpress.yml
│   ├── ailinux-repo.yml
│   └── mailserver.yml
├── install-agents.sh       # Automatisches Install-Script
└── README.md
```

## Installation

### Schritt 1: Compose-Dateien patchen

Füge den `codex-agent` Service in jede docker-compose.yml ein.
Die Service-Definitionen findest du in `compose-patches/`.

**WordPress** (`wordpress/docker-compose.yml`):
```yaml
  codex-agent:
    build:
      context: ../agent
      dockerfile: Dockerfile
    container_name: wordpress_codex_agent
    environment:
      TRIFORCE_ROOT: /opt/triforce
      MCP_ENDPOINT: http://host.docker.internal:9100/v1/mcp
      CHECK_INTERVAL: "60"
      TZ: Europe/Berlin
    extra_hosts:
      - "host.docker.internal:host-gateway"
    restart: unless-stopped
    networks:
      - wordpress_network
    depends_on:
      - apache
```

**AILinux-Repo** (`ailinux-repo/docker-compose.yml`):
- Füge `codex-agent` Service hinzu (siehe compose-patches/ailinux-repo.yml)
- Erstelle `repo_network` falls nicht vorhanden
- Füge `networks: [repo_network]` zum nginx Service hinzu

**Mailserver** (`mailserver/docker-compose.yml`):
- Füge `codex-agent` Service hinzu (siehe compose-patches/mailserver.yml)
- Erstelle `mail_network` falls nicht vorhanden

### Schritt 2: Build & Start

```bash
# Einzelner Stack
cd /home/zombie/triforce/wordpress
docker compose build codex-agent
docker compose up -d codex-agent

# Oder alle mit Script
./agent/install-agents.sh all
```

### Schritt 3: Validierung

```bash
# MCP Connection testen
docker exec wordpress_codex_agent curl -s http://host.docker.internal:9100/v1/mcp/status

# Logs prüfen
docker logs -f wordpress_codex_agent
```

## Konfiguration

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `TRIFORCE_ROOT` | `/opt/triforce` | Container-Pfad für Config |
| `MCP_ENDPOINT` | `http://host.docker.internal:9100/v1/mcp` | MCP Server URL |
| `CHECK_INTERVAL` | `60` | Health Check Intervall (Sekunden) |

## Fallback

Falls `host.docker.internal` nicht funktioniert, ersetze mit `172.17.0.1:9100`.

## Auth Bypass

Die MCP-Verbindung ist für Docker-Netzwerke (172.17.0.0/16) ohne Auth konfiguriert.
