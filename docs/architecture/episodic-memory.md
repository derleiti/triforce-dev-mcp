# TriForce Agent Memory Fabric

## Baseline and integration decision (2026-09-08)

Before edits: clean `master`, HEAD `315e7215cdc19c8011809a72cb64bdb06d7ddb32`.
Existing suite: 354 passed in 3.46 s. `triforce.service` and `ollama.service`
active; localhost:9100 was unreachable. No service restarted. Node 22.23.2,
npm 10.9.8; Bun absent from PATH. Runtime experiments live outside Git in /tmp.

Primary source: https://github.com/thedotmack/claude-mem . Latest release checked
via GitHub API: v13.24.1, published 2026-09-05. License: Apache-2.0.
Read the release source, not an old installation tutorial. Node >=20.12 and
Bun >=1.0 are declared; SQLite is bundled with Bun. Optional Chroma uses uv/Python.

Decision: C (provider abstraction) over B (official local Worker API).
The official MCP entry `npx claude-mem mcp` launches the shipped stdio server;
its search/timeline/get tools delegate to the same HTTP worker. HTTP additionally
supports JSON search results and direct manual observations with metadata, without
an observer model. No Claude-Mem fork or direct SQLite writes from TriForce.

Verified interfaces at the pinned release:
- GET /api/health
- GET /api/search?format=json&type=observations (compact projection in adapter)
- GET /api/timeline?anchor=ID&project=PROJECT&depth_before=N&depth_after=N
- POST /api/observations/batch {ids, project}
- POST /api/memory/save {text, title, project, metadata}

No fabricated upstream session-summary operation: manual records are grouped in
upstream manual sessions; TriForce run/session identity lives in metadata.
Recent work is search without a query ordered by date. Summary is an explicit
TriForce event, never represented as an upstream-generated summary.

Upstream documents `npx claude-mem install --ide antigravity`; this installs IDE
hooks and is unnecessary for this backend integration. A global npm install alone
is not a complete installation. Supported integrations include Claude Code,
Codex, Gemini, OpenClaw, OpenCode and Antigravity; TriForce remains model-neutral.
Current upstream main has branding changes; pin release artifacts, not moving main.

## Layer ownership

Runtime events -> MemoryTriggerEngine -> EpisodicMemoryProvider -> ClaudeMemAdapter
-> local worker -> SQLite observations -> bounded, untrusted historical context.

Episodic memory answers what happened. Curated TriForce memory answers what we know.
Runtime state answers what is happening now. Recall cannot write curated memory.
Explicit promotion requires verified evidence and operator-enabled policy.
Current code/tests/runtime take precedence over all historical observations.

## Privacy decision before installation

Use an isolated worker/data directory, loopback only, restrictive permissions.
Disable telemetry (on by default upstream), cloud sync and Chroma. Use only direct
manual save and retrieval; no session observation pipeline, external observer,
subscription, API credentials, account login or provider activation. Existing
Ollama Gemma defaults remain unchanged. Local host-model observation is possible
upstream but not needed here. Default Claude/Gemini/OpenRouter/host integrations
can transmit captured content depending on configuration and host routing; they
require a separate explicit deployment decision.

## Runtime triggers and write policy

Automatic recall is centralized in `MemoryTriggerEngine`. Current runtime wiring covers:
- task start / run resume
- first relevant file read or edit per run (deduplicated)
- tool/provider/test-style failures through normalized error queries
- retry, handoff, merge and commit events through the common event API

Automatic writes are a separate capability and default to OFF. With
`TRIFORCE_MEMORY_RECORD_ENABLED=true`, remote AICoder failures and completed runs may
be recorded as episodic observations. A completed run is stored as `completed`, never
as `verified`; successful verification must be explicit evidence from tests/runtime.
Writes are bounded, redacted and deduplicated before the Claude-Mem worker is called.

Promotion into curated TriForce memory is also OFF by default and requires
`TRIFORCE_MEMORY_PROMOTION_ENABLED=true`. Promotion is explicit/operator-only and
requires the source observation itself to carry `verification_state=verified` plus
verification evidence. Failed, attempted, changed or merely completed observations
are rejected. Source observation ID, run and commit provenance are retained as tags.

## Configuration

