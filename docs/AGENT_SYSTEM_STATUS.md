# Agent System Status

**Last Updated:** 2026-03-21

## Overview

| Component | Status | Description |
|-----------|--------|-------------|
| REST API | ✅ Working | `/v1/agents/cli/*` endpoints |
| MCP Tools | ✅ Working | `agents`, `agent_start`, `agent_stop`, `agent_call`, `agent_broadcast` |
| Agent Controller | ✅ Working | `app/services/tristar/agent_controller.py` |
| Agent Spawner | ✅ Working | `app/services/agent_spawner.py` (Tier 2 workers) |
| Notification Manager v2 | ✅ Working | `app/mcp/notification_manager.py` (event-driven orchestrator) |
| Unified Logging | ✅ Active | `logs/unified.log` + stdout |
| Wrapper Scripts | ✅ Created | `/home/zombie/triforce/triforce/bin/` |

## Tier 1 — Core Agents (Permanent)

| Agent ID | Type | Role |
|----------|------|------|
| `claude-mcp` | Claude Code | Ops-Koordination, Support-Triage, Mail/Forum-Verarbeitung |
| `gemini-mcp` | Google Gemini | Lead-Koordination, Mustererkennung, Swarm-Steuerung |
| `codex-mcp` | OpenAI Codex | Code-Analyse, Research, technische Proposals |
| `opencode-mcp` | OpenCode | Workflow-Härtung, CLI/Wrapper-Probleme |

**Note:** `_init_core_agents` is currently disabled in main.py. Core agents are started on-demand by the notification manager dispatch or manual `agent_start`.

## Tier 2 — Worker Agents (Spawned on Demand)

| Worker Type | Used For | Spawned By |
|-------------|----------|------------|
| `bug_fixer` / `bug_hunter` | Python tracebacks, code bugs | Notifier: ops.error, support.bug_report |
| `ops_worker` / `ops_handler` | System issues, service crashes | Notifier: ops.service_down, incidents |
| `support_agent` | User support, forum replies | Notifier: mail.support, forum.question |
| `marketing_agent` | WP posts, community content | Task scheduler |
| `research_agent` | Codebase analysis, findings | Notifier: mail.research |
| `content_agent` | Automated blog/forum posts | Task scheduler |
| `implementation_agent` | Code patches (research-approved) | Manual or research pipeline |
| `swarm_coordinator` | Multi-agent coordination | Load-based or manual |

## Notification-Driven Dispatch

The Notification Manager v2 automatically dispatches agents based on event type:

```
New mail arrives → mail poller (5min) → classify → support.login (HIGH)
  → dispatch → claude-mcp/support_agent
  → System-Prompt + Task-Prompt with mail uid + workflow steps
  → Agent works (5min timeout) → notify_read(resolve=true) → TASK_COMPLETE
  → cleanup loop removes session
```

See `docs/NOTIFICATION_SYSTEM.md` for full architecture.

### Event-to-Agent Mapping

| Event | Agent | Worker |
|-------|-------|--------|
| `ops.error` | codex-mcp | bug_hunter |
| `ops.repeated_error` | gemini-mcp | bug_hunter |
| `ops.service_down` | codex-mcp | ops_handler |
| `support.login` | claude-mcp | support_agent |
| `support.bug_report` | codex-mcp | bug_hunter |
| `forum.question` | claude-mcp | support_agent |
| `mail.support` | claude-mcp | support_agent |
| `mail.research` | codex-mcp | research_agent |
| `incident.auth` | codex-mcp | ops_handler |
| `incident.service` | gemini-mcp | ops_handler |

## Agent Session Lifecycle

1. **Spawn:** `spawn_for_issue(issue_type, context, timeout_seconds=300)`
2. **Init:** System-Prompt (role + permissions) sent to agent
3. **Task:** Task-Prompt (specific tools + steps) + Investigation-Prompt sent
4. **Working:** Agent executes tools autonomously
5. **Complete:** Agent calls `notify_read(resolve=true)` + responds `TASK_COMPLETE`
6. **Expire:** 5min inactivity → session marked expired
7. **Cleanup:** Every 10min, expired/done sessions removed

Default timeout: 30min (scheduler tasks), 5min (notifier tasks).

## Persistent Agent Memory

Agents can now receive persistent episodic context through the native TriForce Memory Fabric. `MemoryTriggerEngine` centralizes recall for task start/resume, file context, failures, retries and handoffs; the current provider is Claude-Mem 13.24.1 behind `EpisodicMemoryProvider`. Recall is model-neutral, bounded, redacted and fail-open. Curated TriForce memory remains a separate verified-knowledge layer, and promotion from episodic history is explicit and evidence-gated.

See [`architecture/episodic-memory.md`](architecture/episodic-memory.md).

## MCP Tools

### Agent Management
| Tool | Description |
|------|-------------|
| `agents` | List all CLI agents with status |
| `agent_call` | Send message to specific agent |
| `agent_start` | Start/restart a CLI agent |
| `agent_stop` | Stop a CLI agent |
| `agent_broadcast` | Message to all agents |
| `agent_spawn_worker` | Spawn Tier 2 worker |
| `agent_spawn_status` | List active spawned sessions |
| `agent_session_list` | Detailed session list |
| `agent_session_send` | Send message to specific session |

### Notification Management
| Tool | Description |
|------|-------------|
| `notify_list` | List notifications (filterable) |
| `notify_read` | Mark notification as read/resolved |
| `notify_clear` | Delete resolved notifications |
| `notify_send` | Create manual notification |
| `notify_status` | Manager stats + poller health |

## REST API Endpoints

```
GET  /v1/agents/cli                    - List all agents
GET  /v1/agents/cli/{agent_id}         - Agent details
POST /v1/agents/cli/{agent_id}/start   - Start agent
POST /v1/agents/cli/{agent_id}/stop    - Stop agent
POST /v1/agents/cli/{agent_id}/call    - Send message to agent
```

## Files

| File | Purpose |
|------|---------|
| `app/services/agent_spawner.py` | Tier 2 spawner, session management, cleanup |
| `app/services/tristar/agent_controller.py` | Tier 1 agent controller |
| `app/mcp/notification_manager.py` | Event-driven orchestrator (v2) |
| `app/mcp/handlers_scheduler.py` | MCP tool handlers for spawn/session tools |
| `app/services/task_scheduler.py` | Scheduled tasks (mail/forum handlers disabled since v2) |
| `config/agents/` | Agent configuration files |
