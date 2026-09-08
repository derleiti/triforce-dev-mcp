# TriForce AI Platform


<!-- AILINUX_STATUS_START -->
## Current production snapshot

- Production branch: `master`.
- Episodic-memory integration baseline: `65eec52f` (`Integrate episodic memory and harden agent bridges`).
- API base URL: `https://api.ailinux.me`.
- Health check: `GET /health` reports core service health plus the optional `episodic_memory` provider state.
- Systemd service: `triforce.service`, working directory `/home/zombie/triforce`, Uvicorn on port `9000`.
- Default chat model: `ollama/gemma4:12b`; local Ollama tag is `gemma4:12b`.
- OpenClaw gateway: `ws://127.0.0.1:18789`.
- Runtime hygiene: logs, local env files, runtime spools, Docker/n8n volumes, virtualenvs, backup files, and build outputs stay out of Git.
- Git safety: use explicit pathspecs; avoid broad cleanup commands in production checkouts.
<!-- AILINUX_STATUS_END -->

<div align="center">

![Version](https://img.shields.io/badge/version-2.81-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Nodes](https://img.shields.io/badge/federation-3%20nodes-orange)
![Models](https://img.shields.io/badge/models-925%2B-purple)
![MCP Tools](https://img.shields.io/badge/MCP%20core-39-red)

**Multi-LLM Orchestration Platform with Federation Support**

[Installation](#installation) • [Hub Sync](#server-hub-sync) • [CLI Agents](#cli-agents) • [Agent Memory](#native-agent-memory) • [MCP Tools](#mcp-tools) • [API](#api-usage)

</div>

---

## 🚀 Overview

TriForce is a decentralized AI platform that unifies **925+ LLM models** from **9 providers** into a single API. It features a federated mesh network, local Ollama integration, **39 default / 79 full canonical MCP tools**, and **4 autonomous CLI agents**.

### Key Features

- **Multi-Provider**: Gemini, Anthropic, Groq, Cerebras, Mistral, OpenRouter, GitHub, Cloudflare, Ollama
- **Federation**: Distributed compute across multiple nodes (64 cores, 156GB RAM)
- **MCP Tools**: 39-tool minimal default surface; 79 canonical tools in the full inventory, with legacy duplicates hidden from model discovery
- **CLI Agents**: 4 autonomous AI agents (Claude, Codex, Gemini, OpenCode)
- **Auto-Sync**: Automatic hub synchronization via update.ailinux.me (hourly)
- **Local Models**: Ollama integration for private inference
- **Native Agent Memory**: TriForce Memory Fabric combines curated knowledge with persistent episodic history through a provider abstraction
- **OpenAI Compatible**: Drop-in replacement for OpenAI API

### Federation Status

| Node | Cores | RAM | GPU | Role |
|------|-------|-----|-----|------|
| Hetzner EX63 | 20 | 62 GB | - | Master |
| Backup VPS | 28 | 64 GB | - | Hub |
| Zombie-PC | 16 | 30 GB | RX 6800 XT | Hub |
| **Total** | **64** | **156 GB** | 1 GPU | |

---

## 📦 Installation

### Client Installation

**Debian/Ubuntu (APT Repository)**:
```bash
# Add GPG key
curl -fsSL https://repo.ailinux.me/mirror/archive.ailinux.me/ailinux-archive-key.gpg | sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/ailinux.gpg

# Add repository
echo "deb https://repo.ailinux.me/mirror/archive.ailinux.me stable main" | sudo tee /etc/apt/sources.list.d/ailinux.list

# Install
sudo apt update && sudo apt install ailinux-client
```

**Direct Download**:
```bash
# Desktop (Linux)
wget https://update.ailinux.me/client/linux/ailinux-client_4.3.3_amd64.deb
sudo dpkg -i ailinux-client_4.3.3_amd64.deb

# Android (Beta)
wget https://update.ailinux.me/client/android/ailinux-1.0.0-arm64-v8a-debug.apk
```

### Server Installation

```bash
git clone https://github.com/derleiti/triforce.git
cd triforce
./scripts/install-hub.sh
systemctl start triforce.service
```

---

## 🔄 Server Hub Sync

All federation hubs synchronize automatically via **https://update.ailinux.me/server/**

### Quick Sync (One-Time)

```bash
curl -fsSL https://update.ailinux.me/server/scripts/hub-sync.sh | bash
```

### Automatic Updates (Hourly Timer)

```bash
# Download systemd units
sudo curl -o /etc/systemd/system/triforce-hub-sync.service \
  https://update.ailinux.me/server/scripts/triforce-hub-sync.service
sudo curl -o /etc/systemd/system/triforce-hub-sync.timer \
  https://update.ailinux.me/server/scripts/triforce-hub-sync.timer

# Enable hourly sync
sudo systemctl daemon-reload
sudo systemctl enable --now triforce-hub-sync.timer

# Check status
systemctl list-timers triforce-hub-sync.timer
```

### Create New Release (Master only)

```bash
# Bump version in app/config.py, then:
./scripts/create-release.sh 2.82

# All federation hubs auto-sync within 1 hour
```

### Update Safety Features

- SHA256 verification before extraction
- Automatic backup before update
- Service health check after restart
- Auto-rollback on failure

---

## 🤖 CLI Agents

Four autonomous AI agents with full MCP connectivity:

| Agent | Model | Mode | Purpose |
|-------|-------|------|---------|
| `claude-mcp` | Claude | dangerously-skip-permissions | Autonomous coding |
| `codex-mcp` | Codex | full-auto | Code execution |
| `gemini-mcp` | Gemini | YOLO | Coordinator/Lead |
| `opencode-mcp` | OpenCode | auto | Multi-model |

### Control Agents

```bash
# List agents
curl https://api.ailinux.me/v1/agents/cli -H "Authorization: Bearer TOKEN"

# Start agent
curl -X POST https://api.ailinux.me/v1/agents/cli/claude-mcp/start

# Send task
curl -X POST https://api.ailinux.me/v1/agents/cli/claude-mcp/call \
  -H "Content-Type: application/json" \
  -d '{"message": "fix the bug in main.py"}'

# Stop agent
curl -X POST https://api.ailinux.me/v1/agents/cli/claude-mcp/stop
```

---

## 🧠 Native Agent Memory

TriForce now has a **native memory fabric** with three deliberately separated layers:

| Layer | Purpose | Authority |
|-------|---------|-----------|
| **Runtime State** | What is happening right now in the active task/run | Current runtime only |
| **Episodic Memory** | What agents previously tried, changed, discovered or failed on | Historical context; never treated as truth by itself |
| **Curated TriForce Memory** | Verified facts, decisions, code knowledge, summaries and TODOs | Durable project knowledge |

The episodic layer is integrated through `EpisodicMemoryProvider` and currently uses a pinned **Claude-Mem 13.24.1** local worker. Claude-Mem is an implementation detail of the episodic provider, not the identity of TriForce memory. The same recalled history can therefore be supplied to Codex, Gemini, Nemotron, Qwen, Mistral, local models and future TriForce agents.

Automatic recall is centralized in `MemoryTriggerEngine` and can react to task start/resume, first file access, failures, retries, handoffs, merge/commit review points and run completion. File/failure recall is deduplicated, bounded by result/context limits, redacts common secrets and fails open if the worker is unavailable.

Promotion from episodic history into curated TriForce memory is **never automatic**. It is disabled by default and requires an observation marked `verified` plus explicit verification evidence. Current code, tests and runtime evidence always outrank historical memory.

Core configuration:

```bash
TRIFORCE_EPISODIC_MEMORY_ENABLED=false
TRIFORCE_EPISODIC_MEMORY_PROVIDER=claude-mem
TRIFORCE_EPISODIC_MEMORY_DATA_DIR=/path/to/claude-mem-data
TRIFORCE_MEMORY_AUTO_RECALL=true
TRIFORCE_MEMORY_MAX_RESULTS=4
TRIFORCE_MEMORY_TOKEN_BUDGET=1200
TRIFORCE_MEMORY_TIMEOUT=0.8
TRIFORCE_MEMORY_RECORD_ENABLED=false
TRIFORCE_MEMORY_PROMOTION_ENABLED=false
TRIFORCE_MEMORY_PROJECT_ID=triforce
```

The worker port is discovered from Claude-Mem `settings.json`; TriForce does not guess or hardcode it. The integration was verified end-to-end against Claude-Mem 13.24.1 with worker health, manual store and subsequent search using isolated synthetic data.

Detailed design, privacy model, triggers and troubleshooting: [`docs/architecture/episodic-memory.md`](docs/architecture/episodic-memory.md).

---

## 🔧 MCP Tools

The default MCP discovery surface is **39 core tools**. `inventory=all` exposes **79 canonical tools**; specialized inventories expose only the requested capability group. Selected categories:

| Category | Tools | Examples |
|----------|-------|----------|
| Chat | 3 | chat, models, specialist |
| Code | 6 | code_read, code_edit, code_search, code_patch |
| System | 9 | shell, status, health, logs, restart |
| Memory | 4 | memory_store, memory_search, memory_clear, memory_history |
| Web | 3 | search, crawl, web_fetch |
| Agents | 8 | agents, agent_call, agent_start, agent_stop |
| Ollama | 6 | ollama_run, ollama_list, ollama_pull |
| Gemini | 3 | gemini_coordinate, gemini_research, gemini_exec |

### MCP Usage

```bash
curl -X POST https://api.ailinux.me/v1/mcp \
  -H "Authorization: Basic $(echo -n 'user:pass' | base64)" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search","arguments":{"query":"AI news"}},"id":"1"}'
```

---

## 📡 API Usage

### Chat Completion (OpenAI Compatible)

```bash
curl https://api.ailinux.me/v1/chat/completions \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.0-flash",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Available Models

```bash
curl https://api.ailinux.me/v1/models -H "Authorization: Bearer TOKEN"
```

---

## 📋 URLs & Resources

| Resource | URL |
|----------|-----|
| API | https://api.ailinux.me |
| API Docs | https://api.ailinux.me/docs |
| API Health | https://api.ailinux.me/health |
| MCP Endpoint | https://api.ailinux.me/v1/mcp |
| Update Server | https://update.ailinux.me |
| Server Updates | https://update.ailinux.me/server/ |
| APT Repository | https://repo.ailinux.me |
| GPG Key | https://repo.ailinux.me/mirror/archive.ailinux.me/ailinux-archive-key.gpg |

---

## 📁 Project Structure

```
triforce/
├── app/                    # FastAPI Backend
│   ├── main.py            # Application entry
│   ├── routes/            # API endpoints
│   ├── services/          # Business logic (incl. episodic memory runtime/trigger/provider)
│   ├── mcp/               # MCP handlers & registry (incl. memory_history)
│   └── utils/             # Utilities & logging
├── config/                 # Configuration files
├── scripts/               # Management scripts
│   ├── hub-sync.sh        # Federation sync
│   ├── create-release.sh  # Release builder
│   └── start-triforce.sh  # Service starter
├── bin/                   # Agent wrappers
├── docs/                  # Documentation, including architecture/episodic-memory.md
└── docker/                # Docker configs
```

---

## 📄 License

MIT License - see [LICENSE](LICENSE)

---

<div align="center">

**Built with ❤️ by [AILinux](https://ailinux.me)**

[GitHub](https://github.com/derleiti/triforce) • [API Docs](https://api.ailinux.me/docs) • [Updates](https://update.ailinux.me)

</div>


## OpenClaw MCP Node

TriForce supports an OpenClaw-compatible MCP Node bridge. A local node connects via WebSocket to `/v1/mcp/node/connect`, authenticates through `/v1/auth/login`, and receives tool calls through `/v1/mcp/node/call`.

See: `docs/MCP_NODE_OPENCLAW.md`
---

## Repository Hygiene and Runtime Artifacts

TriForce keeps source code, configuration templates, scripts, and documentation in Git. Generated runtime state must stay out of commits. Treat the following paths as local/server state unless a maintainer explicitly asks for a migration commit:

- `logs/`, `triforce/logs/`, `*.log.old` - runtime logs and rotated logs.
- `.venv/`, `client-deploy/*/.venv/`, `__pycache__/`, `*.pyc` - local Python environments and bytecode caches.
- `docker/repository/repo/`, `docker/repository/data/`, `docker/repository/etc/gnupg/` - repository mirror and signing/runtime state.
- `client-deploy/debian-build/` - generated Debian package staging area.
- `.backups/`, `.debug/`, `.patch_backups/`, `.repair-backup/`, `*.bak`, `*.orig`, `*.REMOVED.*` - local repair, patch, and review artifacts.

Before deleting operational data, copy it outside the repository, for example to `/home/zombie/triforce-backup-cleanup/` or a host-specific backup location. Do not use `git add .` from `/home/zombie`; always confirm `pwd`, `git rev-parse --show-toplevel`, and `git branch --show-current` first.

### Active route inventory

The following route modules are active and intentionally coexist:

| Module | Mounted path | Purpose |
|---|---:|---|
| `app/routes/vision.py` | `/v1/images/analyze`, `/v1/images/analyze/upload` | Vision v1 image analysis API |
| `app/routes_vision.py` | `/vision/overlay-data` | Legacy/overlay vision data endpoint |
| `app/routes_sd3.py` | `/v1/images/generate` | Stable Diffusion 3 image generation endpoint |
| `app/routes/txt2img.py` | `/txt2img`, `/txt2img/queue`, `/txt2img/stream` | Text-to-image queue and streaming API |

Do not remove `app/routes_sd3.py` or `app/routes_vision.py` until `app/main.py` has been migrated and `python3 -m compileall app -q` passes.
