# Copilot Instructions — AILinux / TriForce


<!-- AILINUX_STATUS_START -->
## Repository-specific operating rules

- Main production branch: `nova-nextlevel-20260603`.
- Current production HEAD: `16f43b8a`.
- Production checkout path: `/home/zombie/workspace/triforce`.
- Default model route: `ollama/gemma4:12b`.
- Runtime directories and local config files must not be committed.
- Use explicit pathspecs when staging changes.
- Preserve legacy route modules `app/routes_sd3.py` and `app/routes_vision.py` unless `app/main.py` imports are migrated in the same change.
- Keep generated, cache, backup, vendor, and runtime documentation out of GitHub documentation refreshes.
<!-- AILINUX_STATUS_END -->

## Project Context
This is part of the AILinux ecosystem by Markus Leitermann (@derleiti, Warzenried, Oberpfalz).
Backend: TriForce — FastAPI multi-LLM orchestration, 659+ models, MCP tools, WireGuard federation mesh.

## Stack
- Python 3.12, FastAPI, uvicorn, httpx
- Redis, Docker, Apache reverse proxy
- PyQt6 (desktop clients)
- MCP (Model Context Protocol) — JSON-RPC 2.0

## Coding Style
- Efficiency beats enthusiasm.
- No padding, no unnecessary abstraction.
- Ursache vor Fix — understand before implementing.
- Short, robust changes. No blind overwrites.
- Always syntax-check Python before suggesting: `python3 -c "import ast; ast.parse(...)"`
- Prefer subprocess over MCP for local execution.
- German variable names are acceptable, English for APIs.

## Architecture Rules
- MCP tools are READ-ONLY for aicoder users. No shell/binary_exec/code_edit on server.
- Execution (shell, service management) runs LOCALLY via subprocess.
- JWT tokens contain: client_id, sub (email), role, account_role, tier.
- Backend base URL for local/WireGuard: http://10.10.0.1:9000
- Backend base URL for external: https://api.ailinux.me

## Key Paths (Hetzner server)
- Backend: /home/zombie/workspace/triforce/
- Config: /home/zombie/workspace/triforce/config/triforce.env
- Users: /config/users.json
- WP: /home/zombie/workspace/triforce/docker/wordpress/html/
