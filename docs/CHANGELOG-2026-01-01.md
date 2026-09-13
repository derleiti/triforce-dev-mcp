# TriForce Development Changelog - 01.01.2026

## Übersicht

Dokumentation aller Arbeiten aus den letzten 3 Chat-Sessions. Hauptthemen:
- MCP WebSocket Tier-Mapping Fix
- Eigenes Update-System (update.ailinux.me)
- Client-Build mit PyQt6 WebEngine Fix
- Multi-Node Auto-Update Deployment

---

## Session 1: MCP Tier Enum Fix

### Problem
Nach Backend-Restart schlugen MCP WebSocket-Verbindungen fehl:
```
ValueError: 'free' is not a valid UserTier
  File "mcp_node.py", line 332, in websocket_connect
    resolved_tier = UserTier(tier)
```

### Ursache
Client sendet `tier=free` als Query-Parameter, aber Python Enum sucht nach VALUE, nicht NAME:
```python
class UserTier(str, Enum):
    GUEST = "guest"
    FREE = "guest"  # Value ist "guest", nicht "free"!
```
`UserTier("free")` findet keinen Enum mit value="free" → ValueError

### Lösung
Explizites Tier-Mapping Dictionary in `/home/zombie/workspace/triforce/app/routes/mcp_node.py`:
```python
# Line 330-340
tier_mapping = {
    "free": UserTier.GUEST,
    "guest": UserTier.GUEST,
    "registered": UserTier.REGISTERED,
    "pro": UserTier.PRO,
    "enterprise": UserTier.ENTERPRISE
}
if tier and tier.lower() in tier_mapping:
    resolved_tier = tier_mapping[tier.lower()]
else:
    resolved_tier = tier_service.get_user_tier(resolved_user_id)
```

### Deployment
- Alle 3 Nodes via SCP aktualisiert
- Git Commit: `4ba45ab0`
- 0 Errors nach Fix

---

## Session 2: Update-System (update.ailinux.me)

### Ziel
Eigenes Verteilungssystem statt GitHub für automatische Updates auf allen Federation-Nodes.

### Komponenten

#### 1. Apache vHost
**Datei:** `/home/zombie/workspace/triforce/docker/wordpress/apache/vhosts/vhost-update.ailinux.me.conf`
```apache
<VirtualHost *:443>
  ServerName update.ailinux.me
  DocumentRoot /var/www/update.ailinux.me
  SSLEngine on
  SSLCertificateFile /etc/letsencrypt/live/ailinux.me/fullchain.pem
  SSLCertificateKeyFile /etc/letsencrypt/live/ailinux.me/privkey.pem
  
  <Directory /var/www/update.ailinux.me>
    Options +Indexes
    Require all granted
  </Directory>
</VirtualHost>
```

#### 2. Docker Volume Mount
**Datei:** `/home/zombie/workspace/triforce/docker/wordpress/docker-compose.yml`
```yaml
volumes:
  - /var/www/update.ailinux.me:/var/www/update.ailinux.me:ro
```

#### 3. DNS (Cloudflare)
- Type: A
- Name: update
- Content: 138.201.50.230
- Proxy: OFF

### Verzeichnisstruktur
```
/var/www/update.ailinux.me/
├── manifest.json           # Backend-Version & File-Hashes
├── releases/
│   ├── 2.80.1.tar.gz      # Backend-Releases
│   ├── 2.80.1.sha256
│   └── 2.80.1.changelog
├── current/
│   └── triforce-latest.tar.gz -> ../releases/2.80.1.tar.gz
├── client/
│   ├── manifest.json       # Client-Version
│   ├── releases/
│   │   ├── ailinux-client_4.3.3_amd64.deb
│   │   └── ailinux-client_4.3.3_amd64.deb.sha256
│   └── current/
│       └── ailinux-client-latest.deb
└── archive/
```

### Scripts

#### Release Publisher (Backend)
**Datei:** `/home/zombie/workspace/triforce/scripts/publish-release.sh`
```bash
# Usage: ./publish-release.sh [patch|minor|major] [message]
# - Bumpt VERSION
# - Git commit + tag
# - Erstellt tarball mit SHA256
# - Generiert manifest.json
```

#### Release Creator
**Datei:** `/home/zombie/workspace/triforce/scripts/create-release.sh`
```bash
# Erstellt:
# - releases/$VERSION.tar.gz (app/, config/, scripts/)
# - releases/$VERSION.sha256
# - releases/$VERSION.changelog
# - manifest.json mit File-Hashes
```

#### Auto-Updater (Node-Client)
**Datei:** `/home/zombie/workspace/triforce/scripts/triforce-update.sh`
```bash
# Features:
# - Prüft https://update.ailinux.me/manifest.json
# - Vergleicht Git-Commit
# - Download + SHA256 Verification
# - Backup vor Update
# - Optional: --restart für Service-Neustart
```

### Systemd Timer
**Dateien:**
- `/etc/systemd/system/triforce-update.timer`
- `/etc/systemd/system/triforce-update.service`

```ini
# Timer: Alle 30 Minuten
[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
RandomizedDelaySec=5min
```

### Node-Deployment
```bash
# Auf jedem Node:
sudo cp triforce-update.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now triforce-update.timer
```

