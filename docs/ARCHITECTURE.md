# TriForce System Architecture

## Überblick

TriForce ist eine verteilte Multi-LLM Plattform mit folgenden Hauptkomponenten:

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL ACCESS                                │
│                                                                          │
│    Internet → Cloudflare → Apache Proxy → TriForce Backend              │
│              (DDoS/WAF)    (SSL/Auth)     (FastAPI)                      │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         TRIFORCE BACKEND v2.81                           │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │   Routes   │  │  Services  │  │    MCP     │  │   Agents   │        │
│  │ (FastAPI)  │  │ (Business) │  │  (Tools)   │  │  (Multi)   │        │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘        │
│                                                                          │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐        │
│  │   Redis    │  │Memory Fabric│ │ Federation │  │  Mesh AI   │        │
│  │  (Cache)   │  │Curated+Epis.│ │  (P2P)     │  │  (Coord)   │        │
│  └────────────┘  └────────────┘  └────────────┘  └────────────┘        │
└──────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          LLM PROVIDERS                                   │
│                                                                          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐           │
│  │ Gemini  │ │Anthropic│ │  Groq   │ │Cerebras │ │ Mistral │           │
│  │  (15)   │ │   (5)   │ │  (12)   │ │   (4)   │ │   (8)   │           │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘           │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│  │OpenRout │ │ GitHub  │ │Cloudfl. │ │ Ollama  │  = 640+ Models        │
│  │  (200)  │ │  (10)   │ │  (15)   │ │  (∞)    │                       │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘                       │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Kernkomponenten

### 1. API Gateway (Apache + FastAPI)

**Verantwortlich für:**
- SSL-Terminierung
- Rate Limiting
- Authentication (JWT/Basic)
- Request Routing

**Ports:**
- 443 (HTTPS) → Apache Proxy
- 9000 (intern) → FastAPI Backend
- 9100 (extern) → X-Forwarded-Port Auth

### 2. Backend Services

| Service | Funktion |
|---------|----------|
| `chat_router` | LLM-Anfragen verteilen |
| `model_registry` | Modell-Verfügbarkeit |
| `tier_manager` | Berechtigungen prüfen |
| `rate_limiter` | Token-Limits |
| `mesh_coordinator` | Multi-Agent Tasks |

### 3. MCP System

Der Unified Registry exponiert standardmäßig **39 Core-Tools**; `inventory=all` liefert **79 kanonische Tools**. Legacy-Aliase und doppelte Fähigkeiten bleiben nur als Kompatibilitätsschicht und werden Modellen nicht advertised. Spezialisierte Inventories wie Memory, Filesystem, Browser, Forum oder WordPress können gezielt angefordert werden.

Discovery und Dispatch besitzen jeweils genau **eine kanonische Implementierung** in `app/routes/mcp.py` zusammen mit `app/mcp/tool_registry_unified.py`. Historische Python-Einstiege in `app/services/mcp_service.py` delegieren nur noch auf diese Implementierung und führen keine eigene Tool-Liste bzw. keinen eigenen Dispatcher mehr.

Die Memory-Oberfläche besteht aktuell aus:

- `memory_store` — kuratiertes Wissen speichern
- `memory_search` — kuratiertes Wissen suchen
- `memory_clear` — kuratiertes Memory verwalten
- `memory_history` — episodische Historie suchen, Timeline/Details abrufen und verifizierte Observations kontrolliert promoten

`memory_history` ist für authentifizierte interne Operatoren gedacht; episodische Treffer gelten als untrusted historical context.

### 4. Native Agent Memory Fabric

TriForce trennt drei Zuständigkeiten strikt:

```text
Runtime Events
    │
    ▼
MemoryTriggerEngine
    │
    ▼
EpisodicMemoryProvider ──► ClaudeMemAdapter ──► local Claude-Mem worker / SQLite
    │                                              │
    └──────── bounded, redacted recall ◄───────────┘
                       │
                       ▼
                   Agent Context
                       │ verified + evidence + explicit promotion
                       ▼
               TriForce Curated Memory
```

