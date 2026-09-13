# Repository Guidelines


<!-- AILINUX_STATUS_START -->
## Active agent and model policy

- Default server chat model: `ollama/gemma4:12b`.
- Local Ollama tag: `gemma4:12b`.
- OpenClaw primary model should stay on the Ollama Gemma 4 12B route.
- AI-Coder selected and fallback model should be `ollama/gemma4:12b`.
- Provider-specific models remain available as explicit alternatives, but must not silently replace the default route.

Agent workspace rules: edit tracked source/docs only; keep runtime data, local env files, logs, Docker/n8n volumes, package caches, and generated binaries out of Git. Use explicit pathspecs when staging changes.
<!-- AILINUX_STATUS_END -->

## Project Structure & Module Organization
- `app/`: FastAPI backend with routes (`routes/`), services (`services/`), MCP handlers (`mcp/`), and shared utilities (`config.py`, `main.py`).
- `tests/`: Pytest suite covering MCP, integrations, and services.
- `bin/`: CLI tools (`tristar` orchestration CLI, startup scripts) and TUI helpers.
- `docs/`, `README.md`: Protocol docs and quick start; `systemd/` holds service unit templates; `node_modules/` and `nova-ai-frontend*/` are frontend assets (mostly untouched for backend work).
- Config lives in `.env` (copy from `.env.example`) with provider keys and ports.

## Build, Test, and Development Commands
- Install deps: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- Run dev server (FastAPI): `.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9100 --reload`
- Smoke MCP endpoints: `curl -X POST http://localhost:9100/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'`
- Tests: `.venv/bin/pytest` or target a file, e.g. `.venv/bin/pytest tests/test_mcp.py -v`
- CLI check: `bin/tristar status` (prefers local `http://localhost:9100/v1/mcp`)

## Coding Style & Naming Conventions
- Python 3.11+, PEP8; prefer type hints and async/await for I/O. Keep functions small and log via existing loggers (see `logging.getLogger("ailinux.*")`).
- Route handlers live in `app/routes/`; service logic belongs in `app/services/`; keep MCP tools in dedicated handler modules.
- Name MCP methods with dotted namespaces (`llm.invoke`, `admin.crawler.config.set`) and keep JSON-RPC payloads snake_case.

## Testing Guidelines
- Framework: pytest; async tests use `@pytest.mark.asyncio`. Mock external calls (LLM, HTTP, Ollama) like in `tests/test_mcp.py`.
- Add focused tests per service file; name tests `test_<behavior>` and mirror module paths under `tests/`.
- Prefer fast, offline tests; gate network/API-key cases with `@pytest.mark.skipif`.

## Commit & Pull Request Guidelines
- Commits: short imperative subject (<=72 chars), optional body for rationale; align scope to a single concern (e.g., “fix mcp llm.invoke token accounting”).
- PRs: include summary, scope (routes/services touched), testing proof (`pytest ...` output or curl snippet), and any config/env changes. Link issues/tickets and add screenshots for UI-affecting changes.

## Security & Configuration Tips
- Never commit secrets; load keys via `.env` only. Redis and provider endpoints are assumed local by default—avoid embedding public URLs.
- MCP/Tristar ports default to 9100; update scripts if you change them. Use localhost endpoints for agent calls to stay offline.

## Repository Hygiene Rules for Agents

- Never run `git add .` from `/home/zombie` or any user home directory. Confirm the repository root before staging changes.
- Treat `.venv/`, `__pycache__/`, logs, Docker mirror data, generated Debian build directories, and patch/backups as non-source artifacts.
- Prefer explicit paths with `git add README.md SERVER_DOCUMENTATION.md AGENTS.md` over broad staging.
- Before deleting files, search active imports/references with `grep -R` and run `python3 -m compileall app -q`.
- `app/routes_sd3.py` and `app/routes_vision.py` are active while `app/main.py` imports them; do not classify them as dead code solely because similar modules exist under `app/routes/`.
- Back up production data outside the repository before cleanup. Suggested local scratch location: `/home/zombie/triforce-backup-cleanup/`.

## Shared Workspace Recovery Policy
- Canonical per-user workspace root: `~/workspace` (override: `AILINUX_WORKSPACE_ROOT`).
- Canonical cross-app fallback store: `~/workspace/.workspacebackup` (override: `AILINUX_WORKSPACE_BACKUP_ROOT`).
- Before every mutating workspace interaction, create a fallback backup. Targeted edits back up the affected path; shell/task/binary operations with unknown write scope snapshot the workspace before execution.
- Never place recovery backups inside Git history, never delete them as part of normal success cleanup, and never recursively back up `.workspacebackup` itself.
- Prompt guidance is advisory; runtime backup enforcement is authoritative. If a required backup fails, block the mutation.
