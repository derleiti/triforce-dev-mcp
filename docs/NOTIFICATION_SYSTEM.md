# Nova Notification Manager v2.0

**Last Updated:** 2026-11-24
**File:** `app/mcp/notification_manager.py`
**Status:** Existing manager deployed on all 3 nodes; leader lifecycle changes are pending rollout.

## Architecture

```
Sources (polled every 1-10 min):
  Mail Poller → IMAP nova@ailinux.me (1min)
  Forum Poller → Flarum API (5min)
  WordPress Poller → WP REST API comments (10min)
  System → system_log_collector bridge (realtime)
  Manual → notify_send MCP tool

  ↓ create_event()

Normalize + Classify:
  Keyword matching → 20 Event Types
  Auto-priority from dispatch rules

  ↓ _fingerprint() + Redis dedup

Deduplicate:
  SHA1(source + event_type + normalized_content)
  Timestamps/PIDs/session-IDs stripped before hash
  Error window: 1h | Content window: 24h
  5x same error → promoted to ops.repeated_error

  ↓ _dispatch_event()

Filter:
  Skip: internal tags (agent-spawn, scheduler, auto, log-monitor, etc.)
  Skip: priority < HIGH (NORMAL/LOW are passive)
  Cooldown: 2min per event_type
  Rate-gate: 5/min (claude/codex), 3/min (gemini)

  ↓ spawn_for_issue(timeout_seconds=300)

Agent Spawn:
  System-Prompt → WORKER_AGENT_PROMPTS[issue_type]
  Task-Prompt → TASK_PROMPTS[event_type] (tool-specific steps)
  Investigation-Prompt → _build_investigation_prompt()
  Auto-shutdown: 5min inactivity timeout
  TASK_COMPLETE signal → session marked as done

  ↓ cleanup_loop (every 10min)

Cleanup:
  Expired + task_done sessions removed automatically
```

## Event Types

| Event Type | Agent | Priority | Trigger |
|---|---|---|---|
| `ops.error` | codex-mcp | high | Python exceptions, tracebacks |
| `ops.repeated_error` | gemini-mcp | high | Same error 5+ times in 1h |
| `ops.service_down` | codex-mcp | critical | Service crashed, OOM, segfault |
| `ops.performance` | codex-mcp | normal | Performance degradation |
| `support.general` | claude-mcp | normal | General support requests |
| `support.install` | claude-mcp | normal | Installation help |
| `support.login` | claude-mcp | high | Login/account problems |
| `support.bug_report` | codex-mcp | high | Bug reports |
| `support.feature_req` | gemini-mcp | low | Feature requests |
| `forum.question` | claude-mcp | normal | New forum question |
| `forum.support` | claude-mcp | normal | Forum support request |
| `forum.feedback` | — | low | Feedback (passive) |
| `forum.spam` | — | low | Spam detected (2+ keywords) |
| `mail.support` | claude-mcp | normal | Support email |
| `mail.research` | codex-mcp | normal | Research email ([RESEARCH] tag) |
| `mail.spam` | — | low | Spam email |
| `wp.comment` | claude-mcp | low | WordPress comment |
| `wp.update` | — | low | WordPress content update |
| `incident.auth` | codex-mcp | critical | Auth security incident |
| `incident.service` | gemini-mcp | critical | Multi-service incident |

## Task-Specific Prompts

Each event type has a dedicated task prompt with:
- **Exact tool list** the agent should use
- **Step-by-step workflow** for that specific task
- **Security boundaries** (no password resets, no restarts without approval)
- **Metadata injection** (mail uid, forum discussion_id, author)
- **Completion signal** (notify_read + TASK_COMPLETE)

Covered: `mail.support`, `mail.research`, `forum.question`, `forum.support`,
`ops.error`, `ops.repeated_error`, `ops.service_down`, `support.login`,
`support.bug_report`, `support.install`, `incident.auth`, `incident.service`,
`wp.comment`

## MCP Tools

| Tool | Description |
|---|---|
| `notify_list` | List notifications (filter: unread_only, source, priority, event_type, limit) |
| `notify_read` | Mark notification as read or resolved (id, resolve=true) |
| `notify_clear` | Delete resolved notifications (all=true for everything) |
| `notify_send` | Create manual notification (title, body, source, priority, event_type, tags) |
| `notify_status` | Manager stats, poller health, dedup windows, dispatch rule count |

## Poller leadership and Redis outages

