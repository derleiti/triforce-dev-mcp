# TriForce Cloudflare Load Balancer

> Current routing note (TriForce **2.86.4**): Cloudflare configuration is deployment infrastructure, not a source of truth for active backend inventory. Verify origins/health endpoints before deployment; do not copy historical IP examples into production blindly.

## Übersicht

Cloudflare Worker für intelligentes Load Balancing zwischen TriForce Servern.

## Features

- **Dynamic Backend Selection**: Wählt Backend basierend auf Model
- **Health-Aware Routing**: Routet nur zu healthy Backends
- **Weighted Round-Robin**: Gewichtete Verteilung
- **Auto-Failover**: Automatischer Fallback bei Backend-Ausfall
- **Config-Sync**: Periodische Synchronisation mit Hub

## Deployment

```bash
# Install Wrangler
npm install -g wrangler

# Login
wrangler login

# Deploy
cd /home/zombie/workspace/triforce/cloudflare
wrangler deploy
```

## Endpoints

| Endpoint | Beschreibung |
|----------|--------------|
| `/lb/health` | Worker Health Check |
| `/*` | Proxy zu Backend |

## Request Flow

```
Client → Cloudflare Edge → Worker → Backend Selection → Origin Server
                              ↓
                        Config Sync (60s)
                              ↓
                        Hub /v1/federation/lb/cloudflare
```

## Configuration

Der Worker holt Backend-Konfiguration vom Hub:

```json
{
  "backends": [
    {
      "id": "hetzner",
      "url": "https://api.ailinux.me",
      "weight": 100,
      "healthy": true,
      "models": ["*"]
    },
    {
      "id": "backup",
      "url": "http://5.104.107.103:9000",
      "weight": 50,
      "healthy": true,
      "models": ["llama3.2", "qwen2.5"]
    }
  ]
}
```

## Alternative: Cloudflare Load Balancer (Dashboard)

Statt Worker kann auch der native Cloudflare Load Balancer verwendet werden:

1. **Dashboard → Traffic → Load Balancing**
2. **Pool erstellen**: triforce-pool
   - Origin 1: api.ailinux.me:443 (Weight: 100)
   - Origin 2: 5.104.107.103:9000 (Weight: 50, Backup)
3. **Health Check**: HTTP GET /health
4. **Load Balancer**: lb.ailinux.me → triforce-pool

## Monitoring

```bash
# Worker Logs
wrangler tail

# Analytics
# Dashboard → Workers → triforce-loadbalancer → Analytics
```