- **Runtime State** beantwortet: Was passiert gerade?
- **Episodic Memory** beantwortet: Was ist früher passiert/versucht worden?
- **Curated Memory** beantwortet: Was wissen wir verifiziert und dauerhaft?

Der Provider ist optional und fail-open. Worker-Ausfall, Timeout oder malformed responses dürfen keinen normalen TriForce-Workflow stoppen. Auto-Recall verwendet Result-Limits, Kontextbudget, Deduplication, Secret-Redaction und einen Circuit Breaker. Historische Erinnerungen können stale oder falsch sein; aktueller Code, Tests und Runtime-Evidence haben Vorrang.

Die aktuelle Implementierung bindet Claude-Mem **13.24.1** über einen TriForce-eigenen Adapter an. Dadurch ist episodisches Memory modellneutral und nicht auf Claude beschränkt.

### 5. Federation Mesh

3 Nodes über WireGuard VPN:
- Master (Hetzner): Koordination
- Hub (Backup): Compute
- Hub (Zombie-PC): GPU

---

## Datenfluss

### Chat Request

```
1. Client → HTTPS Request
2. Apache → SSL + Auth
3. FastAPI → Rate Check
4. Router → Provider Select
5. Provider → LLM Call
6. Response → Stream/JSON
7. Client ← Response
```

### MCP Tool Call

```
1. Client → MCP Request
2. Backend → Tool Lookup
3. Handler → Execute
4. Result → Format
5. Client ← Response
```

### Episodic Memory Recall

```text
1. Runtime Event → MemoryTriggerEngine
2. Scope/Trigger/Dedup prüfen
3. ClaudeMemAdapter → lokale Worker-Suche
4. Projekt-/Relevanz-/Stale-Filter
5. Result- und Context-Budget anwenden
6. Historischen Kontext als untrusted markieren
7. Agent arbeitet weiter; bei Memory-Fehlern fail-open ohne Recall
```

### Federation Request

```
1. Node A → Sign Request (PSK)
2. WireGuard → Encrypt
3. Node B → Validate Signature
4. Execute → Process
5. Node B → Response
6. Node A ← Result
```

---

## Verzeichnisstruktur

```
triforce/
├── app/
│   ├── main.py              # FastAPI Entry
│   ├── config.py            # Konfiguration
│   ├── routes/              # API Endpoints
│   │   ├── mcp.py           # MCP Handler (149k)
│   │   ├── mesh.py          # Mesh/Federation
│   │   ├── client_*.py      # Client APIs
│   │   └── ...
│   ├── services/            # Business Logic
│   │   ├── chat_router.py
│   │   ├── mesh_coordinator.py
│   │   ├── episodic_memory.py      # Provider boundary / Claude-Mem adapter
│   │   ├── memory_trigger.py       # Central recall/record/promotion policy
│   │   ├── memory_runtime.py       # Runtime event translation
│   │   └── ...
│   ├── mcp/                 # MCP Tools
│   │   ├── tool_registry_v5.py
│   │   ├── tool_registry_unified.py
│   │   └── handlers_memory_history.py
│   └── utils/
├── config/
│   ├── triforce.env         # Environment
│   ├── federation_nodes.json
│   └── users.json
├── client-deploy/           # Client Builds
│   ├── ailinux-client/
│   └── aiwindows-client/
├── docs/                    # Dokumentation
└── scripts/                 # Install/Deploy
```

---

## Technologie-Stack

| Komponente | Technologie |
|------------|-------------|
| Backend | Python 3.12, FastAPI, Uvicorn |
| Cache | Redis |
| Curated Memory | TriForce/TriStar persistent memory |
| Episodic Memory | TriForce Provider API + local Claude-Mem worker (SQLite) |
| Proxy | Apache 2.4 |
| VPN | WireGuard |
| Container | Docker (optional) |
| Search | SearXNG |
| GPU | Ollama (ROCm/CUDA) |

