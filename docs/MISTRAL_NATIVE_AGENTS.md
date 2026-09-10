# Native Mistral Agents integration

TriForce can call a version-pinned Mistral Studio Agent through Mistral's native
Agents + Conversations REST API. This is separate from normal stateless Mistral
chat completions and from the legacy local `mistral-mcp` process.

## Configuration

```env
MISTRAL_API_KEY=...
MISTRAL_AGENT_ID=ag:75b2b27f:20251006:untitled-agent:8f02c404
MISTRAL_AGENT_VERSION=5
MISTRAL_AGENT_BASE_URL=https://api.mistral.ai
MISTRAL_AGENT_TIMEOUT_SECONDS=180
MISTRAL_AGENT_STORE=true
```

`MISTRAL_AGENT_VERSION` is deliberately pinned so a Studio edit cannot silently
change production behavior. `NOVA_MISTRAL_AGENT_ID` remains accepted as a legacy
alias for the agent ID.

## TriForce API

- `GET /v1/mistral/agents/status`
- `GET /v1/mistral/agents/configured`
- `POST /v1/mistral/agents/conversations`
- `POST /v1/mistral/agents/conversations/{conversation_id}`
- `GET /v1/mistral/agents/conversations/{conversation_id}`
- `GET /v1/mistral/agents/conversations/{conversation_id}/history`
- `GET /v1/mistral/agents/conversations/{conversation_id}/messages`
- `DELETE /v1/mistral/agents/conversations/{conversation_id}`
- `POST /v1/mistral/agents/conversations/{conversation_id}/restart`
- `POST /v1/mistral/agents/review`

Nova's `provider=mistral` route automatically uses this native agent when both
`MISTRAL_API_KEY` and an agent ID are configured. Otherwise the existing normal
Mistral model route remains the fallback.

## Reviewer mode

`POST /v1/mistral/agents/review` accepts the original task plus optional handoff,
git diff and test output. It returns the Mistral conversation response and a
normalized `verdict` value: `PASS`, `FIX_REQUIRED`, or `UNKNOWN`.

For a multi-stage coding run, keep the returned `conversation_id` and send it on
subsequent review calls. This lets the reviewer preserve task context across
stages without repeatedly rebuilding the whole transcript.
