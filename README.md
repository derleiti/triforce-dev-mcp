# TriForce AI Platform

[![CI](https://github.com/derleiti/triforce-dev-mcp/actions/workflows/ci.yml/badge.svg?branch=master)](https://github.com/derleiti/triforce-dev-mcp/actions/workflows/ci.yml)
[![Security](https://github.com/derleiti/triforce-dev-mcp/actions/workflows/security.yml/badge.svg?branch=master)](https://github.com/derleiti/triforce-dev-mcp/actions/workflows/security.yml)

**Current source version: 2.86.4** · Public API: https://api.ailinux.me · Public MCP/Helper entry: https://api.ailinux.me/v1/mcp

TriForce is the AILinux control plane for multi-model inference, canonical MCP capabilities, agent orchestration, workspace/device federation, policy enforcement and service integrations.

## AILinux family

| Component | Current role |
|---|---|
| TriForce | Control plane, canonical tool registry, public MCP/API and policy |
| AICoder 1.2.6 | Coding/DevOps worker and local agent runtime |
| AILinux Helper 2.90.13 | Cross-platform endpoint/workspace/device companion |
| AILinux Loom 0.3.0 alpha 2 | Capability fabric, target/grant/lease orchestration |

## MCP and capability model

TriForce exposes one canonical capability pool. Client-visible toolsets are projected from active grants rather than maintained as duplicate tool implementations. Workspace/device capabilities use scoped leases and support read-only/write modes independently of screen, clipboard, service or compute capabilities.

The browser/Helper pairing flow is available at:

```text
https://api.ailinux.me/v1/mcp
```

A one-time pairing ID binds the selected workspace to an MCP session; durable/reconnectable lease tokens allow transport churn without silently widening permissions.

## Runtime

Production source deployments use the repository checkout directly. The service itself is environment/deployment specific; do not assume package installation should overwrite a source-mode systemd unit.

Typical source setup:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Development verification:

```bash
.venv/bin/python -m compileall -q app tests
.venv/bin/python -m pytest -q
```

## Major subsystems

- FastAPI API and OpenAI-compatible surfaces
- multi-provider model routing and availability tracking
- canonical MCP registry and target-aware execution
- local/remote workspace federation
- helper/browser node WebSocket transport
- agent mesh and specialist routing
- persistent memory/evidence integrations
- Docker/service/runtime administration under policy
- notifications, integrations and deployment tooling

## GitHub Actions status note

As of **14 September 2026**, GitHub currently returns `startup_failure` for repository-authored TriForce workflows before creating any jobs, including a minimal one-step smoke workflow. Workflow YAML parses locally and repository Actions are enabled; other AILinux repositories on the same account run normally. This is tracked as a GitHub/repository Actions startup blocker rather than being hidden by a fake CI workaround.

CodeQL currently scans Python only. WebMCP JavaScript still lives inside `app/routes/mcp.py` as an embedded string, so adding `javascript` to the CodeQL language list today would create misleading coverage. JavaScript scanning is gated on extracting WebMCP into real source files under `ailinux-helper/apps/web/`.

## Security

Never commit `.env` files, API keys, signing keys, session tokens or runtime databases. Workspace/endpoint sharing must stay capability-scoped. See `SECURITY.md` and `docs/CONTRIBUTING.md`.

## Documentation map

- `docs/README.md` — documentation index
- `docs/CONTRIBUTING.md` — project-specific contributor notes
- `CHANGELOG.md` and `docs/CHANGELOG.md` — historical changes
- `workspace_browser/README.md` — desktop/browser workspace helper history
- `android_workspace/README.md` — Android workspace helper history
- `scripts/README.md` — operational scripts
- `COMMERCIAL-LICENSING.md` — commercial/alternative licensing

## License

TriForce's published open-source code remains under **GNU AGPL v3.0** as stated in `LICENSE`. Those grants remain valid. Copyright in AILinux-authored portions is held by Markus Leitermann / AILinux, which can additionally offer commercial/alternative terms for material it has the right to relicense. See `COMMERCIAL-LICENSING.md`.
