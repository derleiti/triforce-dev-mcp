# TriForce Architecture Overview

## System-Übersicht

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              USERS                                       │
│    Desktop Client │ Web Client │ API Direct │ MCP Integration           │
└────────────┬──────────────┬───────────┬──────────────┬──────────────────┘
             │              │           │              │
             ▼              ▼           ▼              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         LOAD BALANCER                                    │
│                      (Cloudflare / Nginx)                                │
│                        api.ailinux.me                                    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRIFORCE BACKEND                                 │
│                          (FastAPI + Uvicorn)                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │    Auth     │  │   Router    │  │    MCP      │  │  Federation │    │
│  │  (JWT/RBAC) │  │ (Provider)  │  │ (39 Core)   │  │   (Mesh)    │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │Memory Fabric│  │   Logging   │  │   Metrics   │  │   Agents    │    │
│  │Curated+Epis.│  │   (JSON)    │  │ (Prometheus)│  │  (CLI LLM)  │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────────┬────────────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│     REDIS       │    │     OLLAMA      │    │    SEARXNG      │
│   (Cache/Queue) │    │  (Local Models) │    │   (Web Search)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         FEDERATION MESH                                  │
│          (WireGuard VPN + HMAC-SHA256 Auth + Timestamp Validation)      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │    Hetzner      │  │     Backup      │  │    Zombie-PC    │         │
│  │    (Master)     │◄─┼─►    (Hub)     ◄┼─►│      (Hub)      │         │
│  │   10.10.0.1     │  │   10.10.0.3     │  │   10.10.0.2     │         │
│  │   20c / 62GB    │  │   28c / 64GB    │  │   16c / 30GB    │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         LLM PROVIDERS                                    │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐    │
│  │ Gemini │ │Anthropic│ │  Groq  │ │Cerebras│ │Mistral │ │OpenRout│    │
│  │  (15)  │ │   (5)   │ │  (12)  │ │  (4)   │ │  (8)   │ │ (200)  │    │
│  └────────┘ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘    │
│  ┌────────┐ ┌────────┐ ┌────────┐                                      │
│  │ GitHub │ │Cloudfl.│ │ Ollama │  ← Lokal, 33 Modelle                 │
│  │  (10)  │ │  (15)  │ │  (33)  │                                      │
│  └────────┘ └────────┘ └────────┘                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Komponenten

### 1. Backend (FastAPI)

**Pfad:** `/home/zombie/triforce/app/`

| Modul | Beschreibung |
|-------|--------------|
| `main.py` | App-Entry, Router-Setup |
| `config.py` | Konfiguration, Environment |
| `routes/` | API-Endpoints |
| `services/` | Business Logic |
| `mcp/` | MCP Protocol Implementation |

### 2. Authentifizierung

**4-Layer Security:**

1. **HTTPS** - TLS 1.3 via Cloudflare
2. **JWT** - User Authentication
3. **RBAC** - Role-Based Access Control
4. **Federation PSK** - Node-to-Node Auth

**Tiers:**

| Tier | Rate Limit | Tokens/Tag | Modelle |
|------|------------|------------|---------|
| Guest | 10/min | 1k | Basic |
| Free | 30/min | 10k | Alle |
| Pro | 100/min | 250k | Alle + Priority |
| Unlimited | 500/min | ∞ | Alle + Max Priority |

### 3. Provider Router

**Routing-Logik:**

```python
# Modell-Format: {provider}/{model_name}
model = "gemini/gemini-2.0-flash"

# Router wählt Provider
provider = model.split("/")[0]  # "gemini"
model_name = model.split("/")[1]  # "gemini-2.0-flash"

# Provider-spezifischer Handler
handler = PROVIDERS[provider]
response = await handler.chat(model_name, messages)
```

### 4. MCP (Model Context Protocol)

**39 Core-Tools** im Default-Profil und **79 kanonische Tools** mit `inventory=all`, darunter:

- Search & Web (3)
- Code & Development (6)
- Memory & Knowledge (4): `memory_store`, `memory_search`, `memory_clear`, `memory_history`
- AI & Chat (3)
- System & Admin (5)
- Mesh & Federation (5)
- Ollama (5)
- Weitere (104)

### 5. Native Agent Memory

TriForce besitzt eine native Memory-Abstraktion mit zwei persistenten Ebenen plus Runtime-State:

1. **Curated TriForce Memory** für bestätigte Facts, Entscheidungen, Codewissen, Summaries und TODOs.
2. **Episodic Memory** für frühere Agenten-Runs, Fehler, Lösungsversuche, Datei-Kontext und Handoffs.
3. **Runtime State** für den momentanen Workflow.

Die episodische Ebene läuft über `MemoryTriggerEngine` → `EpisodicMemoryProvider` → `ClaudeMemAdapter`. Aktuell ist Claude-Mem 13.24.1 der Provider. Recall ist modellneutral: auch Codex, Gemini, Nemotron, Qwen, Mistral oder lokale Modelle können denselben historischen Kontext erhalten.

Die Integration ist fail-open, budgetiert, projektgescoped, dedupliziert und redigiert typische Secrets. Promotion in kuratiertes Memory ist separat, standardmäßig deaktiviert und nur für `verified` Observations mit Evidence erlaubt.

Siehe [episodic-memory.md](episodic-memory.md).

### 6. Federation Mesh

**Protokoll:**
- Transport: WireGuard VPN (10.10.0.0/24)
- Auth: PSK + HMAC-SHA256
- Validation: Timestamp ±30s

**Nodes:**
```json
{
  "hetzner": {"ip": "10.10.0.1", "role": "master"},
  "backup": {"ip": "10.10.0.3", "role": "hub"},
  "zombie-pc": {"ip": "10.10.0.2", "role": "hub"}
}
```

---

## Datenfluss

### Chat Request

```
1. Client → API Gateway (HTTPS)
2. Gateway → Auth Middleware (JWT Validation)
3. Auth → Rate Limiter (Tier Check)
4. Rate Limiter → Router (Provider Selection)
5. Router → Provider Handler (API Call)
6. Provider → Response (Stream/Complete)
7. Response → Client
```

### MCP Tool Call

```
1. Client → MCP Endpoint (JSON-RPC)
2. MCP → Tool Registry (Lookup)
3. Registry → Handler (Execution)
4. Handler → Result (JSON)
5. Result → Client
```

### Episodic Recall

```text
1. Agent/Runtime erzeugt Event
2. MemoryTriggerEngine normalisiert und dedupliziert
3. Provider sucht projektgescoped episodische Historie
4. Relevanz/Staleness/Commit-Bezug werden bewertet
5. Wenige Treffer werden innerhalb des Context-Budgets injiziert
6. Bei Workerfehlern läuft TriForce ohne Recall weiter
```

### Federation Task

```
1. Client → Master Node
2. Master → Task Queue (Redis)
3. Queue → Best Node (Load Balancing)
4. Node → Execute (Local/Remote)
5. Result → Master
6. Master → Client
```

---

## Skalierung

### Horizontal

- Mehr Hub-Nodes zur Federation
- Load Balancing über alle Nodes
- GPU-Nodes für lokale Inference

### Vertikal

- Mehr RAM für größere Ollama-Modelle
- Mehr CPU für parallele Requests
- NVMe für schnellere I/O

---

## Monitoring

### Endpoints

```
GET /health          # Core + optional episodic_memory health
GET /v1/mesh/resources  # Federation Status
GET /v1/logs         # System Logs (Admin)
```

### Metriken

- Request Latency (p50, p95, p99)
- Tokens/Second
- Error Rate
- Cache Hit Rate