- `TRIFORCE_EPISODIC_MEMORY_ENABLED` (default false)
- `TRIFORCE_EPISODIC_MEMORY_PROVIDER` (default `claude-mem`)
- `TRIFORCE_EPISODIC_MEMORY_DATA_DIR` (Claude-Mem settings directory)
- `TRIFORCE_MEMORY_AUTO_RECALL` (default true when episodic memory is enabled)
- `TRIFORCE_MEMORY_MAX_RESULTS` (1-5, default 4)
- `TRIFORCE_MEMORY_TOKEN_BUDGET` (default 1200 bytes/conservative context budget)
- `TRIFORCE_MEMORY_TIMEOUT` (default 0.8 s, max 5 s)
- `TRIFORCE_MEMORY_TRIGGER_FILE`
- `TRIFORCE_MEMORY_TRIGGER_FAILURE`
- `TRIFORCE_MEMORY_TRIGGER_RETRY`
- `TRIFORCE_MEMORY_RECORD_ENABLED` (default false)
- `TRIFORCE_MEMORY_PROMOTION_ENABLED` (default false)
- `TRIFORCE_MEMORY_PROJECT_ID`
- `TRIFORCE_MEMORY_STALE_DAYS` (default 90)

The worker port is never guessed. It is discovered from the configured Claude-Mem
`settings.json`, and non-loopback hosts are rejected.

## Failure behaviour and observability

Claude-Mem is optional and fail-open. Timeouts, malformed responses, an unavailable
worker, an unsupported provider or a locked/broken backing store degrade only episodic
memory. TriForce continues without historical context. Three consecutive failures open
a short circuit breaker. Health exposes provider/connection state, last recall/error
and aggregate recall metrics, but no memory contents.

Structured log event names include `memory_trigger`, `memory_query` (hash only),
`memory_hits`, `memory_injected`, `memory_skipped`, `memory_timeout`,
`memory_degraded`, `memory_recorded` and `memory_promoted`. Normal logs do not emit
full historical content or credentials.

## Security and privacy

All worker traffic is loopback-only, proxy environment variables are ignored, HTTP
redirects are disabled and responses are size-bounded. Recall and record paths redact
common API keys, bearer tokens, authorization/cookie headers, passwords, private keys,
session/client secrets and credential-bearing URLs. Project scope is checked again on
returned rows. Remote AICoder project identity is derived from authenticated user plus
workspace so separate users/workspaces cannot intentionally select each other's scope.

The recommended deployment does not need Claude-Mem IDE hooks, cloud sync, Chroma,
account login, an observer LLM or a paid provider. Enabling an upstream observer later
is a separate privacy decision because captured prompts/code/tool output may be sent to
that observer's provider.

## Troubleshooting / disable / uninstall

If health reports `disabled`, enable episodic memory only after the worker and data
policy are configured. `degraded` with `MemoryUnavailable`, timeout or malformed data
means TriForce deliberately continued without recall. Check the Claude-Mem worker,
its `settings.json`, local permissions and SQLite health; do not hardcode 37777.

If searches return nothing, verify the exact project scope and remember that old or
commit-mismatched observations are intentionally down-ranked. If too much context is
injected, lower result count/token budget; full observations require explicit detail
retrieval. Provider/database failures must be fixed in Claude-Mem rather than bypassing
the adapter with direct SQLite writes.

To disable the integration, set `TRIFORCE_EPISODIC_MEMORY_ENABLED=false`. TriForce and
curated memory remain functional. Removing the isolated Claude-Mem runtime/data is a
separate operator action; preserve/export data first if retention is required.

## Verification status

Unit/integration coverage includes task recall, empty results, result/token limits,
file deduplication, failure/retry normalization, handoff/resume/merge/commit context,
malformed worker responses, no guessed port, secret filtering, controlled recording,
record deduplication, unverified-promotion rejection and evidence-required promotion.
The remote coding suite verifies that the added memory hooks do not break the existing
AICoder execution contract.

## End-to-end verification (2026-09-09)

The pinned Claude-Mem 13.24.1 worker was started from an isolated `/tmp` runtime on
Linux with its correct Bun binary restored from the official npm package artifact.
The worker reported `status=ok`, `initialized=true` and `mcpReady=true`. A synthetic
`triforce-e2e` observation was stored through `POST /api/memory/save` and then found
again through the JSON search endpoint. The worker also reported its optional Claude
CLI observer dependency as setup-required; this is expected because the TriForce
integration intentionally uses direct local worker retrieval/manual save and does not
require or enable the observer pipeline. No production memory data was used.
