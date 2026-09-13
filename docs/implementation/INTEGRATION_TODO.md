# Backend Integration TODO

> Checkliste für die Integration der Client-Server-Architektur in das TriForce Backend
> Stand: 2025-12-13

---

## 📋 Übersicht

Diese Dokumentation beschreibt die notwendigen Änderungen am TriForce Backend um:

1. **API Key Vault** - Verschlüsselte Speicherung von Cloud-API-Keys
2. **Task Spawner** - Autonome Agents mit temporären API Keys
3. **Client Auth** - Authentifizierung für Desktop/Mobile Clients
4. **Chat Router** - Intelligente Model-Auswahl (lokal + Cloud)
5. **Client SDK** - Einheitliches SDK für alle Clients

---

## ✅ Phase 1: API Vault

### 1.1 Dateien erstellen
```bash
# Service
cp docs/implementation/api_vault.py app/services/api_vault.py

# Dependencies installieren
pip install cryptography --break-system-packages
```

### 1.2 In MCP Service integrieren
```python
# app/services/mcp_service.py

# Import hinzufügen
from .api_vault import VAULT_TOOLS, VAULT_HANDLERS, api_vault

# Tools registrieren (in get_all_tools oder ähnlich)
tools.extend(VAULT_TOOLS)

# Handlers registrieren
tool_map.update(VAULT_HANDLERS)
```

### 1.3 Tool Registry erweitern
```python
# app/mcp/tool_registry_v3.py

from app.services.api_vault import VAULT_TOOLS
all_tools.extend(VAULT_TOOLS)
```

### 1.4 Testen
```bash
# Server neustarten
sudo systemctl restart triforce

# Vault initialisieren (einmalig!)
curl -X POST https://api.ailinux.me/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"vault_init","arguments":{"master_password":"DEIN_MASTER_PW"}},"id":1}'

# API Key hinzufügen
curl -X POST https://api.ailinux.me/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"vault_add_key","arguments":{"provider":"anthropic","api_key":"sk-ant-..."}},"id":1}'
```

---

## ✅ Phase 2: Chat Router

### 2.1 Dateien erstellen
```bash
cp docs/implementation/chat_router.py app/services/chat_router.py
```

### 2.2 In MCP Service integrieren
```python
# app/services/mcp_service.py

from .chat_router import CHAT_ROUTER_TOOLS, CHAT_ROUTER_HANDLERS

tools.extend(CHAT_ROUTER_TOOLS)
tool_map.update(CHAT_ROUTER_HANDLERS)
```

### 2.3 Testen
```bash
# Intelligenter Chat (wählt automatisch Model)
curl -X POST https://api.ailinux.me/v1/mcp \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"chat_smart","arguments":{"message":"Schreib mir eine Python Funktion"}},"id":1}'

# Verfügbare Modelle auflisten
curl -X POST https://api.ailinux.me/v1/mcp \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"chat_list_models","arguments":{}},"id":1}'
```

---

## ✅ Phase 3: Task Spawner

### 3.1 Dateien erstellen
```bash
cp docs/implementation/task_spawner.py app/services/task_spawner.py
```

### 3.2 In MCP Service integrieren
```python
# app/services/mcp_service.py

from .task_spawner import TASK_SPAWNER_TOOLS, TASK_SPAWNER_HANDLERS

tools.extend(TASK_SPAWNER_TOOLS)
tool_map.update(TASK_SPAWNER_HANDLERS)
```

### 3.3 Testen
```bash
# Task einreichen
curl -X POST https://api.ailinux.me/v1/mcp \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"client_request_task","arguments":{"description":"Analysiere die System-Logs","agent_type":"claude"}},"id":1}'

# Task-Status abfragen
curl -X POST https://api.ailinux.me/v1/mcp \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"client_task_status","arguments":{"task_id":"task_abc123"}},"id":1}'
```

---

## ✅ Phase 4: Client Auth

### 4.1 Route erstellen
```bash
cp docs/implementation/client_auth.py app/routes/client_auth.py
```

### 4.2 In main.py registrieren
```python
# app/main.py

from app.routes.client_auth import router as client_auth_router

app.include_router(client_auth_router)
```