---

## Skalierung

### Horizontal (mehr Nodes)

- Neue Hubs zur Federation hinzufügen
- Load Balancing automatisch
- Modelle verteilt

### Vertikal (mehr Power)

- RAM für größere Modelle
- GPU für schnellere Inference
- CPU für mehr Parallelität

---

## Security

### Authentication

1. **JWT Tokens** - Für Clients
2. **Basic Auth** - Legacy/Admin
3. **PSK Signatures** - Federation

### Encryption

1. **TLS 1.3** - Externe Verbindungen
2. **WireGuard** - Federation
3. **HMAC-SHA256** - Request Signing

### Access Control

1. **Tier System** - Free/Pro/Unlimited
2. **IP Whitelist** - Federation
3. **Rate Limits** - Per User/Tier

---

## Monitoring

### Endpunkte

| Endpoint | Funktion |
|----------|----------|
| `/health` | System-Status inklusive optionalem `episodic_memory` Health-State |
| `/v1/mesh/resources` | Federation-Status |
| `/v1/mesh/status` | Mesh-Koordinator |

### Logs

```bash
# Service Logs
journalctl -u triforce.service -f

# Kategorien
logs?category=api|llm|mcp|error|agent
```

---

## Weiterführend

- [Installation](INSTALL.md)
- [API Reference](api/REST.md)
- [MCP Tools](api/MCP.md)
- [Federation](architecture/FEDERATION.md)
- [Episodic Agent Memory](architecture/episodic-memory.md)


## Project Memory und synchronisierte Agent-Historie

TriForce und AICoder trennen vier Memory-Ebenen bewusst:

- **Runtime State**: aktueller Run, aktive Tools, aktuelle Code-/Runtime-Evidence.
- **Project Memory**: kanonischer aktueller Projektzustand (`todo`, `idea`, `decision`, `architecture`, `bug`, `lesson`, `feature`, `project_summary`, `documentation`).
- **Curated Memory**: explizit verifiziertes dauerhaftes Wissen.
- **Episodic Memory / Claude-Mem**: append-only historische Revisionen und Erfahrungen.

Aktuelle Code- und Runtime-Evidence hat Vorrang vor Project Memory. Project Memory hat Vorrang vor historischer episodischer Erinnerung. Claude-Mem ist **keine Sync Authority**.

### Sync-Protokoll

`POST /v1/project-memory/sync` verwendet die bestehende authentifizierte Client-Session. Die Owner-ID wird ausschließlich serverseitig aus der Session abgeleitet und niemals aus Client-Daten übernommen. Jeder Project-Memory-Eintrag enthält `entity_id`, `project_key`, `kind`, `title`, `content`, `status`, `version`, `base_version`, `server_seq`, `content_hash`, `device_id`, `source`, `updated_at` und `deleted`.

Optimistic Concurrency:

```text
base_version == current.version
  -> Revision akzeptieren
  -> version + 1
  -> server_seq serverseitig vergeben

base_version != current.version
  -> Konflikt
  -> Serverstand nicht überschreiben
  -> Konfliktversuch append-only in revisions erhalten
```

Die Reihenfolge wird ausschließlich über `server_seq` bestimmt. Client-Uhren entscheiden nicht über Gewinner. Löschungen werden als Tombstones (`deleted=true`) synchronisiert; Hard Deletes sind im Sync-Pfad verboten.

### Project Identity und Restore

AICoder verwendet bevorzugt eine normalisierte und gehashte Git-`remote.origin`-Identität. Ohne Remote wird eine persistente Repository-UUID in `.git/ailinux-project-id`, danach eine persistente Workspace-UUID verwendet; ein Pfad-Hash ist nur Fallback. Dadurch erzeugt ein verschobenes Git-Repository kein neues Gedächtnis.

