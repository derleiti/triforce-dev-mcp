# TriForce AI Platform


**Current source version: 2.86.8** · Public API: https://api.ailinux.me · MCP/Helper entry: https://api.ailinux.me/v1/mcp

TriForce is the control, orchestration and policy plane behind AILinux. It connects models, agents, MCP clients, workspaces, devices, memory, compute nodes and service integrations through one capability-oriented backend while keeping authorization and execution boundaries explicit.

## What TriForce provides

- **Canonical MCP tool fabric** — one authoritative tool/schema catalogue with semantic inventories, compatibility aliases and scope-aware discovery instead of duplicated client-specific tool definitions.
- **Multi-provider model routing** — model discovery, specialist routing, account-backed providers, local models and provider-aware fallback/error handling.
- **Agent orchestration** — direct agent calls, broadcasts, specialist workers, group/swarm coordination and resumable long-running workflows.
- **Workspace and device federation** — reconnectable Helper leases, local workspace execution, Android/Desktop/Web endpoints and user-consented device capabilities.
- **Distributed compute** — remote-node scheduling, resource-aware placement, optional Docker compute sandboxes and Ollama offload across available nodes.
- **Persistent memory fabric** — curated memory, Project Memory, Claude-Mem episodic history, bounded recall and explicit evidence-based promotion.
- **Memory Training Center** — read-only distillation of verified workflows, completed runs, failures and documented bug fixes into best-practice, anti-pattern and regression candidates.
- **Bug-report feedback loop** — privacy-aware crash/manual/self-test reporting, persisted triage, `bugs@ailinux.me` archive and verified fix closure.
- **Operations and integrations** — mail, notifications, WordPress, Flarum, search/crawl, repository/deployment tooling, runtime diagnostics and service administration under policy.

## AILinux family

| Component | Current published/source line | Role |
|---|---:|---|
| **TriForce** | **2.86.8** | Control plane, canonical MCP/API, agents, memory, policy and federation |
| **AICoder** | **1.4.3** | Coding/DevOps worker, Team Runtime and local Project Memory client |
| **AILinux Helper** | **2.90.34** | Cross-platform workspace/device endpoint and capability executor |
| **AILinux Loom** | **0.3.0a6** | Unified capability/control-center fabric |

Runtime state and each repository's own version metadata remain authoritative if this table ever lags behind a release.

## Architecture

```text
                   ┌────────────────────────────┐
                   │        MCP / API clients   │
                   │ ChatGPT · AICoder · Loom   │
                   └──────────────┬─────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                         TriForce                                │
│                                                                 │
│  Auth/RBAC ─ Canonical MCP Registry ─ Agent/Model Router       │
│      │              │                    │                      │
│      │              │                    ├─ Cloud/local models  │
│      │              │                    └─ Specialist agents   │
│      │              │                                           │
│      │              ├─ Workspace / Helper capability leases     │
│      │              ├─ Remote nodes / compute offload           │
│      │              └─ Service integrations                     │
│      │                                                          │
│      └─ Curated Memory / Project Memory / Claude-Mem history    │
│                          │                                      │
│                          └─ Memory Training Center               │
└─────────────────────────────────────────────────────────────────┘
              │                       │
              ▼                       ▼
      AILinux Helper nodes       Service infrastructure
      desktop/android/web        repo/mail/forum/web/etc.
```

## Canonical MCP and capability model

The canonical MCP endpoint is:

```text
https://api.ailinux.me/v1/mcp
```

TriForce does not treat every connected client as an administrator. Tool discovery is separated from authorization:

- tool schemas are canonical and shared;
- semantic inventories keep normal model context small;
- `x_scope` / inventory metadata identify ownership and task groupings;
- public/authenticated/admin capabilities remain separate authorization domains;
- paired Helper/workspace tools are available only through the active lease and advertised capabilities;
- cached legacy aliases can remain recognized during rolling upgrades without becoming a second tool implementation.

Use the live `tools/list` result rather than hard-coded tool counts. The registry changes as capabilities are added, consolidated or hidden from a given client scope.

## Models and agents

TriForce provides a provider-neutral routing layer rather than tying AILinux to one model vendor. Current routing supports cloud, account-backed and local-model paths, with runtime availability deciding what is actually usable.

Key behavior includes:

- model discovery and capability-aware selection;
- specialist and general chat routes;
- explicit provider/account authentication boundaries;
- distinct fallback routes rather than retrying a broken provider under another label;
- bounded provider/reasoning settings for agent workflows;
- structured error classification for quota, auth, transport and provider failures;
- multi-agent calls, broadcasts and orchestration.

## Workspace, Helper and distributed compute

AILinux Helper can attach a user-approved workspace or device to TriForce without turning the endpoint into an unrestricted remote shell.

The connection model includes:

- short-lived pair codes for bootstrap;
- durable reconnect/resume credentials after pairing;
- scoped read/write/device capability grants;
- transport/executor health tracked separately;
- Android Accessibility-gated computer control;
- browser/PWA fallback;
- local workspace tools and remote compute as distinct capabilities.