### 4.3 JWT Secret konfigurieren
```bash
# .env oder Environment
export JWT_SECRET="ein_sehr_langer_geheimer_string_hier"
```

### 4.4 Testen
```bash
# Client registrieren (als Admin)
curl -X POST https://api.ailinux.me/v1/auth/client/register \
  -H "Authorization: Bearer ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"client_id":"desktop-test","name":"Test Desktop","role":"desktop"}'

# Client authentifizieren
curl -X POST https://api.ailinux.me/v1/auth/client \
  -H "Content-Type: application/json" \
  -d '{"client_id":"desktop-test","client_secret":"DAS_GENERIERTE_SECRET"}'
```

---

## ✅ Phase 5: Client SDK (Desktop)

### 5.1 SDK Paket erstellen
```bash
mkdir -p ~/ailinux-client/ailinux_sdk
cp docs/implementation/ailinux_sdk_client.py ~/ailinux-client/ailinux_sdk/client.py

# __init__.py
echo "from .client import AILinuxClient, ClientConfig, quick_chat, quick_task" > ~/ailinux-client/ailinux_sdk/__init__.py

# requirements.txt
cat > ~/ailinux-client/requirements.txt << EOF
aiohttp>=3.9.0
python-dotenv>=1.0.0
EOF
```

### 5.2 Client .env erstellen
```bash
mkdir -p ~/.config/ailinux
cat > ~/.config/ailinux/.env << EOF
AILINUX_CLIENT_ID=desktop-markus-main
AILINUX_CLIENT_SECRET=DEIN_CLIENT_SECRET
AILINUX_SERVER=https://api.ailinux.me
AILINUX_DEVICE_NAME=Markus Gaming PC

ALLOW_BASH=true
ALLOW_FILE_READ=true
ALLOW_FILE_WRITE=false
ALLOW_LOGS=true

ALLOWED_PATHS=/home/zombie,/tmp,/var/log
BLOCKED_PATHS=/etc/shadow,/root,~/.ssh
EOF
```

### 5.3 Testen
```bash
cd ~/ailinux-client
python -m ailinux_sdk.client "Hallo Nova!"
```

---

## 📁 Dateistruktur nach Integration

```
/home/zombie/workspace/triforce/
├── app/
│   ├── services/
│   │   ├── api_vault.py         # NEU: Verschlüsselter Key-Tresor
│   │   ├── chat_router.py       # NEU: Intelligente Model-Auswahl
│   │   ├── task_spawner.py      # NEU: Agent-Spawner
│   │   ├── mcp_service.py       # ERWEITERT: Neue Tools
│   │   └── ...
│   │
│   ├── routes/
│   │   ├── client_auth.py       # NEU: Client-Authentifizierung
│   │   └── ...
│   │
│   ├── mcp/
│   │   └── tool_registry_v3.py  # ERWEITERT: Neue Tools
│   │
│   └── main.py                  # ERWEITERT: Neue Route
│
├── .vault/                      # NEU: Verschlüsselter Vault
│   ├── api_keys.enc
│   └── salt
│
└── docs/
    ├── CLIENT_SERVER_ARCHITECTURE.md
    └── implementation/
        ├── api_vault.py
        ├── chat_router.py
        ├── task_spawner.py
        ├── client_auth.py
        └── ailinux_sdk_client.py
```

---

## 🔧 Schnell-Integration (Copy-Paste)

```bash
# Alle Services auf einmal kopieren
cd /home/zombie/workspace/triforce

# 1. API Vault
cp docs/implementation/api_vault.py app/services/

# 2. Chat Router
cp docs/implementation/chat_router.py app/services/

# 3. Task Spawner
cp docs/implementation/task_spawner.py app/services/

# 4. Client Auth
cp docs/implementation/client_auth.py app/routes/

# 5. Dependency
pip install cryptography pyjwt --break-system-packages

# 6. Vault-Verzeichnis erstellen
mkdir -p .vault
chmod 700 .vault

# 7. Restart
sudo systemctl restart triforce
```

---

## 🔐 Nach der Integration: Vault Setup