Nach einer Neuinstallation kann sich AICoder erneut anmelden, dieselbe Projektidentität bestimmen und den kanonischen Project-Memory-Stand von TriForce ab Cursor `0` wiederherstellen.

### Datenschutz und Offline-Verhalten

Synchronisiert werden nur strukturierte Project-Memory-Einträge. Chats, Repositories, rohe Tool-Ausgaben, `.env`, Credentials und Tokens werden nicht automatisch hochgeladen. Secret-artige Inhalte werden client- und serverseitig vor Persistenz/Upload blockiert. Source-Dateien werden über Evidence-Metadaten wie Pfad/Hash referenziert, nicht als kompletter Inhalt synchronisiert.

AICoder bleibt local-first: lokale Änderungen landen in der bestehenden `~/.config/ai-coder/evidence.db` und werden als `dirty` markiert. Netzwerk-/Auth-Ausfälle verhindern weder lokale Speicherung noch Coding. Konflikte werden lokal in `project_memory_conflicts` konserviert. Erfolgreicher Push/Pull aktualisiert Cursor und `server_seq`.

### Claude-Mem Mirror

Jede neu akzeptierte Project-Memory-Revision wird best-effort über die bestehende `EpisodicMemoryProvider`-/`ClaudeMemAdapter`-Schicht als `project_memory_revision` gespiegelt. Ein Claude-Mem-Ausfall darf den Project-Memory-Commit oder Sync niemals zurückrollen.


## Compute-Offload und Node-Rollen

Die drei festen AILinux-Nodes haben unterschiedliche Betriebsrollen:

- **Hetzner / ailinux** bleibt Public Hub, API-/Control-Plane und zuverlässiger Fallback. Rechenarbeit soll dort nicht bevorzugt landen.
- **backup** ist der bevorzugte allgemeine Ollama-/Cloud-Proxy-Compute-Node. Er besitzt eigenen Swap und große freie Storage-/RAM-Reserve.
- **zombie-pc** wird modellabhängig für lokale Compute-Modelle genutzt. Der normale Desktop-/Workspace-Betrieb bleibt davon unabhängig.

Ollama-Anfragen verwenden eine gemeinsame Node-Auswahl. Für normale Modelle ist die Reihenfolge `backup -> hetzner`. Für explizit auf Zombie-PC vorhandene Modelle ist sie `zombie-pc -> backup -> hetzner`. Netzwerk-, Timeout- und Serverfehler führen zuerst zum nächsten Node; erst wenn alle passenden Nodes für das gewünschte Modell versagen, greift ein vorhandener Modell-Fallback.

Die Federation-Gewichtung bevorzugt bei gleicher freier Kapazität Compute-Nodes gegenüber dem Public Hub. Hetzner bleibt trotzdem verfügbar und übernimmt bei Ausfall der Offload-Nodes.

### Memory Storage

Die aktive Claude-Mem-SQLite bleibt auf dem lokalen NVMe des TriForce-Hubs. Sie wird **nicht** über NFS/SSHFS oder ein anderes Netzwerk-Dateisystem gemeinsam schreibbar gemacht. Stattdessen erzeugt Hetzner stündlich mit SQLite-`.backup` einen konsistenten Snapshot und repliziert ihn auf den Backup-Node. Dadurch bleibt die aktive Datenbank lokal schnell und robust, während historische Wiederherstellungskapazität auf dem großen Backup-Datenträger liegt.

### Host-Reserven

Swap und Dateisystemreserve werden vor Compute-/Memory-Wachstum behandelt. Aktueller Zielzustand:

- Hetzner: vorhandener großer Swap als Hub-Reserve.
- backup: dedizierter Swapfile und niedrige Swappiness.
- zombie-pc: vorhandene NVMe-Swap-Partition aktiv plus kleiner Swapfile als zusätzliche Reserve.

Compute-Offload darf Root-, Docker-/Service- und Swap-Reserve nicht verdrängen.
