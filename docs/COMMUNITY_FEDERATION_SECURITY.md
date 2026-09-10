# Community federation security model

TriForce separates **trusted server federation** from **untrusted community compute**.
The trusted federation (`server_federation.py`) is reserved for operator-controlled
nodes such as Hetzner, backup and workstation peers. Community devices connect only
through the distributed-compute worker endpoint.

## Security invariants

Community compute is disabled by default. When enabled:

1. A worker must present a valid AILinux JWT in its first WebSocket registration
   message. Tokens are not accepted in the URL/query string.
2. Only configured account tiers may contribute. Default: `registered,pro,enterprise`.
3. Session IDs are issued by the server. A worker cannot choose or replace another
   worker session.
4. Capabilities, model identifiers, message sizes, task sizes, progress and timing
   fields are validated and bounded.
5. A task is **never** sent to a community worker unless its creator explicitly set
   `allow_community=true`.
6. Community-task status, result and cancellation are bound to the authenticated task
   owner.
7. Community results are marked `result_verified=false` and
   `result_source_trust=community`. Credits stay pending until a future independent
   verification step confirms the result.
8. Mistral is advisory only. It can optionally inspect sanitized registration metadata
   for anomalies, but it cannot authenticate, authorize, assign permissions or override
   deterministic policy.
9. The Mistral metadata audit receives no bearer token, IP address, email, task prompt,
   task input, task result or file contents. The worker identity is pseudonymized.

## Configuration

```env
FEDERATION_COMMUNITY_ENABLED=false
FEDERATION_COMMUNITY_ALLOWED_TIERS=registered,pro,enterprise
FEDERATION_COMMUNITY_MAX_MESSAGE_BYTES=262144
FEDERATION_COMMUNITY_MAX_TASK_BYTES=524288
FEDERATION_COMMUNITY_MAX_MODELS=32
FEDERATION_COMMUNITY_MISTRAL_AUDIT_ENABLED=false
```

Keep both community compute and the Mistral metadata audit disabled until the client
protocol and result-verification strategy have been tested end-to-end.

## Worker registration

Connect to `/v1/distributed/worker` and send the first message as:

```json
{
  "type": "register",
  "authorization": "Bearer <AILinux JWT>",
  "supported_models": ["embedding_small", "sentiment"]
}
```

The server responds with a server-generated session ID and explicit trust metadata.

## Submitting an opt-in community task

`POST /v1/distributed/submit` accepts `allow_community`. If it is true, the request
must include the same AILinux bearer authentication and the task type must be on the
community-safe allowlist.

Community compute must not be used for secrets, private source trees, credentials,
private conversations or other data that the worker is not allowed to read. The
`allow_community` flag is a data-disclosure decision as well as a scheduling decision.

## Mistral's role

The native Mistral Agent integration is suitable for:

- anomaly review of sanitized worker metadata,
- later review of aggregate reliability statistics,
- policy suggestions and suspicious-pattern triage.

It is deliberately **not** used for authentication or authorization. Security gates
must remain deterministic and auditable even if the model is unavailable or wrong.