For compute-heavy tasks, TriForce can schedule work across registered nodes. Optional compute sandboxes expose a bounded workspace copy with resource limits, dropped capabilities, no Docker socket, no host networking and controlled write-back/recovery behavior.

## Memory fabric

TriForce deliberately separates different kinds of memory:

```text
Current code / tests / runtime evidence     ← highest authority
                 ↓
Project Memory                              ← current structured project state
                 ↓
Curated Memory                              ← explicitly verified knowledge
                 ↓
Claude-Mem episodic history                 ← what happened before
```

Historical memory is context, not truth. Current code, tests and runtime evidence always win when they disagree with an old observation.

### Project Memory

TriForce is the synchronization authority for Project Memory. AICoder can work local-first, keep dirty/offline changes, preserve conflicts and tombstones, then synchronize accepted revisions through TriForce.

Accepted revisions can be mirrored into Claude-Mem as append-only history without making Claude-Mem the current-state authority.

### Claude-Mem episodic history

The Claude-Mem integration uses the local worker API through a provider abstraction rather than direct SQLite coupling. The worker is expected to stay loopback-only. Recall is bounded, redacted and fail-open: a memory outage must not break normal TriForce execution.

### Memory Training Center

`memory_training` is an internal, read-only evidence distillation tool. It can turn selected high-signal history into:

- verified workflow / best-practice candidates;
- completed-run evidence;
- anti-pattern/failure clusters;
- regression-test candidates;
- verified bugfix candidates.

It **does not** train model weights, rewrite prompts, change routing, edit code or automatically promote historical content into curated memory. Promotion remains an explicit, evidence-gated action.

See `docs/architecture/episodic-memory.md`.

## Bug reporting and verified fix archive

AILinux applications can submit bounded, redacted diagnostics to:

```text
POST https://api.ailinux.me/v1/bugs/report
```

Clients never receive SMTP credentials. TriForce persists reports first, deduplicates/rate-limits notification mail and archives reports server-side to `bugs@ailinux.me`.

A bug becomes reusable Training Center evidence only after `bug_report_resolve` receives:

- a fix summary;
- concrete verification evidence;
- a maintained documentation/changelog reference;
- optionally the fixed version and commit.

TriForce then archives a second `[AILinux Bugfix]` message. Simply changing a triage state to `resolved` is not sufficient to create a verified best practice.

See `docs/BUG_REPORTING.md`.

## Evidence-first operating model

TriForce's agent/runtime policy follows a deliberately conservative engineering loop:

```text
understand → inspect → create rollback → change → test → verify → improve
```

For repository mutations this means:

- inspect the real working tree and runtime before acting;
- create a recovery path first;
- preserve unrelated local changes;
- prefer typed tools over broad shell access;
- verify the original acceptance condition, not merely an exit code;
- update the nearest authoritative documentation together with the implementation;
- treat historical memory and generated suggestions as evidence to re-check, not instructions to obey blindly.

## Development setup

Production source deployments can run directly from a repository checkout. Service/unit layout is deployment-specific; do not assume package installation should overwrite an existing source-mode service.

Typical development setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Verification:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
git diff --check
```

## Security

- Never commit `.env` files, API keys, signing keys, passwords, session/resume tokens or runtime databases.
- Workspace/device sharing remains capability- and lease-scoped.
- Client-facing bug reporters do not contain mail credentials.
- Memory payloads and bug reports redact common secret material.
- Claude-Mem integration is designed for local loopback access and bounded responses.
- Administrative tools remain separate from normal client capability surfaces.
- Destructive workflows should establish a backup/snapshot/recovery path before mutation.

See `SECURITY.md` and `docs/CONTRIBUTING.md`.

## Documentation map

- `docs/README.md` — documentation index
- `docs/MCP_TOOLS.md` — canonical MCP/tool model and examples
- `docs/architecture/episodic-memory.md` — memory fabric and Training Center
- `docs/BUG_REPORTING.md` — crash/manual report and verified bugfix flow
- `docs/AI_CHANGELOG.md` — implementation records for recent AI-assisted changes
- `docs/CONTRIBUTING.md` — project-specific contributor notes
- `CHANGELOG.md` / `docs/CHANGELOG.md` — historical change logs
- `workspace_browser/README.md` — desktop/browser workspace helper history
- `android_workspace/README.md` — Android workspace helper history
- `scripts/README.md` — operational scripts
- `COMMERCIAL-LICENSING.md` — commercial/alternative licensing

## Public services

| Service | Address |
|---|---|
| API | https://api.ailinux.me |
| Health | https://api.ailinux.me/health |
| MCP | https://api.ailinux.me/v1/mcp |
| Repository | https://repo.ailinux.me |
| AILinux | https://ailinux.me |

## License

TriForce's published open-source code remains under **GNU AGPL v3.0** as stated in `LICENSE`. Those grants remain valid. Copyright in AILinux-authored portions is held by Markus Leitermann / AILinux, which can additionally offer commercial/alternative terms for material it has the right to relicense. See `COMMERCIAL-LICENSING.md`.
