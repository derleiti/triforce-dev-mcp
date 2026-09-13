# AILinux TriForce Backend v2.80

<!-- AILINUX_STATUS_START -->
## Production operations snapshot

| Area | Current value |
| --- | --- |
| Production checkout | `/home/zombie/workspace/triforce` |
| Branch | `nova-nextlevel-20260603` |
| Expected HEAD | `16f43b8a` |
| Service | `triforce.service` |
| API URL | `https://api.ailinux.me` |
| Local port | `9000` via Uvicorn |
| Default model | `ollama/gemma4:12b` |
| Ollama local tag | `gemma4:12b` |
| OpenClaw gateway | `ws://127.0.0.1:18789` |

### Standard health checks

```bash
cd /home/zombie/workspace/triforce
git status --short --branch
git log --oneline -3
systemctl is-active triforce
curl -sS https://api.ailinux.me/health | jq .
```

Expected baseline: clean branch status, active service, and a health JSON with `ok: true`.

### Runtime directories

`data/crawler_spool/` is runtime state and is intentionally ignored. Recreate it if needed with `mkdir -p /home/zombie/workspace/triforce/data/crawler_spool`.

### Model defaults

Server, OpenClaw, and AI-Coder defaults should point to `ollama/gemma4:12b`. Direct local Ollama runs use `ollama run gemma4:12b`.

### Auto-update branch alignment

If the service log says the updater targets `master` while the checkout is on `nova-nextlevel-20260603`, updates are skipped. Align the systemd drop-in only after confirming the production branch.
<!-- AILINUX_STATUS_END -->

## Server Documentation

### Architektur-Übersicht

```
┌─────────────────────────────────────────────────────────────────┐
│                    TriForce Backend v2.80                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │   Mesh AI    │  │  MCP Service │  │   Client     │          │
│  │  Coordinator │◄─┤    (134+     │◄─┤   API        │          │
│  │   (Gemini)   │  │    Tools)    │  │  (JWT Auth)  │          │
│  └──────────────┘  └──────────────┘  └──────────────┘          │
│         │                 │                 │                   │
│         ▼                 ▼                 ▼                   │
│  ┌──────────────────────────────────────────────────┐          │
│  │              Mesh Brain v2.0                      │          │
│  │         Universal Load Balancer                   │          │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐    │          │
│  │  │ Ollama │ │ Gemini │ │  Groq  │ │Mistral │    │          │
│  │  │Hetzner │ │  API   │ │  API   │ │  API   │    │          │
│  │  └────────┘ └────────┘ └────────┘ └────────┘    │          │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐    │          │
│  │  │ Ollama │ │OpenRout│ │Cerebras│ │Claude  │    │          │
│  │  │ Backup │ │  API   │ │  API   │ │  API   │    │          │
│  │  └────────┘ └────────┘ └────────┘ └────────┘    │          │
│  └──────────────────────────────────────────────────┘          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

### 1. API Endpoints

#### Client API (`/v1/client/`)
| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/client/login` | POST | JWT Login (Email/Password) |
| `/client/register` | POST | User Registrierung |
| `/client/chat` | POST | Tier-basierter Chat |
| `/client/models` | GET | Verfügbare Modelle für Tier |
| `/client/tier` | GET | User Tier Info |
| `/client/tokens/usage` | GET | Token-Verbrauch |

#### MCP API (`/v1/mcp/`)
| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/mcp/tools` | GET | Liste aller 134+ Tools |
| `/mcp/call` | POST | Tool ausführen |
| `/mcp/prompts` | GET | System Prompts |
| `/mcp/resources` | GET | Verfügbare Ressourcen |

#### MCP Node (`/mcp/node/`)
| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/node/connect` | WebSocket | Client-Verbindung |
| `/node/clients` | GET | Verbundene Clients |
| `/node/call` | POST | Tool auf Client ausführen |
| `/node/tools` | GET | Client-Tools |
| `/node/chat-with-files` | POST | Chat mit Client-Dateien |

#### Mesh AI (`/v1/mesh/`)
| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/mesh/status` | GET | Mesh-Status |
| `/mesh/agents` | GET | Verfügbare Agents |
| `/mesh/task` | POST | Task an Mesh übergeben |

#### Distributed Compute
| Endpoint | Method | Beschreibung |
|----------|--------|--------------|
| `/compute/status` | GET | Compute-Cluster Status |
| `/compute/submit` | POST | Task einreichen |
| `/compute/workers` | GET | Verbundene Worker |

---

### 2. Services

#### Mesh Brain v2.0 (`services/mesh_brain_v2.py`)
Universal Load Balancer für alle AI Provider.

**Strategien:**
- `fallback` - Primary → Secondary bei Failure
- `round_robin` - Rotation durch Provider
- `fastest` - Schnellster Provider
- `cheapest` - Günstigster Provider
- `best` - Höchste Qualität
- `random` - Zufällige Auswahl

**Provider (sortiert nach Priorität):**
```
Tier 1 (Free & Fast):
  - Groq (llama-3.3-70b, speed=5)
  - Cerebras (llama-3.3-70b, speed=5)

Tier 2 (Free/Cheap & Good):
  - Gemini (gemini-2.5-flash, quality=4)
  - GitHub Models (gpt-4o-mini)
  - Cloudflare Workers AI

Tier 3 (Paid & Quality):
  - Mistral (mistral-small)
  - OpenRouter (deepseek-chat)

Tier 4 (Premium):
  - Anthropic (claude-3.5-sonnet)

