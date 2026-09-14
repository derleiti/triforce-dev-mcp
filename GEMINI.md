# Gemini Workspace Analysis


<!-- AILINUX_STATUS_START -->
## Current TriForce coordination defaults

TriForce remains a multi-provider platform, but the default local route is now `ollama/gemma4:12b`.

Gemini integrations remain available for explicit Gemini tasks and function-calling paths. The service currently logs a warning about the deprecated `google.generativeai` package, so migration to `google.genai` should be planned.

When updating Gemini-specific code, preserve provider boundaries and do not replace Ollama defaults unless explicitly requested.
<!-- AILINUX_STATUS_END -->

This document provides a comprehensive overview of the **TriForce AI Backend** project (`ailinux-ai-server-backend`). It is intended to be used as a guide for developers and AI agents to understand the codebase, architecture, and development conventions.

## Project Overview

**TriForce** is a Multi-LLM Orchestration Backend designed to coordinate various AI models (Ollama, Gemini, Mistral, Claude, GPT-OSS) through a unified API. It fully implements the **Model Context Protocol (MCP)**, enabling seamless integration with CLI tools like Claude Code, Codex CLI, and Gemini CLI.

### Key Capabilities
*   **Multi-LLM Support:** Orchestrates 115+ models (Local & Cloud).
*   **MCP Implementation:** Provides MCP JSON-RPC endpoints for agentic tool use.
*   **Mesh AI:** Gemini-led coordination system for multi-model consensus and task delegation.
*   **Shortcode Protocol v2.0:** Token-efficient syntax for inter-agent communication.
*   **TriStar Memory:** 12-shard shared memory system with confidence scoring.
*   **Command Queue:** Prioritized task distribution mechanism.
*   **Web Crawler:** AI-driven tools for website analysis and data extraction.

## Architecture

The project is built using **Python 3.11+** and **FastAPI**, backed by **Redis** for state management and caching.

*   **Framework:** FastAPI (Async/Await)
*   **Server:** Uvicorn / Gunicorn
*   **Database/Cache:** Redis
*   **Containerization:** Docker & Docker Compose
*   **Provider Layer:** Integrations for Ollama, Google Gemini, Anthropic, Mistral, OpenAI.

## Directory Structure

*   `app/`: Main application source code.
    *   `main.py`: FastAPI entry point and app configuration.
    *   `config.py`: Configuration management (Pydantic settings).
    *   `routes/`: API route definitions (split by domain: `mcp`, `mesh`, `agents`, etc.).
    *   `services/`: Business logic (Chat, Mesh Coordinator, Command Queue, Memory Controller).
    *   `mcp/`: MCP-specific handlers, API documentation, and tool registry.
    *   `utils/`: Shared utilities (Logging, Metrics, Auth).
*   `tests/`: Pytest suite for unit and integration testing.
*   `bin/`: CLI tools and helper scripts.
*   `deployment/`: Systemd service files and deployment configs.
*   `docs/`: Documentation for protocols and setups.

## Building and Running

### Prerequisites
*   Python 3.11+
*   Redis Server
*   (Optional) Ollama running locally

### Local Development Setup

1.  **Clone and Virtual Env:**
    ```bash
    git clone <repo_url>
    cd ailinux-ai-server-backend
    python3 -m venv .venv
    source .venv/bin/activate
    ```

2.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    Copy `.env.example` to `.env` and populate API keys (Gemini, Mistral, Anthropic, etc.).
    ```bash
    cp .env.example .env
    ```

4.  **Run Development Server:**
    ```bash
    uvicorn app.main:app --host 0.0.0.0 --port 9000 --reload
    ```
    *Note: Check `AGENTS.md` or `.env` if port 9100 is preferred.*

### Docker Deployment

*   **Build:**
    ```bash
    docker build -t triforce-backend .
    ```
*   **Run with Compose:**
    ```bash
    docker compose up -d
    ```

### Testing

Run the test suite using `pytest`:
```bash
# Run all tests
pytest tests/

# Run specific test file
pytest tests/test_mcp.py -v
```

## Protocols

### Shortcode Protocol v2.0
A syntax for efficient agent-to-agent communication (defined in `README.md` and `app/services/tristar/shortcodes.py`).
*   **Aliases:** `@g` (Gemini Lead), `@c` (Claude Worker), `@x` (Codex), etc.
*   **Actions:** `!gen` (Generate), `!code` (Code), `!review` (Review), `!search` (Search).
*   **Flow:** `>` (Send), `>>` (Chain), `|` (Pipe).

### Token-Saver Protocol (TSP) v2
Optimized directives for coding agents (defined in `TSP_PROTOCOL.md`).
*   **Directives:** `§C` (Code), `§R` (Review), `§F` (Fix).
*   **References:** `@File`, `@Dir`, `@Agent`.

## Development Conventions

*   **Style:** PEP8 compliance. Use type hints for all function signatures. Prefer `async/await` for I/O bound operations.
*   **Module Organization:** Keep logic in `services/` and routing in `routes/`.
*   **Security:** Never commit secrets. Use `.env` for all sensitive configuration.
*   **MCP Handlers:** Register new MCP tools in the dedicated handler modules within `app/mcp/` or `app/services/`.
*   **Commits:** Use imperative subject lines (e.g., "Add feature X", "Fix bug Y").

## Key Files for Context
*   `README.md`: Primary project documentation.
*   `AGENTS.md`: Detailed guide on agent capabilities and configuration.
*   `app/main.py`: Entry point for the FastAPI application.
*   `app/routes/mcp.py`: Core logic for the Model Context Protocol endpoints.

## Local Project Hygiene Context

When assisting in this repository, distinguish source changes from runtime cleanup. The live project checkout is `/home/zombie/workspace/triforce`; the local review checkout may be `/home/zombie/workspace/triforce-review`. Always verify the root and branch before writing files.

Generated or host-local assets such as `.venv/`, `__pycache__/`, Docker repository mirror data, generated Debian package staging, and backup/patch directories should be ignored or backed up outside Git rather than committed. Route cleanup must preserve active compatibility modules referenced by `app/main.py`, especially `app/routes_sd3.py` and `app/routes_vision.py`.
