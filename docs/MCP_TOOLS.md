# MCP Tools Reference

**Last Updated:** 2026-09-09
**Default MCP surface:** 39 core tools
**Full canonical surface:** 79 tools

## Overview

TriForce now exposes a **minimal 39-tool default surface** through `/v1/mcp`. Clients can request `inventory=all` for the **79-tool canonical surface** or a specialized inventory such as `memory`, `filesystem`, `group_chat`, `forum`, `wordpress`, `browser` or `integration`. Legacy aliases and duplicate handlers may remain callable for compatibility, but they are intentionally hidden from normal model discovery.

`app/routes/mcp.py` is the single canonical MCP discovery and tool-call dispatcher. Compatibility functions in `app/services/mcp_service.py` delegate to it; they do not maintain a second registry.

## Authentication

```bash
# Basic Auth
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <base64(user:pass)>" \
  -H "Content-Type: application/json"

# Bearer Token
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json"
```

## Tool Categories

### System Tools

| Tool | Description |
|------|-------------|
| `status` | System status overview |
| `health` | Health check |
| `init` | Initialize session |
| `config` | Get configuration |
| `config_set` | Set configuration |
| `restart` | Restart service |

### Agent Tools

| Tool | Description |
|------|-------------|
| `agents` | List all CLI agents |
| `agent_start` | Start a CLI agent |
| `agent_stop` | Stop a CLI agent |
| `agent_call` | Send message to agent |
| `agent_broadcast` | Message to all agents |
| `bootstrap` | Bootstrap all agents |

### Model Tools

| Tool | Description |
|------|-------------|
| `models` | List available models |
| `chat` | Chat with any model |
| `specialist` | Route to specialist model |

### Ollama Tools

| Tool | Description |
|------|-------------|
| `ollama_status` | Ollama server status |
| `ollama_list` | List local models |
| `ollama_run` | Run inference |
| `ollama_pull` | Download model |
| `ollama_delete` | Delete model |
| `ollama_embed` | Generate embeddings |

### Memory Tools

| Tool | Description |
|------|-------------|
| `memory_store` | Store information |
| `memory_search` | Search memory |
| `memory_clear` | Clear/manage curated memory |
| `memory_history` | Scoped episodic history: compact search/recent, timeline/get, and controlled promotion of verified observations |

#### Memory model

`memory_store` / `memory_search` operate on **curated TriForce memory**. `memory_history` operates on the separate **episodic history provider**. Episodic results are historical observations, not trusted instructions or current facts. Current code, tests and runtime evidence take precedence.

`memory_history` is restricted to authenticated internal operator credentials and a configured `TRIFORCE_MEMORY_PROJECT_ID`. Promotion is additionally disabled unless `TRIFORCE_MEMORY_PROMOTION_ENABLED=true`, and the source observation must be `verified` with verification evidence.

Available `memory_history` actions:

| Action | Purpose |
|--------|---------|
| `search` | Compact, budgeted episodic recall for a query |
| `recent` | Recent scoped history |
| `timeline` | Context around one selected observation |
| `get` | Retrieve selected observation IDs |
| `promote` | Explicitly promote verified evidence into curated TriForce memory |

### Code Tools

| Tool | Description |
|------|-------------|
| `code_read` | Read file |
| `code_search` | Search codebase |
| `code_edit` | Edit file |
| `code_tree` | Directory structure |
| `code_patch` | Apply patch |

### Search Tools

| Tool | Description |
|------|-------------|
| `search` | Web search |
| `crawl` | Crawl website |

### Mesh Tools

| Tool | Description |
|------|-------------|
| `mesh_status` | Mesh network status |
| `mesh_agents` | List mesh agents |
| `mesh_task` | Submit mesh task |

### Log Tools

| Tool | Description |
|------|-------------|
| `logs` | Get system logs |
| `logs_errors` | Get error logs |
| `logs_stats` | Log statistics |

### Vault Tools

| Tool | Description |
|------|-------------|
| `vault_status` | Vault status |
| `vault_keys` | List API keys |
| `vault_add` | Add API key |

### Evolution Tools

| Tool | Description |
|------|-------------|
| `evolve` | Run evolution analysis |
| `evolve_history` | Evolution history |

### Gemini Coordinator Tools

| Tool | Description |
|------|-------------|
| `gemini_coordinate` | Coordinate multi-LLM task |
| `gemini_research` | Research with memory |
| `gemini_exec` | Execute Python code |

### Prompt Tools

| Tool | Description |
|------|-------------|
| `prompts` | List prompts |
| `prompt_set` | Create/update prompt |

### Remote Tools