Ollama Nodes:
  - Hetzner (127.0.0.1:11434) - Primary
  - Backup (10.10.0.3:11434) - Secondary
```

#### Mesh Coordinator (`services/mesh_coordinator.py`)
Multi-Agent Orchestrierung mit:
- Gemini als Lead Coordinator
- Worker Agents (Claude, Codex, DeepSeek)
- Reviewer Agents (Mistral, Cogito)

**Task Phasen:**
1. `received` → Task empfangen
2. `researching` → Recherche
3. `polling` → KI-Umfrage
4. `planning` → Planung
5. `implementing` → Implementierung
6. `reviewing` → Code-Review
7. `completed` → Fertig

#### Distributed Compute (`services/distributed_compute.py`)
Client-Outsourcing für rechenintensive Tasks:
- Embedding-Berechnung
- Batch-Inferenz
- Image Processing (CLIP)
- Audio Transcription (Whisper)

---

### 3. MCP Tools (134+)

**Kategorien:**
- `shell` - Bash-Befehle
- `code_*` - Code lesen/schreiben/patchen
- `memory_*` - Prisma Memory CRUD
- `ollama_*` - Ollama Management
- `search` - SearXNG Web Search
- `crawl` - Website Crawler
- `gemini_*` - Gemini Coordination
- `agent_*` - CLI Agent Control
- `remote_*` - Remote Task Execution

---

### 4. User Tiers

| Tier | Preis | Modelle | Token/Tag | MCP |
|------|-------|---------|-----------|-----|
| Guest | 0€ | Ollama | 50k | ❌ |
| Registered | 0€ | Ollama | 100k | ✓ |
| Pro | 17,99€ | Alle | 250k (Ollama ∞) | ✓ |
| Unlimited | 59,99€ | Alle | ∞ | ✓ |

---

### 5. Server-zu-Server (Node-to-Node)

**Aktueller Stand:**
- Backup-Server (`5.104.107.103`) als Ollama Node registriert
- Intern via `10.10.0.3:11434` erreichbar (VPN)

**Geplant (Server Hub):**
```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Hetzner   │◄───►│  Server Hub │◄───►│   Backup    │
│  (Primary)  │     │ (Koordinator)│     │ (Secondary) │
└─────────────┘     └─────────────┘     └─────────────┘
        │                  │                    │
        ▼                  ▼                    ▼
   Clients via        Federation          Failover &
   api.ailinux.me     Protocol            Load Balance
```

**Optionen für Ausbau:**
1. **WebSocket Federation** - Bidirektionale Verbindung zwischen Servern
2. **gRPC Mesh** - High-Performance RPC für Server-Kommunikation
3. **MCP over SSE** - MCP-Protokoll für Server-Federation
4. **Raft Consensus** - Für verteilte Entscheidungen

---

### 6. Konfiguration

**Pfade:**
```
/home/zombie/workspace/triforce/          # Backend Root
├── app/                        # FastAPI App
│   ├── routes/                 # API Endpoints
│   ├── services/               # Business Logic
│   └── main.py                 # Entry Point
├── config/                     # Konfiguration
│   └── users/                  # User Tier Files
├── docker/                     # Docker Stacks
└── log/                        # Logs
```

**Environment:**
```bash
OLLAMA_BASE_URL=http://localhost:11434
OPENROUTER_API_KEY=...
GEMINI_API_KEY=...
JWT_SECRET=...
```

---

### 7. Infrastruktur

| Server | IP | Funktion |
|--------|-----|----------|
| Hetzner EX63 | 138.201.50.230 | Backend, API |
| Backup | 5.104.107.103 | Backups, Ollama Secondary |
| Cloudflare | CF Proxy | Root Domain |

**Domains:**
- `api.ailinux.me` → Hetzner (Backend)
- `repo.ailinux.me` → Hetzner (Pakete)
- `search.ailinux.me` → Hetzner (SearXNG)
- `mail.ailinux.me` → Hetzner (Mail)
- `backup.ailinux.me` → Backup-Server
- `ailinux.me` → Cloudflare (WordPress)

---

*Dokumentation erstellt: 2025-12-31*
*Version: TriForce v2.80*
---

### Repository and Runtime Layout Notes

Keep the backend repository focused on reproducible source files. The server may contain large runtime directories for APT mirrors, WordPress content, n8n state, logs, generated client packages, and local virtual environments. These are operational assets, not normal source changes.

Recommended split:

| Category | Keep in Git | Keep outside Git / backup separately |
|---|---|---|
| Backend source | `app/`, `scripts/`, `requirements.txt`, templates | local `.venv/`, `__pycache__/`, `.pytest_cache/` |
| Docker services | Compose files, nginx/config templates, READMEs | `docker/repository/repo/`, `docker/repository/data/`, mirror logs, GPG runtime state |
| Clients | Source, requirements, release notes | `client-deploy/debian-build/`, packaged binaries generated by builds |
| Operations | systemd units, deployment docs | live logs, local patch backups, repair snapshots |

Before running cleanup on a production host, verify location and branch:

```bash
pwd
git rev-parse --show-toplevel
git branch --show-current
git status --short
```

`/home/zombie` must not be used as a Git repository. Use `/home/zombie/workspace/triforce` for the live server checkout and `/home/zombie/workspace/triforce-review` only on the local review machine when present.

### Route compatibility note

`app/main.py` currently mounts both modern route modules under `app/routes/` and legacy top-level route modules. `app/routes_sd3.py` and `app/routes_vision.py` are active compatibility endpoints and must be retained unless their imports and `include_router` calls are intentionally migrated.