Automatic pollers use the configured `REDIS_URL` and a single supervisor task.
They wait for Redis and a 120-second leader lease before launching, renew the
lease atomically every 60 seconds and stop their tasks when renewal fails.
They retry after 15 seconds. On normal shutdown local pollers are cancelled
before the owner-checked lease release. `notify_status.poller_leader` reports
the leader state. Manual notifications retain their file fallback.

The lease coordinates processes sharing the **same Redis database**. Separate
Redis databases on different federation nodes do not coordinate external source
polling; use a shared appropriate Redis database or run external pollers only
on a designated node. For separate Redis installations, set
`TRIFORCE_NOTIFICATION_EXTERNAL_POLLERS_ENABLED=false` on worker nodes.
This disables the mail, forum and WordPress pollers while preserving the
node-local AICoder digest when its Redis lease is available. The setting
defaults to true for backwards compatibility; new federation node templates
default to false. Before promoting a standby, persistently set
`TRIFORCE_NOTIFICATION_EXTERNAL_POLLERS_ENABLED=false` on the old primary,
restart or fence it, and verify that it cannot resume external polling. Only
then enable and restart the standby. If the old primary cannot be reached,
fence it before promotion. This is not automatic failover or exactly-once
external delivery.

Source pollers persist an event with a stable source key before recording its
Seen marker. IMAP polling reads without setting the Seen flag, and acknowledges
only after persistence. The local bounded notification store suppresses repeat
creation after cancellation between persistence and acknowledgement. This is
not a cross-node durable outbox: process crashes during external dispatch,
manual clearing/retention, or promotion to a different store can still affect
delivery; do not claim exactly-once replies.

## Dedup Behavior

| Occurrence | Action |
|---|---|
| 1st | Event created + stored + dispatched (if HIGH/CRITICAL) |
| 2nd-4th | Silently deduplicated (Redis counter incremented) |
| 5th+ | Promoted to `ops.repeated_error` → dispatched to gemini-mcp |

Fingerprint normalization strips:
- Timestamps (`2026-03-21T05:28:03+01:00`)
- PIDs (`[581770]`)
- Session IDs (`spawn-abc12345`)
- Notification IDs

## Agent Lifecycle

```
1. Event arrives (poller or manual)
2. Classified → event_type determined
3. Dedup check → skip if duplicate
4. Dispatch check → skip if LOW/NORMAL, cooldown, or rate-limited
5. spawn_for_issue(issue_type, context, timeout_seconds=300)
6. Agent receives: System-Prompt + Task-Prompt + Investigation-Prompt
7. Agent works (max 5 min inactivity)
8. Agent calls notify_read(id, resolve=true) + TASK_COMPLETE
9. Cleanup loop removes expired/done sessions every 10 min
```

## Configuration

```python
MAIL_POLL_INTERVAL = 60        # 1 min
FORUM_POLL_INTERVAL = 300      # 5 min
WP_POLL_INTERVAL = 600         # 10 min
DEDUP_WINDOW_ERROR = 3600      # 1h for system errors
DEDUP_WINDOW_CONTENT = 86400   # 24h for content (mail/forum/wp)
DISPATCH_AGENT_TIMEOUT = 300   # 5 min agent auto-shutdown
DISPATCH_COOLDOWN_S = 120      # 2 min between same event_type dispatches
MAX_ENTRIES = 1000              # Max notifications in store
```

Storage: `/var/lib/triforce/notifications.json` (JSON file)
Dedup index: Redis `notify:dedup:{fingerprint}` keys with TTL

## Source Files

| File | Purpose |
|---|---|
| `app/mcp/notification_manager.py` | Core: pollers, classify, dedup, dispatch, CRUD, MCP tools |
| `app/services/agent_spawner.py` | Agent spawn with per-session timeout, cleanup loop |
| `app/services/task_scheduler.py` | Scheduled tasks (mail/forum handlers disabled, v2 pollers handle it) |
| `app/utils/system_log_collector.py` | LOG→NOTIFY bridge for journald errors |
| `app/main.py` | Startup: `start_pollers()` / Shutdown: `stop_pollers()` |

## Backward Compatibility

`create_notification(dict_or_title, **kwargs)` is a sync wrapper that:
- Accepts the old dict format (`create_notification({"title": "...", "body": "..."})`)
- Fires async `create_event()` in the background
- Returns `{"title": ..., "status": "queued"}`

All existing callers (task_scheduler, agent_spawner, nova_content_engine, system_log_collector) continue to work without changes.