| Tool | Description |
|------|-------------|
| `remote_hosts` | List remote hosts |
| `remote_task` | Submit remote task |
| `remote_status` | Remote task status |

## Usage Examples

### List Tools

```bash
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":"1"}'
```

### Call Tool

```bash
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "status",
      "arguments": {}
    },
    "id": "2"
  }'
```

### Chat with Model

```bash
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "chat",
      "arguments": {
        "message": "Hello!",
        "model": "gemini-2.0-flash"
      }
    },
    "id": "3"
  }'
```

### Search Codebase

```bash
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "code_search",
      "arguments": {
        "query": "async def",
        "path": "app"
      }
    },
    "id": "4"
  }'
```


### Search Episodic Agent History

```bash
curl -X POST "https://api.ailinux.me/v1/mcp" \
  -H "Authorization: Basic <credentials>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
      "name": "memory_history",
      "arguments": {"action": "search", "query": "parser timeout retry"}
    },
    "id": "memory-1"
  }'
```

Start with compact search and fetch timeline/full observations only for selected IDs. This progressive-disclosure pattern prevents historical sessions from flooding the model context.

## Error Handling

Errors are returned in JSON-RPC format:

```json
{
  "jsonrpc": "2.0",
  "error": {
    "code": -32602,
    "message": "Invalid params"
  },
  "id": "1"
}
```

| Code | Description |
|------|-------------|
| -32700 | Parse error |
| -32600 | Invalid request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

## SSE Streaming

For long-running operations, use SSE endpoint:

```bash
curl -N "https://api.ailinux.me/v1/mcp/sse" \
  -H "Authorization: Basic <credentials>"
```

## Semantic inventory discovery

TriForce keeps **authorization inventories** (`admin`, `filesystem`, `device`, …) separate from **semantic discovery profiles**. The semantic profiles are intentionally overlapping and exist only to reduce model context:

- `debug` — logs, MCP analytics, bounded status and failure evidence
- `code` — source search/read/edit and Git
- `files` — general file/directory operations
- `vision` — screen/browser observation and screenshots
- `system` — host/device/process/service/application/container operations
- `research` — current web/document research plus scoped memory recall
- `automation` — execution/integration primitives
- `communication` — mail, notifications, forum and publishing
- `collaboration` — agents, AI and group orchestration

Clients should start with the smallest relevant profile and expand only when needed. `x_inventory_groups` and `x_usage_hint` are discovery metadata; they never grant access or weaken per-tool RBAC, local approval, capability grants, or destructive-action policy.

### AI change discipline

For state-changing development work the common policy is: persistent `.workspacebackup` first, coherent architecture inspection, evidence-based root-cause analysis, smallest correct patch, focused regression tests plus relevant logs/reproducer, documentation update, then a reusable feature-memory summary. Version-sensitive framework/API/security assumptions are verified against current primary documentation. Existing authoritative documentation is updated in place; when no project change log exists, `docs/AI_CHANGELOG.md` is the fallback record.

## Session Docker compute sandbox

`compute_execute` may be granted by AILinux Helper as a **separate remote-compute capability**. It never turns the TriForce host into a shell target and does not expose Android Termux.

For the `triforce_docker` runtime TriForce creates an ephemeral, per-session Docker execution environment with these invariants:

- explicit Helper opt-in is required (`compute_execute` + compute `execute` grant),
- the container has public Internet egress but traffic to the TriForce host, RFC1918/private networks, link-local and other reserved destinations is rejected,
- Docker host networking, published ports, devices, privileged mode and `/var/run/docker.sock` are never exposed,
- all Linux capabilities are dropped, `no-new-privileges` is enabled, the root filesystem is read-only, and CPU/memory/PID limits apply,
- the only host bind mount is a bounded per-session mirror of the explicitly paired workspace,
- inside the container that share is available as `~/workspace`,
- read-only shares are mounted read-only; write shares are synchronized back only after creating `.workspacebackup/<run>-compute-sandbox/backup.md` and copies of changed/deleted files,
- the current mirror contract is UTF-8/source-oriented (1000 files, 2 MiB/file, 64 MiB total); unsupported/binary content is never silently rewritten.

The dedicated `triforce-sandbox-egress` network uses an isolated subnet and fail-closed host firewall rules. If TriForce cannot verify the expected subnet or install the isolation rules, compute execution is refused.

### Restart-safe workspace pairing

Web-first pair tickets are persisted in Redis **by SHA-256 hash only** for their short TTL. The raw pairing code is never persisted. This lets an Android/Helper executor reconnect after a TriForce restart or worker handoff without turning a still-valid code into `WORKSPACE_PAIR_FAILED`. Once a durable workspace lease exists, the ordinary lease/resume indexes replace the temporary pair-ticket record.