**Status:**
- Hetzner: ✅ Timer aktiv
- Desktop: ✅ Timer aktiv  
- Backup: ✅ Timer aktiv

---

## Session 3: Client Build & Update

### Problem
Client-Binary crasht mit:
```
ModuleNotFoundError: No module named 'PyQt6.QtWebEngineWidgets'
```

### Ursache
PyInstaller bundled QtWebEngine nicht vollständig trotz `--hidden-import`.

### Lösung
**Datei:** `/home/zombie/workspace/triforce/client-deploy/release.sh`
```bash
# Hinzugefügt:
--hidden-import=PyQt6.QtWebEngineCore \
--hidden-import=PyQt6.QtWebChannel \
--collect-all=PyQt6.QtWebEngineWidgets \
--collect-all=PyQt6.QtWebEngineCore
```

### Client Updater Umstellung
**Datei:** `/home/zombie/workspace/triforce/client-deploy/ailinux-client/ailinux_client/core/updater.py`

Vorher:
```python
UPDATE_BASE_URL = "https://api.ailinux.me"
VERSION_ENDPOINT = "/v1/client/update/version"
```

Nachher:
```python
UPDATE_BASE_URL = "https://update.ailinux.me"
MANIFEST_URL = f"{UPDATE_BASE_URL}/client/manifest.json"
RELEASES_URL = f"{UPDATE_BASE_URL}/client/releases"
```

### Client Publisher
**Datei:** `/home/zombie/workspace/triforce/client-deploy/publish-client.sh`
```bash
# Workflow:
# 1. Ruft release.sh auf (PyInstaller + .deb)
# 2. Kopiert nach /var/www/update.ailinux.me/client/releases/
# 3. Generiert SHA256 + manifest.json
# 4. Updated Symlinks
```

### APT Repository Update
```bash
# Nach Client-Build:
cp ailinux-client_4.3.3_amd64.deb \
   /home/zombie/workspace/triforce/docker/repository/repo/mirror/archive.ailinux.me/pool/main/a/ailinux-client/

sudo ./update-mirror.sh
```

### Ergebnis
- Client v4.3.3 auf update.ailinux.me
- Client v4.3.3 im APT Repository
- QtWebEngine Fix integriert

---

## Aktuelle Endpoints

| Service | URL |
|---------|-----|
| Backend Manifest | https://update.ailinux.me/manifest.json |
| Backend Download | https://update.ailinux.me/releases/2.80.1.tar.gz |
| Client Manifest | https://update.ailinux.me/client/manifest.json |
| Client Download | https://update.ailinux.me/client/releases/ailinux-client_4.3.3_amd64.deb |
| APT Repository | https://archive.ailinux.me |

---

## Workflow-Übersicht

### Backend-Release
```bash
cd /home/zombie/workspace/triforce
./scripts/publish-release.sh patch "Fix description"
# → Automatisches Update auf allen Nodes innerhalb 30 Min
```

### Client-Release
```bash
cd /home/zombie/workspace/triforce/client-deploy
./publish-client.sh --bump-patch
# → Kopiert zu update.ailinux.me + APT repo
```

### Manuelles Node-Update
```bash
/home/zombie/workspace/triforce/scripts/triforce-update.sh --restart
```

---

## Dateien-Referenz

### Backend
| Datei | Beschreibung |
|-------|--------------|
| `app/routes/mcp_node.py` | MCP WebSocket mit Tier-Mapping |
| `app/services/user_tiers.py` | UserTier Enum Definition |
| `scripts/publish-release.sh` | Release Publisher |
| `scripts/create-release.sh` | Tarball Creator |
| `scripts/triforce-update.sh` | Auto-Updater Client |
| `VERSION` | Aktuelle Version (2.80.1) |

### Client
| Datei | Beschreibung |
|-------|--------------|
| `release.sh` | PyInstaller + .deb Builder |
| `publish-client.sh` | Update-Server Publisher |
| `ailinux_client/core/updater.py` | Auto-Update Client |
| `ailinux_client/version.py` | Version (4.3.3) |

### Infrastructure
| Datei | Beschreibung |
|-------|--------------|
| `docker/wordpress/apache/vhosts/vhost-update.ailinux.me.conf` | Apache vHost |
| `docker/wordpress/docker-compose.yml` | Volume Mount |
| `/etc/systemd/system/triforce-update.timer` | Update Timer |

---

## Federation Status

```
┌─────────────────────────────┐
│    update.ailinux.me        │
│    Backend v2.80.1          │
│    Client v4.3.3            │
└─────────────────────────────┘
              │
    ┌─────────┼─────────┐
    ▼         ▼         ▼
┌────────┐ ┌────────┐ ┌────────┐
│Hetzner │ │Desktop │ │ Backup │
│  Hub   │ │  Node  │ │  Node  │
│ 30min  │ │ 30min  │ │ 30min  │
└────────┘ └────────┘ └────────┘
     ✅        ✅        ✅
```

---

## Git Commits (01.01.2026)

1. `4ba45ab0` - fix: Map 'free' tier to UserTier.GUEST in MCP node WebSocket
2. Client v4.3.3 - fix: PyQt6 QtWebEngineWidgets bundling

---

*Erstellt: 01.01.2026*
*Author: Nova (Claude) + Markus*
