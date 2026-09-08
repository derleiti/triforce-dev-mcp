# OpenClaw MCP Node Integration


<!-- AILINUX_STATUS_START -->
## Current OpenClaw / MCP node baseline

- Gateway target: `ws://127.0.0.1:18789`.
- User services: `ailinux-mcp-node.service` and `openclaw-gateway.service`.
- Primary/default model route: `ollama/gemma4:12b`.
- Validate with `openclaw doctor` or `openclaw`.
- `openclaw primary: ...` is status text, not a shell command.
- If OpenClaw reports stale or invalid plugin configuration, prefer `openclaw doctor --fix`, then re-check with `openclaw doctor`.
<!-- AILINUX_STATUS_END -->

This document describes the OpenClaw MCP Node bridge for TriForce.

## Architecture

The OpenClaw MCP Node connects a local client machine to the TriForce server.

Flow:

1. The client logs in through `/v1/auth/login`.
2. The client receives a JWT bearer token.
3. The local node connects to `/v1/mcp/node/connect` via WebSocket.
4. TriForce registers the node under a `client_id`, for example `openclaw`.
5. Tool calls are sent through `/v1/mcp/node/call`.
6. The server forwards calls to the connected WebSocket node.
7. The node executes local tools and returns the result.

## Endpoints

### Login

    POST /v1/auth/login

### Node WebSocket

    WSS /v1/mcp/node/connect

### Proxy Tool Call

    POST /v1/mcp/node/call

Example request:

    {
      "client_id": "openclaw",
      "tool": "client_file_list",
      "params": {
        "path": "/home/zombie/triforce"
      }
    }

For compatibility, `params` and `arguments` are both accepted.

### Authentication and connection lifecycle

`/v1/mcp/node/clients`, `/call`, and `/chat-with-files` require a valid
`Authorization: Bearer <JWT>` with a subject or client identity. Discovery lists
only the caller's clients and collapses session aliases. Calls against a client
owned by another account return 404 before any remote execution. Telemetry-only
connections cannot execute tools.

WebSocket ownership comes from the verified token, and tier comes from the
server's tier service. The legacy `user_id` and `tier` query parameters remain
accepted but cannot grant identity or privileges. Anonymous telemetry receives
an isolated random client ID and cannot claim a supplied session alias. An
authenticated alias collision with another account closes with code 4003.

Tool timeouts cover both sending and receiving. Disconnects fail pending calls
immediately, and cleanup removes only aliases still referring to the disconnected
socket. Client tool errors are returned with `success: false`.

The legacy `/chat-with-files` path and request parameters remain reserved, but
the route returns HTTP 501 after authentication and ownership checks: its tool
execution loop was never implemented. It no longer sends a model prompt claiming
filesystem access or reports tools as available. Compatible AICoder clients can
use `/v1/remote-coding/run` for the implemented remote coding workflow.

### Antigravity remote coding reliability

`/v1/remote-coding/run` defaults to `ollama/gemma4:12b`; an explicit request model
or `TRIFORCE_ANTIGRAVITY_MODEL` remains an override. Nodes must advertise at least
one compatible coding tool. Only one coding run may use a physical connection
at a time, including through its aliases. The guard is released on cancellation
or failure; it is not a durable idempotency key across reconnects or restarts.

Worker RPC exchanges are serialized. Malformed protocol messages and missing
tool results fail explicitly. Completion requires a nonempty final response,
successful worker exit, and a successful run-state acknowledgement when the
client advertises that capability. Timeout, startup-send failure, and cancellation
close and reap the local worker. Diagnostic output is drained with bounded memory.

After an edit attempt, the worker requires a read of each affected path before
completion. Reading another file or running `git status` does not satisfy this
check. This establishes post-edit evidence for the agent; it does not prove the
semantic correctness of arbitrary generated changes. A disconnected client's
already-dispatched operation may still have run: inspect its state before retrying.

Offline regression checks:

    .venv/bin/pytest tests/test_remote_coding_agent.py tests/test_security_findings.py

## Supported Local Tools

- `tools_index`
- `client_ping`
- `client_info`
- `client_file_list`
- `client_file_read`
- `client_shell_exec`
- `client_git_status`

## systemd User Service

Example service path:

    ~/.config/systemd/user/ailinux-mcp-node.service

Useful commands:

    systemctl --user daemon-reload
    systemctl --user enable --now ailinux-mcp-node.service
    systemctl --user status ailinux-mcp-node.service --no-pager
    journalctl --user -u ailinux-mcp-node.service -n 50 --no-pager

## Test Commands

List tools:

    python3 ~/.openclaw/skills/ailinux-mcp-client/bin/ailinux_mcp.py tools

List a directory:

    python3 ~/.openclaw/skills/ailinux-mcp-client/bin/ailinux_mcp.py call client_file_list '{"path":"/home/zombie/triforce"}'

Run a shell command:

    python3 ~/.openclaw/skills/ailinux-mcp-client/bin/ailinux_mcp.py call client_shell_exec '{"command":"pwd && ls -la | head -40","cwd":"/home/zombie/triforce"}'

## Notes

The proxy previously only accepted `params`. Some clients sent `arguments`, causing empty argument forwarding.

The proxy now supports both forms:

    request.params or request.arguments or {}
