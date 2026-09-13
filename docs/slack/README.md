# AILinux Slack baseline

Slack is a communication surface, not the source of truth.

## Source-of-truth policy

- GitHub: code, issues, pull requests, releases, durable decisions.
- TriForce: machine-readable runtime state, MCP/agent context, notifications and automation.
- Slack: discussion, alerts, handoff, and short-lived coordination.

Important Slack decisions must be copied to GitHub or TriForce before they age out of the free-plan history window.

## Free-plan operating rules

1. Keep the app count deliberately small. Target: GitHub, ChatGPT, and the AILinux/TriForce app.
2. Do not create a channel for every feature. Use threads.
3. Bots post only actionable or durable information: failed releases/deployments, successful production deployments, service incidents/security events, published releases, and agent results that require human action.
4. Routine passing tests and verbose logs stay in GitHub Actions or TriForce logs.
5. Every bug gets one root message and one thread.
6. Durable decisions use `[DECISION]`, tasks use `[ACTION]`, blockers use `[BLOCKER]`.
7. A weekly digest captures decisions, owners, blockers, completed work and next steps into a durable system.

## Message conventions

### Decision

```text
[DECISION] Canonical MCP tool pool shared by local and public MCP
Owner: Markus
Date: YYYY-MM-DD
Context: <one sentence>
Result: <one sentence>
Canonical record: <GitHub issue/PR/doc>
```

### Action

```text
[ACTION] <task>
Owner: <name>
Due: <date or none>
Canonical record: <GitHub issue/PR/doc>
```

### Blocker

```text
[BLOCKER] <short description>
Owner: <name>
Impact: <what is blocked>
Needs: <decision/input/fix>
Canonical record: <GitHub issue/PR/doc>
```

### Bug thread

```text
[BUG][P1] Workspace reconnect loses session
Component: TriForce
Owner: Markus
Status: investigating
Canonical issue: <url>
```

When resolved, reply in the same thread:

```text
[RESOLVED]
Cause: <root cause>
Fix: <summary>
Commit: <sha>
Tests: <result>
```

## Channel model

The desired channels and descriptions are versioned in `config/slack/channels.yaml`.

Core channels:

- `#announcements`
- `#general`
- `#dev`
- `#triforce`
- `#aicoder`
- `#loom`
- `#helper`
- `#mcp`
- `#bugs`
- `#releases`
- `#ops`
- `#random`

## Slack app manifest

`config/slack/manifest.yaml` contains the minimal initial bot scopes required for outbound notifications and channel discovery.

The initial app intentionally does not request message-history scopes. Add history scopes only when a concrete, reviewed feature needs to read Slack messages. This keeps the default integration least-privileged.

Secrets must never be committed. Expected runtime variables, when Slack delivery is enabled:

- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET` only if inbound Slack events are enabled later

## Future integration path

TriForce Notification System remains the single event source. Add a Slack transport/provider there instead of creating a parallel notification subsystem. Route events by severity/type to `#releases`, `#ops`, `#bugs`, or a project channel.