```bash
# 1. Vault initialisieren
# WICHTIG: Master-Passwort sicher aufbewahren!

# 2. API Keys hinzufügen
vault_add_key provider=anthropic api_key=sk-ant-...
vault_add_key provider=openai api_key=sk-...
vault_add_key provider=google api_key=AIza...
vault_add_key provider=mistral api_key=...
vault_add_key provider=groq api_key=gsk_...
vault_add_key provider=cerebras api_key=csk-...

# 3. Keys prüfen
vault_list_keys
```

---

## 📊 Neue MCP Tools nach Integration

| Tool | Beschreibung |
|------|--------------|
| `vault_init` | Vault initialisieren |
| `vault_unlock` | Vault entsperren |
| `vault_lock` | Vault sperren |
| `vault_add_key` | API Key hinzufügen |
| `vault_list_keys` | Keys auflisten |
| `vault_remove_key` | Key entfernen |
| `vault_status` | Status prüfen |
| `chat_smart` | Intelligenter Chat |
| `chat_list_models` | Modelle auflisten |
| `client_request_task` | Task einreichen |
| `client_task_status` | Task-Status |
| `client_task_output` | Task-Output |
| `client_list_tasks` | Tasks auflisten |
| `client_cancel_task` | Task abbrechen |

**Gesamt: 15 neue Tools**

---

## 🐻 Brumo sagt

*„Kopieren. Neustarten. Läuft. So einfach."*

---

## 🖥️ Phase 6: Client Tool-Routing

### 6.1 Tool-Kategorien

Clients können **alle Tools** nutzen - aber mit unterschiedlicher Ausführung:

| Kategorie | Ausführung | Tools |
|-----------|------------|-------|
| **Server-Only** | TriForce Server | `chat_smart`, `web_search`, `vault_*`, `ollama_*`, `tristar_*` |
| **Client-Local** | Lokales Dateisystem | `codebase_*`, `code_scout`, `bash_exec`, `file_*` |

### 6.2 Verwendung im Client

```python
from ailinux_sdk import AILinuxClient, call_tool_smart, local_code_search

# Client initialisieren
client = AILinuxClient.from_env()
await client.connect()

# LOKAL: Code durchsuchen (läuft auf dem Client)
results = await local_code_search(client, "def main", "/home/markus/projects")

# LOKAL: Datei bearbeiten
await call_tool_smart(client, "codebase_edit", {
    "path": "/home/markus/projects/test.py",
    "mode": "replace",
    "old_text": "print('old')",
    "new_text": "print('new')"
})

# SERVER: Chat mit Cloud-Model (API Keys auf Server)
response = await call_tool_smart(client, "chat_smart", {
    "message": "Erkläre mir Python Decorators"
})

# SERVER: Web-Suche (läuft auf Server)
results = await call_tool_smart(client, "web_search", {
    "query": "Python FastAPI Tutorial"
})
```

### 6.3 Client .env Konfiguration

```bash
# ~/.config/ailinux/.env

# Server-Verbindung
AILINUX_CLIENT_ID=desktop-markus-main
AILINUX_CLIENT_SECRET=geheim123
AILINUX_SERVER=https://api.ailinux.me
AILINUX_DEVICE_NAME=Gaming-PC

# Lokale Berechtigungen
ALLOW_BASH=true
ALLOW_FILE_READ=true
ALLOW_FILE_WRITE=true
ALLOW_LOGS=true

# Pfad-Beschränkungen
ALLOWED_PATHS=/home/markus,/tmp,/var/log
BLOCKED_PATHS=/etc/shadow,/root,~/.ssh/id_rsa
```

### 6.4 Vorteile

1. **Schnelles lokales Coden** - Keine Netzwerk-Latenz für Datei-Operationen
2. **Sichere API Keys** - Bleiben auf dem Server, Client braucht keine
3. **Volle Tool-Power** - Alle 130+ Tools verfügbar
4. **Flexibel** - Server-Logik + lokale Ausführung kombiniert

---

## 🐻 Brumo sagt

*"Server für die Cloud. Client für die Files. Beides zusammen: Magie."*
