# TriForce Documentation


<!-- AILINUX_STATUS_START -->
## Documentation map and production baseline

Authoritative project documentation starts with:

- `README.md` for the public overview.
- `SERVER_DOCUMENTATION.md` for production operations.
- `AGENTS.md` for agent workspace rules.
- `docs/MCP_NODE_OPENCLAW.md` for OpenClaw/MCP node operation.
- `docs/API_DOCUMENTATION.md` and `docs/api/REST.md` for API details.
- `docs/ARCHITECTURE.md` and `docs/architecture/OVERVIEW.md` for architecture notes.
- `docs/architecture/episodic-memory.md` for the native persistent Agent Memory Fabric and Claude-Mem provider integration.

Current baseline: production branch `master`; episodic-memory integration baseline `65eec52f`; service `triforce.service`; API `https://api.ailinux.me`; default chat model `ollama/gemma4:12b`.

Generated, cache, backup, vendor, and runtime folders are not authoritative documentation sources.
<!-- AILINUX_STATUS_END -->

## Übersicht

| Dokument | Beschreibung |
|----------|--------------|
| [INSTALL.md](INSTALL.md) | Vollständige Installationsanleitung |
| [QUICKSTART.md](QUICKSTART.md) | In 5 Minuten starten |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System-Architektur |
| [BUSINESS_PLAN_BETA.md](BUSINESS_PLAN_BETA.md) | Business Model |

## API Referenz

| Dokument | Beschreibung |
|----------|--------------|
| [api/REST.md](api/REST.md) | REST API Dokumentation |
| [api/MCP.md](api/MCP.md) | MCP Tools Referenz |

## Guides

| Dokument | Beschreibung |
|----------|--------------|
| [guides/HUB_SETUP.md](guides/HUB_SETUP.md) | Hub-Server einrichten |
| [guides/CLIENT_SETUP.md](guides/CLIENT_SETUP.md) | Client installieren |
| [guides/CONTRIBUTING.md](guides/CONTRIBUTING.md) | Wie beitragen? |

## Architektur

| Dokument | Beschreibung |
|----------|--------------|
| [architecture/FEDERATION.md](architecture/FEDERATION.md) | Federation Protocol |
| [architecture/SECURITY.md](architecture/SECURITY.md) | Sicherheitskonzept |
| [architecture/episodic-memory.md](architecture/episodic-memory.md) | Native Agent Memory Fabric, Claude-Mem Adapter, Trigger, Privacy und Failure-Verhalten |
| [MCP_TOOL_AUDIT_2026-09-09.md](MCP_TOOL_AUDIT_2026-09-09.md) | MCP Code-Review: tote Tools, Duplikate, 39-Core/79-Full Konsolidierung |

---

## Quick Links

- **API**: https://api.ailinux.me
- **Status**: https://api.ailinux.me/v1/mesh/resources
- **GitHub**: https://github.com/derleiti/triforce
