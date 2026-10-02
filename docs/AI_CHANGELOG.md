# AI Change Log

## 2026-09-14 — Remote Docker compute sandbox + restart-safe pairing

- Scope: TriForce workspace pairing/compute bridge and AILinux Helper Android compute advertisement.
- Reason: allow an explicitly requested Internet-enabled Docker shell without exposing the TriForce host, while making an already paired share available at `~/workspace`.
- Security: dedicated egress network, host/private-network rejection, no Docker socket/host network/privileged mode, dropped capabilities, no-new-privileges, resource limits, per-session workspace mirror.
- Recovery: workspace write-back creates `.workspacebackup/<run>-compute-sandbox/backup.md`; source recovery snapshot is `.workspacebackup/20260914-045000-remote-compute-sandbox/`.
- Pairing: pre-lease pair tickets now survive backend restarts through hash-only Redis metadata; raw pairing codes are not persisted.
- Verification: focused pytest coverage, Helper source-contract tests, Android JDK17/SDK35 build, Docker public-Internet/host-isolation smoke test, then full TriForce regression suite.

## 2026-09-14 — MCP workspace reconnect + Helper 2.90.29 hardening

- Scope: `/v1/mcp` server/web Helper, workspace pairing/resume, MCP agent instructions, and AILinux Helper Android lifecycle.
- Root causes: reconnect-by-pair-code did not return the persisted resume credential; Android could keep a transport-looking socket while no inbound frames arrived; capability changes from Accessibility readiness were not re-advertised until a later reconnect.
- Server fixes: reconnect responses now return the existing resume credential; regression coverage verifies it; `HTTPException` is imported for Helper download routes; MCP Python modules no longer carry accidental executable mode bits.
- MCP client guidance: initialize instructions now include a compact workspace operating guide covering status/transport/executor distinction, short-lived pair codes vs durable resume credentials, Android Accessibility capability gating, semantic observe/invoke/observe control, and secret handling.
- Web Helper: browser/PWA executor/cache assets advanced to 2.90.29 with matching regression expectations.
- Verification: 101 focused TriForce MCP/workspace tests passed; Python compile and `git diff --check` passed; code audit reports no error/critical bug/security findings in the audited MCP routes after fixes.
- Recovery: `/home/zombie/workspace/.workspacebackup/retry-20260914-183651/`.

## 2026-09-16 — MCP functional audit fixes

- Fixed WordPress permanent-delete success detection for the WordPress REST `deleted/previous` response shape.
- Added enum-to-capability fallback for `specialist` so simple `math`, `code`, `debug`, `vision`, `research`, `analysis`, and `creative` requests do not fail before provider invocation.
- Hardened upstream HTTP error extraction for unread HTTPX streaming responses; preserves status/reason instead of raising `ResponseNotRead`.
- Changed the v4 MCP chat default from retired `gemini-2.0-flash` to configurable `TRIFORCE_DEFAULT_CHAT_MODEL`, defaulting to the currently working Groq compound-mini route.
- Recovery: `/home/zombie/workspace/.workspacebackup/mcp-tool-fixes-20260916/backup.md`.
- Verification: `tests/test_mcp_tool_audit_fixes.py` plus focused MCP/auth tests.


## 2026-09-24 — MCP init version alignment

- Scope: `app/services/init_service.py` and package regression coverage only; WebMCP, AILinux Helper wire protocol, workspace leases, and device-control schemas are unchanged.
- Reason: the live MCP handshake reports the product version from `app.config.VERSION`, while CompactInitGenerator still emitted stale hard-coded `2.80.0` metadata.
- Change: CompactInitGenerator now imports `VERSION`, uses it for its internal version and provider/universal MCP metadata, and regression coverage rejects reintroducing the stale literal.
- Compatibility: cached ChatGPT Local-MCP schemas continue to use the existing `@device` shell compatibility bridge; no Helper/WebMCP behavior was modified.
- Recovery: `/home/zombie/workspace/.workspacebackup/mcp-init-version-20260924-062426/`.
- Verification: focused package/runtime tests, MCP workspace/device compatibility tests, syntax/diff checks, and live MCP initialize/tool-list probes.


## 2026-09-27 — Memory Training Center + verified bugfix archive

- Scope: Claude-Mem evidence distillation, bug-report resolution metadata/archive, MCP memory/bug triage tools and regression tests.
- Reason: turn persistent agent history into reusable engineering evidence without treating model output as current truth or allowing automatic self-modification.
- Added `memory_training`, a read-only internal-operator digest that mines completed Project-Memory workflows, completed runs, failures and documented resolved bugs into best-practice, anti-pattern and regression candidates.
- Added bounded cross-project Claude-Mem discovery through the worker API; full records remain project-scoped and no direct Claude-Mem SQLite access is introduced.
- Added `bug_report_resolve`: a bug becomes training-eligible only with fix summary, concrete verification and a maintained documentation reference; optional fix version/commit preserve provenance.
- Verified resolutions are persisted first and archived separately to `bugs@ailinux.me` as `[AILinux Bugfix]` mail. Mail failure is fail-open for the already-recorded resolution; identical resolved records are idempotent.
- Safety: no automatic Curated-Memory promotion, prompt rewrite, model routing change or code modification. Runtime/code/tests remain authoritative.
- Recovery: `/home/zombie/workspace/.workspacebackup/memory-training-center-20260927-095444/`.
- Verification: focused Memory/Claude-Mem/Bug Reporter tests plus live Training Center digest and canonical MCP registry smoke checks.


## 2026-09-28 — Verified feature-experience memory bridge

- Scope: AICoder verified feature-experience completion, Project Memory synchronization and Memory Training Center distillation.
- Reason: real completed AILinux/AICoder changes were retained locally in `feature_experience` but were not automatically entering Project Memory/Claude-Mem, leaving the Training Center dominated by E2E/synthetic observations.
- Change: after a mutation with fresh verification, AICoder now writes a bounded `aicoder-feature-experience-v1` Project Memory record containing task, summary, architecture, verification, lessons and future-feature ideas, then performs best-effort sync. TriForce already mirrors accepted Project Memory revisions into Claude-Mem. Dev-MCP now exposes the internal `feature_experience_store` completion hook for the same structured experience fields when work is completed directly through MCP.
- Training: the Training Center recognizes both synchronized AICoder feature experiences and verified Dev-MCP `feature_experience` observations as `verified_feature_experience`, emitting a corresponding `feature_regression` candidate while retaining provenance.
- Safety: Project Memory secret filtering remains authoritative; sync/Claude-Mem outages are fail-open and leave the local row dirty for later retry rather than failing a successful coding run.
- Recovery: `.workspacebackup/feature-experience-sync-20260928/` in both `ai-coder` and `triforce` workspaces.
- Verification: AICoder online/offline sync simulation passed; TriForce memory-training tests 5/5 passed; synthetic feature-experience distillation preserved architecture, verification, lessons and regression evidence.
