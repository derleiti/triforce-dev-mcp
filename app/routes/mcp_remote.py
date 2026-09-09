"""
MCP Remote Server for Claude.ai Connectors

This module implements the Model Context Protocol (MCP) Remote Server specification
for integration with Claude.ai custom connectors.

Features:
- Multi-model AI chat (Ollama, Gemini, Mistral, Anthropic, GPT-OSS)
- Vision analysis with multiple providers
- Web crawling and content extraction
- Model specialist routing for expert task delegation
- Context management for multi-turn conversations
- Prompt templates for common tasks
- WordPress content publishing
- API documentation lookup

Endpoints:
- GET /.well-known/mcp.json - MCP discovery
- GET /.well-known/oauth-authorization-server - OAuth discovery (optional)
- GET /mcp - SSE endpoint for MCP communication
- POST /mcp - JSON-RPC endpoint for tool calls
"""

from __future__ import annotations

import json
import uuid
import secrets
from datetime import datetime, timezone
from datetime import timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from ..services.model_registry import registry
from ..services import chat as chat_service
from ..services.crawler.user_crawler import get_user_crawler
from ..services.crawler.manager import crawler_manager
from ..services.wordpress import wordpress_service
from ..services.ollama_mcp import OLLAMA_TOOLS, OLLAMA_HANDLERS
from ..services.tristar_mcp import TRISTAR_TOOLS, TRISTAR_HANDLERS
# New Client-Server Architecture
from ..services.api_vault import VAULT_HANDLERS
from ..services.chat_router import CHAT_ROUTER_HANDLERS
from ..services.task_spawner import TASK_SPAWNER_HANDLERS
from ..services.gemini_access import GEMINI_ACCESS_TOOLS, GEMINI_ACCESS_HANDLERS
from ..services.command_queue import QUEUE_TOOLS, QUEUE_HANDLERS
from ..services.huggingface_inference import HF_INFERENCE_TOOLS, HF_HANDLERS
from ..mcp.adaptive_code import ADAPTIVE_CODE_TOOLS, ADAPTIVE_CODE_HANDLERS
from ..mcp.structured_admin import STRUCTURED_ADMIN_TOOLS, STRUCTURED_ADMIN_HANDLERS
from ..utils.throttle import request_slot
from ..mcp.api_docs import get_api_docs, API_DOCUMENTATION
from ..mcp.specialists import specialist_router, SPECIALISTS
from ..mcp.context import context_manager, prompt_library
from ..utils.mcp_auth import (
    AUTH_ENABLED,
    MCP_AUTH_USER,
    MCP_AUTH_PASS,
    require_mcp_auth,
    is_valid_token,
    create_token,
    add_token,
    get_active_tokens,
    get_persistent_tokens,
    _validate_credentials,
    _extract_basic_auth,
    _safe_compare,
)

router = APIRouter(tags=["MCP Remote Server"])


# NOTE: OAuth metadata endpoints are now in oauth_service.py

from app.config import VERSION


# ============================================================================
# MCP Server Info
# ============================================================================

MCP_SERVER_INFO = {
    "name": "AILinux API",
    "version": VERSION,
    "description": f"AILinux AI Backend {VERSION} - TriStar/TriForce Multi-LLM Orchestration with CLI Agents, Codebase Access, and Self-Development capabilities",
    "vendor": "AILinux",
}

MCP_CAPABILITIES = {
    "tools": True,
    "prompts": True,  # Now supports prompt templates
    "resources": False,
    "logging": False,
}

# ============================================================================
# Authentication - Uses central mcp_auth module
# NOTE: /authorize and /token endpoints are now in oauth_service.py
# ============================================================================


# Helper for localhost automation
@router.post("/auth/auto", tags=["Auth"], summary="Automated localhost authorization")
async def auto_authorize(request: Request):
    """
    Automates the OAuth flow for localhost CLI tools.
    Input: JSON with { "auth_url": "...", "username": "...", "password": "..." }
    Action: Parses URL, validates credentials, and triggers the callback.
    """
    from ..utils.mcp_auth import store_auth_code

    try:
        data = await request.json()
        auth_url = data.get("auth_url")
        username = data.get("username")
        password = data.get("password")

        if not auth_url:
            raise HTTPException(status_code=400, detail="Missing auth_url")

        # Verify credentials using central auth module
        if not _validate_credentials(username, password):
             raise HTTPException(status_code=401, detail="Invalid credentials for auto-auth")

        from urllib.parse import urlparse, parse_qs, urlencode
        parsed = urlparse(auth_url)
        params = parse_qs(parsed.query)

        state = params.get("state", [None])[0]
        redirect_uri = params.get("redirect_uri", [None])[0]
        client_id = params.get("client_id", [None])[0]

        if not (state and redirect_uri):
             raise HTTPException(status_code=400, detail="Invalid auth URL params")

        # Generate and store auth code using central auth module
        code = secrets.token_urlsafe(16)
        code_challenge = params.get("code_challenge", [None])[0]
        code_challenge_method = params.get("code_challenge_method", [None])[0]

        store_auth_code(
            code=code,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            user=username,
        )

        # Construct Callback URL
        callback_params = {"code": code, "state": state}
        callback_url = f"{redirect_uri}?{urlencode(callback_params)}"

        # Perform the callback (HIT the CLI tool's local server)
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            # CLI tools usually expect a GET request to their callback
            await client.get(callback_url)

        return {"status": "success", "callback_url": callback_url}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# NOTE: /token endpoint is now handled by oauth_service.py
# Token endpoints are kept for backward compatibility but use central mcp_auth module

@router.post("/auth/create-token", tags=["Auth"], summary="Create a long-lived token for Claude Web")
async def create_persistent_token(request: Request):
    """
    Create a long-lived Bearer token for Claude Web integration.
    This bypasses the OAuth redirect flow.

    POST with JSON: {"username": "...", "password": "...", "name": "claude-web"}
    Returns: {"token": "...", "expires_in": 86400*30}

    Use this token in Claude Web MCP settings as Bearer token.
    """
    try:
        data = await request.json()
    except:
        raise HTTPException(status_code=400, detail="JSON body required")

    username = data.get("username", "")
    password = data.get("password", "")
    token_name = data.get("name", "unnamed")

    if not _validate_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate long-lived token using central auth module
    token = create_token(user=token_name, scope="mcp", expires_days=30)

    return {
        "token": token,
        "token_type": "bearer",
        "expires_in": 86400 * 30,  # 30 days
        "name": token_name,
        "usage": {
            "header": f"Authorization: Bearer {token}",
            "claude_web": "In Claude Web MCP settings, use this token as Bearer token",
            "curl_example": f"curl -H 'Authorization: Bearer {token}' https://api.ailinux.me/v1/mcp -d '{{\"jsonrpc\":\"2.0\",\"method\":\"tools/list\",\"id\":1}}'"
        }
    }


@router.get("/auth/tokens", tags=["Auth"], summary="List active tokens")
async def list_tokens(request: Request):
    """List all active persistent tokens (requires auth)."""
    await require_mcp_auth(request)

    persistent = get_persistent_tokens()
    tokens_info = []
    for token, info in persistent.items():
        tokens_info.append({
            "token_prefix": token[:12] + "...",
            "name": info.get("name") or info.get("user"),
            "created_at": info.get("created_at"),
            "expires_at": info.get("expires_at")
        })

    return {"tokens": tokens_info, "count": len(tokens_info)}


# ============================================================================
# Tool Definitions
# ============================================================================

def get_tools() -> List[Dict[str, Any]]:
    """Return available MCP tools."""
    return [
        # =================================================================
        # Chat & Models
        # =================================================================
        {
            "name": "chat",
            "description": "Send a message to an AI model. Supports Ollama, Gemini, Mistral, Anthropic Claude, and GPT-OSS.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The message to send to the AI model"
                    },
                    "model": {
                        "type": "string",
                        "description": "Model ID (e.g., 'anthropic/claude-sonnet-4', 'gemini/gemini-2.0-flash'). Use list_models to see all.",
                        "default": "gpt-oss:20b-cloud"
                    },
                    "system_prompt": {
                        "type": "string",
                        "description": "Optional system prompt to set context"
                    },
                    "temperature": {
                        "type": "number",
                        "description": "Sampling temperature (0.0-2.0)",
                        "default": 0.7
                    }
                },
                "required": ["message"]
            }
        },
        {
            "name": "list_models",
            "description": "List all available AI models with their capabilities",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },

        # =================================================================
        # Specialist Routing (NEW)
        # =================================================================
        {
            "name": "ask_specialist",
            "description": "Route a task to the best specialist model. Automatically selects the optimal model based on task type (coding, security, German language, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Task description for routing (e.g., 'code review', 'security audit', 'German translation')"
                    },
                    "message": {
                        "type": "string",
                        "description": "The actual message/prompt for the specialist"
                    },
                    "preferred_speed": {
                        "type": "string",
                        "enum": ["fast", "medium", "slow"],
                        "description": "Preferred response speed"
                    }
                },
                "required": ["task", "message"]
            }
        },
        {
            "name": "list_specialists",
            "description": "List all available model specialists with their capabilities and optimal use cases",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },

        # =================================================================
        # Vision
        # =================================================================
        {
            "name": "analyze_image",
            "description": "Analyze an image using a vision-capable AI model (Gemini or Claude)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "URL of the image to analyze"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Question or instruction about the image",
                        "default": "Describe this image in detail"
                    },
                    "model": {
                        "type": "string",
                        "description": "Vision model (e.g., 'gemini/gemini-2.0-flash', 'anthropic/claude-sonnet-4')",
                        "default": "gemini/gemini-2.0-flash"
                    }
                },
                "required": ["image_url"]
            }
        },

        # =================================================================
        # Web Crawling (NEW)
        # =================================================================
        {
            "name": "crawl_url",
            "description": "Crawl a website and extract content. Fast single-URL crawl with optional keyword filtering.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to crawl"
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional keywords for relevance filtering"
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to crawl",
                        "default": 10
                    }
                },
                "required": ["url"]
            }
        },
        {
            "name": "crawl_site",
            "description": "Deep crawl a website with multiple starting URLs and depth control.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "site_url": {
                        "type": "string",
                        "description": "Main site URL"
                    },
                    "seeds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Starting URLs (defaults to site_url)"
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords for filtering"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum link depth",
                        "default": 2
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages",
                        "default": 40
                    }
                },
                "required": ["site_url"]
            }
        },
        {
            "name": "crawl_status",
            "description": "Get the status and results of a crawler job",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Crawler job ID"
                    },
                    "include_results": {
                        "type": "boolean",
                        "description": "Include crawled content in response",
                        "default": False
                    }
                },
                "required": ["job_id"]
            }
        },

        # =================================================================
        # WordPress Publishing (NEW)
        # =================================================================
        {
            "name": "create_post",
            "description": "Create a new WordPress blog post",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Post title"
                    },
                    "content": {
                        "type": "string",
                        "description": "Post content (HTML supported)"
                    },
                    "status": {
                        "type": "string",
                        "enum": ["publish", "draft", "pending"],
                        "description": "Post status",
                        "default": "draft"
                    }
                },
                "required": ["title", "content"]
            }
        },

        # =================================================================
        # Context & Conversations (NEW)
        # =================================================================
        {
            "name": "conversation",
            "description": "Manage multi-turn conversations with context. Add messages and get AI responses.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": "Conversation session ID (auto-generated if not provided)"
                    },
                    "message": {
                        "type": "string",
                        "description": "User message to add"
                    },
                    "model": {
                        "type": "string",
                        "description": "Model for response generation"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["add", "get", "clear", "list"],
                        "description": "Action to perform",
                        "default": "add"
                    }
                },
                "required": ["action"]
            }
        },

        # =================================================================
        # Prompt Templates (NEW)
        # =================================================================
        {
            "name": "prompt_template",
            "description": "Use pre-built prompt templates for common tasks (code_review, security_audit, documentation, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "render", "add"],
                        "description": "Action: list templates, render with variables, or add custom"
                    },
                    "name": {
                        "type": "string",
                        "description": "Template name (e.g., 'code_review', 'security_audit', 'german_content')"
                    },
                    "variables": {
                        "type": "object",
                        "description": "Variables to substitute in template"
                    }
                },
                "required": ["action"]
            }
        },

        # =================================================================
        # API Documentation (NEW)
        # =================================================================
        {
            "name": "api_docs",
            "description": "Get AILinux API documentation. Query endpoints, MCP methods, and usage examples.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["endpoints", "mcp_methods", "providers", "usage_examples", "info"],
                        "description": "Documentation section to retrieve"
                    },
                    "search": {
                        "type": "string",
                        "description": "Search for endpoints by task description"
                    }
                },
                "required": []
            }
        },

        # =================================================================
        # Web Search
        # =================================================================
        {
            "name": "web_search",
            "description": "Search the web for information using AI-powered search",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    }
                },
                "required": ["query"]
            }
        },

        # =================================================================
        # TriStar Integration (v2.80)
        # =================================================================
        {
            "name": "tristar_models",
            "description": "Get all registered TriStar LLM models with roles (admin, lead, worker, reviewer) and capabilities",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["admin", "lead", "worker", "reviewer"],
                        "description": "Filter by role"
                    },
                    "capability": {
                        "type": "string",
                        "description": "Filter by capability (code, math, reasoning, vision)"
                    },
                    "provider": {
                        "type": "string",
                        "description": "Filter by provider (ollama, gemini, anthropic, mistral)"
                    }
                },
                "required": []
            }
        },
        {
            "name": "tristar_init",
            "description": "Initialize (impfen) a model with system prompt and configuration",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model_id": {
                        "type": "string",
                        "description": "Model ID to initialize"
                    }
                },
                "required": ["model_id"]
            }
        },
        {
            "name": "tristar_memory_store",
            "description": "Store a memory entry in TriStar shared memory with confidence scoring",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Memory content to store"
                    },
                    "memory_type": {
                        "type": "string",
                        "enum": ["fact", "decision", "code", "summary", "context", "todo"],
                        "description": "Type of memory"
                    },
                    "llm_id": {
                        "type": "string",
                        "description": "ID of the LLM storing the memory"
                    },
                    "initial_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Initial confidence score (0.0-1.0)"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization"
                    }
                },
                "required": ["content"]
            }
        },
        {
            "name": "tristar_memory_search",
            "description": "Search TriStar shared memory with confidence and tag filtering",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query"
                    },
                    "min_confidence": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "Minimum confidence score"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags"
                    },
                    "memory_type": {
                        "type": "string",
                        "description": "Filter by memory type"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum results (default: 20)"
                    }
                },
                "required": []
            }
        },

        # =================================================================
        # Codebase Access (v2.80) - Self-Development
        # =================================================================
        {
            "name": "codebase_structure",
            "description": "Get the backend codebase directory structure (app/, routes/, services/, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to scan (default: 'app')"
                    },
                    "include_files": {
                        "type": "boolean",
                        "description": "Include files in output (default: true)"
                    },
                    "max_depth": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "description": "Maximum directory depth (default: 4)"
                    }
                },
                "required": []
            }
        },
        {
            "name": "codebase_file",
            "description": "Read a specific file from the backend codebase (Python, YAML, JSON, etc.)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path (e.g., 'app/routes/mcp.py', 'app/main.py')"
                    }
                },
                "required": ["path"]
            }
        },
        {
            "name": "codebase_search",
            "description": "Search for patterns/text in the codebase (regex supported)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search pattern (regex supported)"
                    },
                    "path": {
                        "type": "string",
                        "description": "Relative path to search in (default: 'app')"
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "File glob pattern (default: '*.py')"
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum results (default: 50)"
                    },
                    "context_lines": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                        "description": "Context lines around match (default: 2)"
                    }
                },
                "required": ["query"]
            }
        },
        {
            "name": "codebase_routes",
            "description": "Get all API routes with their HTTP methods, paths, and handlers",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "codebase_services",
            "description": "Get all service modules with their classes and functions",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },

        # =================================================================
        # CLI Agents (v2.80) - Claude, Codex, Gemini Subprocess Management
        # =================================================================
        {
            "name": "cli-agents_list",
            "description": "List all CLI agents (Claude, Codex, Gemini subprocesses) with their status",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "name": "cli-agents_get",
            "description": "Get details for a specific CLI agent including output buffer",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID (e.g., 'claude-mcp', 'codex-mcp', 'gemini-mcp')"
                    }
                },
                "required": ["agent_id"]
            }
        },
        {
            "name": "cli-agents_start",
            "description": "Start a CLI agent subprocess (auto-fetches system prompt from TriForce)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to start"
                    }
                },
                "required": ["agent_id"]
            }
        },
        {
            "name": "cli-agents_stop",
            "description": "Stop a CLI agent subprocess",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to stop"
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force kill (default: false)"
                    }
                },
                "required": ["agent_id"]
            }
        },
        {
            "name": "cli-agents_restart",
            "description": "Restart a CLI agent (stop + start)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to restart"
                    }
                },
                "required": ["agent_id"]
            }
        },
        {
            "name": "cli-agents_call",
            "description": "Send a message to a CLI agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID to call"
                    },
                    "message": {
                        "type": "string",
                        "description": "Message to send"
                    },
                    "timeout": {
                        "type": "integer",
                        "minimum": 10,
                        "maximum": 600,
                        "description": "Timeout in seconds (default: 120)"
                    }
                },
                "required": ["agent_id", "message"]
            }
        },
        {
            "name": "cli-agents_broadcast",
            "description": "Broadcast a message to multiple or all CLI agents",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message to broadcast"
                    },
                    "agent_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Specific agent IDs (omit for all)"
                    }
                },
                "required": ["message"]
            }
        },
        {
            "name": "cli-agents_output",
            "description": "Get output buffer for a CLI agent",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_id": {
                        "type": "string",
                        "description": "Agent ID"
                    },
                    "lines": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 500,
                        "description": "Number of lines (default: 50)"
                    }
                },
                "required": ["agent_id"]
            }
        },
        {
            "name": "cli-agents_stats",
            "description": "Get statistics for CLI agents (count by status and type)",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },

        # =================================================================
        # Ollama Local LLM Tools (v2.80)
        # =================================================================
        *OLLAMA_TOOLS,

        # =================================================================
        # TriStar System Management Tools (v2.80)
        # =================================================================
        *TRISTAR_TOOLS,

        # =================================================================
        # Gemini Access Point Tools (v2.80)
        # =================================================================
        *GEMINI_ACCESS_TOOLS,

        # =================================================================
        # Command Queue Tools (v2.80)
        # =================================================================
        *QUEUE_TOOLS,

        # =================================================================
        # Hugging Face Inference Tools (v2.80)
        # =================================================================
        *HF_INFERENCE_TOOLS,

        # =================================================================
        # Adaptive Code Illumination Tools (v2.80)
        # =================================================================
        *ADAPTIVE_CODE_TOOLS,

        # =================================================================
        # Structured Admin Tools
        # =================================================================
        # Exposes tools already registered in app/mcp/structured_admin.py.
        # NOTE: Some of these are write/exec capable. Keep MCP auth strong.
        *STRUCTURED_ADMIN_TOOLS,
    ]


# ============================================================================
# Tool Handlers
# ============================================================================

def _serialize_job(job) -> Dict[str, Any]:
    """Serialize a crawler job to dict."""
    payload = job.to_dict()
    payload["allowed_domains"] = list(job.allowed_domains)
    return payload


async def handle_chat(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle chat tool invocation - supports both 'message' (string) and 'messages' (array)."""
    model_id = arguments.get("model", "gpt-oss:20b-cloud")
    temperature = arguments.get("temperature", arguments.get("options", {}).get("temperature", 0.7))
    
    # Support both formats: 'message' (string) or 'messages' (array)
    messages_input = arguments.get("messages")
    message = arguments.get("message")
    system_prompt = arguments.get("system_prompt")
    
    if messages_input and isinstance(messages_input, list):
        # OpenAI-Format: messages array
        messages = messages_input
    elif message:
        # Simple format: single message string
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": message})
    else:
        raise ValueError("'message' or 'messages' is required")
    
    model = await registry.get_model(model_id)
    if not model:
        model = await registry.get_model(f"ollama/{model_id}")
    valid_caps = {"chat", "code", "reasoning"}
    if not model or not any(cap in model.capabilities for cap in valid_caps):
        raise ValueError(f"Model '{model_id}' not found or does not support chat/code")
    
    chunks = []
    async with request_slot():
        async for chunk in chat_service.stream_chat(
            model,
            model_id,
            iter(messages),
            stream=True,
            temperature=temperature,
        ):
            if chunk:
                chunks.append(chunk)
    
    response = "".join(chunks)
    return {"response": response, "model": model_id}
async def handle_list_models(_: Dict[str, Any]) -> Dict[str, Any]:
    """
    List available models, optimized for context window usage.
    Returns models grouped by provider and capability.
    """
    models = await registry.list_models()
    
    # Group by provider
    by_provider = {}
    for m in models:
        if m.provider not in by_provider:
            by_provider[m.provider] = []
        by_provider[m.provider].append(m.id)

    # Group by key capabilities
    by_capability = {
        "code": [],
        "vision": [],
        "chat": [],
        "embedding": []
    }
    
    for m in models:
        for cap in m.capabilities:
            if cap in by_capability:
                by_capability[cap].append(m.id)
            elif cap == "image_gen":
                 if "vision" not in by_capability: by_capability["vision"] = []
                 by_capability["vision"].append(m.id)

    return {
        "summary": {
            "total_models": len(models),
            "providers": list(by_provider.keys())
        },
        "by_provider": by_provider,
        "by_capability": {k: v[:50] for k, v in by_capability.items()},
        "note": "Lists truncated to top 50 per capability to save context."
    }


async def handle_ask_specialist(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Route task to best specialist and get response."""
    task = arguments.get("task")
    message = arguments.get("message")
    preferred_speed = arguments.get("preferred_speed")

    if not task or not message:
        raise ValueError("'task' and 'message' are required")

    # Find best specialist
    specialist = specialist_router.get_best_specialist(task, preferred_speed=preferred_speed)
    if not specialist:
        raise ValueError(f"No suitable specialist found for task: {task}")

    # Build messages
    messages = []
    if specialist.system_prompt_template:
        messages.append({"role": "system", "content": specialist.system_prompt_template})
    messages.append({"role": "user", "content": message})

    # Invoke the model
    model = await registry.get_model(specialist.id)
    if not model:
        raise ValueError(f"Specialist model '{specialist.id}' not available")

    chunks = []
    async with request_slot():
        async for chunk in chat_service.stream_chat(
            model,
            specialist.id,
            iter(messages),
            stream=True,
        ):
            if chunk:
                chunks.append(chunk)

    return {
        "response": "".join(chunks),
        "specialist": {
            "id": specialist.id,
            "name": specialist.name,
            "provider": specialist.provider,
            "capabilities": [c.value for c in specialist.capabilities]
        },
        "task": task
    }


async def handle_list_specialists(_: Dict[str, Any]) -> Dict[str, Any]:
    """List all available specialists."""
    return {
        "specialists": specialist_router.list_specialists(),
        "count": len(SPECIALISTS)
    }


async def handle_analyze_image(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle image analysis."""
    from ..services import vision

    image_url = arguments.get("image_url")
    prompt = arguments.get("prompt", "Describe this image in detail")
    model_id = arguments.get("model", "gemini/gemini-2.0-flash")

    if not image_url:
        raise ValueError("'image_url' is required")

    model = await registry.get_model(model_id)
    if not model or "vision" not in model.capabilities:
        raise ValueError(f"Model '{model_id}' not found or does not support vision")

    result = await vision.analyze_from_url(model, model_id, image_url, prompt)
    return {"analysis": result, "model": model_id}


async def handle_crawl_url(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle single URL crawl."""
    url = arguments.get("url")
    if not url:
        raise ValueError("'url' is required")

    keywords = arguments.get("keywords")
    max_pages = arguments.get("max_pages", 10)

    job = await get_user_crawler().crawl_url(
        url=url,
        keywords=list(keywords) if keywords else None,
        max_pages=int(max_pages),
    )
    return {"job": _serialize_job(job), "message": f"Crawl job started for {url}"}


async def handle_crawl_site(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle site crawl with depth."""
    site_url = arguments.get("site_url")
    if not site_url:
        raise ValueError("'site_url' is required")

    seeds = arguments.get("seeds") or [site_url]
    keywords = arguments.get("keywords") or []
    max_depth = arguments.get("max_depth", 2)
    max_pages = arguments.get("max_pages", 40)

    job = await crawler_manager.create_job(
        keywords=list(keywords) if keywords else [site_url],
        seeds=[str(seed) for seed in seeds],
        max_depth=int(max_depth),
        max_pages=int(max_pages),
        allow_external=False,
        relevance_threshold=0.35,
        requested_by="mcp-remote",
        priority="low",
    )
    return {"job": _serialize_job(job), "message": f"Deep crawl started for {site_url}"}


async def handle_crawl_status(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get crawler job status."""
    job_id = arguments.get("job_id")
    if not job_id:
        raise ValueError("'job_id' is required")

    include_results = arguments.get("include_results", False)

    # Try user crawler first
    _uc = get_user_crawler()
    job = await _uc.get_job(job_id)
    source = "user"
    manager = _uc
    if not job:
        job = await crawler_manager.get_job(job_id)
        source = "manager"
        manager = crawler_manager
    if not job:
        raise ValueError(f"Job '{job_id}' not found")

    result = _serialize_job(job)
    result["source"] = source

    if include_results:
        results = []
        for result_id in job.results[:10]:  # Limit to 10
            r = await manager.get_result(result_id)
            if r:
                results.append({
                    "url": r.url,
                    "title": r.title,
                    "excerpt": r.excerpt[:200] if r.excerpt else None,
                    "score": r.score
                })
        result["results"] = results

    return result


async def handle_create_post(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Create WordPress post."""
    title = arguments.get("title")
    content = arguments.get("content")
    status = arguments.get("status", "draft")

    if not title or not content:
        raise ValueError("'title' and 'content' are required")

    result = await wordpress_service.create_post(
        title=title,
        content=content,
        status=status,
    )
    return result


async def handle_conversation(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Manage multi-turn conversations."""
    action = arguments.get("action", "add")
    session_id = arguments.get("session_id")
    message = arguments.get("message")
    model = arguments.get("model")

    if action == "list":
        return {"contexts": context_manager.list_contexts()}

    if action == "get":
        if not session_id:
            raise ValueError("'session_id' required for get action")
        context = context_manager.get_context(session_id)
        if not context:
            raise ValueError(f"Context '{session_id}' not found")
        return context.to_dict()

    if action == "clear":
        if not session_id:
            raise ValueError("'session_id' required for clear action")
        context = context_manager.get_context(session_id)
        if not context:
            raise ValueError(f"Context '{session_id}' not found")
        context.clear()
        return {"session_id": session_id, "cleared": True}

    if action == "add":
        if not message:
            raise ValueError("'message' required for add action")

        context = context_manager.get_or_create_context(session_id or "default")
        context.add_user_message(message)

        result: Dict[str, Any] = {
            "session_id": context.session_id,
            "message_added": True
        }

        # Get AI response if model specified
        if model:
            model_obj = await registry.get_model(model)
            if model_obj:
                messages = context.get_messages_for_api()
                chunks = []
                async with request_slot():
                    async for chunk in chat_service.stream_chat(
                        model_obj, model, iter(messages), stream=True
                    ):
                        if chunk:
                            chunks.append(chunk)
                response = "".join(chunks)
                context.add_assistant_message(response)
                result["response"] = response
                result["model"] = model

        result["context_summary"] = context.get_summary()
        return result

    raise ValueError(f"Unknown action: {action}")


async def handle_prompt_template(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle prompt template operations."""
    action = arguments.get("action", "list")
    name = arguments.get("name")
    variables = arguments.get("variables", {})

    if action == "list":
        templates = prompt_library.list_templates()
        return {
            "templates": [
                prompt_library.get_template_info(t)
                for t in templates
            ]
        }

    if action == "render":
        if not name:
            raise ValueError("'name' required for render action")
        rendered = prompt_library.render(name, **variables)
        return {"name": name, "rendered": rendered}

    if action == "add":
        template = arguments.get("template")
        if not name or not template:
            raise ValueError("'name' and 'template' required for add action")
        prompt_library.add_template(name, template)
        return {"name": name, "added": True}

    raise ValueError(f"Unknown action: {action}")


async def handle_api_docs(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get API documentation."""
    from ..mcp.api_docs import get_endpoint_for_task

    section = arguments.get("section")
    search = arguments.get("search")

    if search:
        endpoints = get_endpoint_for_task(search)
        return {
            "search": search,
            "results": [
                {
                    "path": ep.path,
                    "method": ep.method.value,
                    "summary": ep.summary,
                    "mcp_method": ep.mcp_method
                }
                for ep in endpoints
            ]
        }

    return get_api_docs(section)


async def handle_web_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle web search."""
    from ..services import web_search

    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")

    results = await web_search.search_web(query)
    return {"results": results, "query": query}


# ============================================================================
# TriStar & Codebase Tool Handlers (v2.80)
# ============================================================================

async def handle_tristar_models(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get TriStar models."""
    from ..routes.mcp import handle_tristar_models as _handler
    return await _handler(arguments)


async def handle_tristar_init(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Initialize a TriStar model."""
    from ..routes.mcp import handle_tristar_init as _handler
    return await _handler(arguments)


async def handle_tristar_memory_store(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Store memory entry."""
    from ..routes.mcp import handle_tristar_memory_store as _handler
    return await _handler(arguments)


async def handle_tristar_memory_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search memory."""
    from ..routes.mcp import handle_tristar_memory_search as _handler
    return await _handler(arguments)


async def handle_codebase_structure(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get codebase structure."""
    from ..routes.mcp import handle_codebase_structure as _handler
    return await _handler(arguments)


async def handle_codebase_file(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Read codebase file."""
    from ..routes.mcp import handle_codebase_file as _handler
    return await _handler(arguments)


async def handle_codebase_search(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search codebase."""
    from ..routes.mcp import handle_codebase_search as _handler
    return await _handler(arguments)


async def handle_codebase_routes(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get codebase routes."""
    from ..routes.mcp import handle_codebase_routes as _handler
    return await _handler(arguments)


async def handle_codebase_services(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get codebase services."""
    from ..routes.mcp import handle_codebase_services as _handler
    return await _handler(arguments)


# ============================================================================
# CLI Agent Tool Handlers (v2.80)
# ============================================================================

async def handle_cli_agents_list(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List CLI agents."""
    from ..routes.mcp import handle_cli_agents_list as _handler
    return await _handler(arguments)


async def handle_cli_agents_get(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get CLI agent."""
    from ..routes.mcp import handle_cli_agents_get as _handler
    return await _handler(arguments)


async def handle_cli_agents_start(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Start CLI agent."""
    from ..routes.mcp import handle_cli_agents_start as _handler
    return await _handler(arguments)


async def handle_cli_agents_stop(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Stop CLI agent."""
    from ..routes.mcp import handle_cli_agents_stop as _handler
    return await _handler(arguments)


async def handle_cli_agents_restart(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Restart CLI agent."""
    from ..routes.mcp import handle_cli_agents_restart as _handler
    return await _handler(arguments)


async def handle_cli_agents_call(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Call CLI agent."""
    from ..routes.mcp import handle_cli_agents_call as _handler
    return await _handler(arguments)


async def handle_cli_agents_broadcast(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Broadcast to CLI agents."""
    from ..routes.mcp import handle_cli_agents_broadcast as _handler
    return await _handler(arguments)


async def handle_cli_agents_output(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get CLI agent output."""
    from ..routes.mcp import handle_cli_agents_output as _handler
    return await _handler(arguments)


async def handle_cli_agents_stats(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get CLI agent stats."""
    from ..routes.mcp import handle_cli_agents_stats as _handler
    return await _handler(arguments)



# =================================================================
# Extended Search Tool Handlers (v4.0)
# =================================================================

async def handle_multi_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Extended Multi-API Search with all providers."""
    from ..services.multi_search import multi_search_extended
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    return await multi_search_extended(
        query=query,
        max_results=arguments.get("max_results", 50),
        lang=arguments.get("lang", "de"),
        use_searxng=arguments.get("use_searxng", True),
        use_ddg=arguments.get("use_ddg", True),
        use_wiby=arguments.get("use_wiby", True),
        use_wikipedia=arguments.get("use_wikipedia", True),
        use_grokipedia=arguments.get("use_grokipedia", True),
        use_ailinux_news=arguments.get("use_ailinux_news", True),
    )


async def handle_smart_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """AI-Powered Smart Search with LLM enhancement."""
    from ..services.multi_search import smart_search
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    return await smart_search(
        query=query,
        max_results=arguments.get("max_results", 30),
        lang=arguments.get("lang", "de"),
        expand_query_enabled=arguments.get("expand_query", True),
        detect_intent_enabled=arguments.get("detect_intent", True),
        summarize_enabled=arguments.get("summarize", True),
        smart_rank_enabled=arguments.get("smart_rank", True),
    )


async def handle_quick_smart_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Quick Smart Search - Speed optimized <500ms."""
    from ..services.multi_search import quick_smart_search
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    return await quick_smart_search(
        query=query,
        max_results=arguments.get("max_results", 15),
        lang=arguments.get("lang", "de"),
    )


async def handle_google_deep_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Deep Google search with up to 150 results."""
    from ..services.multi_search import google_search_deep
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    results = await google_search_deep(
        query=query,
        num_results=arguments.get("num_results", 150),
        lang=arguments.get("lang", "de"),
    )
    return {"query": query, "results": results, "count": len(results)}


async def handle_ailinux_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search AILinux.me News Archive."""
    from ..services.multi_search import search_ailinux
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    results = await search_ailinux(query, arguments.get("num_results", 20))
    return {"query": query, "results": results, "count": len(results), "source": "ailinux.me"}


async def handle_grokipedia_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Search Grokipedia.com - xAI knowledge base."""
    from ..services.multi_search import search_grokipedia
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    results = await search_grokipedia(query, arguments.get("num_results", 5))
    return {"query": query, "results": results, "count": len(results), "source": "grokipedia.com"}

async def handle_image_search_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Bildersuche via SearXNG."""
    from ..services.multi_search import image_search
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    return await image_search(query, arguments.get("num_results", 30), arguments.get("lang", "de"))


async def handle_search_health_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Check health of all search providers."""
    from ..services.multi_search import check_search_health
    health = await check_search_health()
    return {"providers": health, "status": "ok" if health.get("all_healthy") else "degraded"}


async def handle_weather_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get current weather from Open-Meteo API."""
    from ..services.multi_search import get_weather
    return await get_weather(
        lat=arguments.get("lat", 52.28),
        lon=arguments.get("lon", 7.44),
        location=arguments.get("location", "Rheine"),
    )


async def handle_crypto_prices_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get crypto prices from CoinGecko."""
    from ..services.multi_search import get_crypto_prices
    coins = arguments.get("coins", ["bitcoin", "ethereum", "solana"])
    return await get_crypto_prices(coins)


async def handle_stock_indices_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get stock indices from Yahoo Finance."""
    from ..services.multi_search import get_stock_indices
    return await get_stock_indices()


async def handle_market_overview_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Combined market data: crypto + stocks."""
    from ..services.multi_search import get_market_overview
    return await get_market_overview()


async def handle_current_time_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Get current time with timezone."""
    from ..services.multi_search import get_current_time
    return await get_current_time(
        timezone=arguments.get("timezone", "Europe/Berlin"),
        location=arguments.get("location"),
    )


async def handle_list_timezones_remote(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """List available timezones."""
    from ..services.multi_search import list_timezones
    return await list_timezones(arguments.get("region"))



TOOL_HANDLERS = {
    # Core
    "chat": handle_chat,
    "list_models": handle_list_models,

    # Specialists
    "ask_specialist": handle_ask_specialist,
    "list_specialists": handle_list_specialists,

    # Vision
    "analyze_image": handle_analyze_image,

    # Crawling
    "crawl_url": handle_crawl_url,
    "crawl_site": handle_crawl_site,
    "crawl_status": handle_crawl_status,

    # WordPress
    "create_post": handle_create_post,

    # Context & Prompts
    "conversation": handle_conversation,
    "prompt_template": handle_prompt_template,

    # Docs
    "api_docs": handle_api_docs,

    # Search
    "web_search": handle_web_search,

    # TriStar Integration (v2.80)
    "tristar_models": handle_tristar_models,
    "tristar_init": handle_tristar_init,
    "tristar_memory_store": handle_tristar_memory_store,
    "tristar_memory_search": handle_tristar_memory_search,

    # Codebase Access (v2.80)
    "codebase_structure": handle_codebase_structure,
    "codebase_file": handle_codebase_file,
    "codebase_search": handle_codebase_search,
    "codebase_routes": handle_codebase_routes,
    "codebase_services": handle_codebase_services,

    # CLI Agents (v2.80)
    "cli-agents_list": handle_cli_agents_list,
    "cli-agents_get": handle_cli_agents_get,
    "cli-agents_start": handle_cli_agents_start,
    "cli-agents_stop": handle_cli_agents_stop,
    "cli-agents_restart": handle_cli_agents_restart,
    "cli-agents_call": handle_cli_agents_call,
    "cli-agents_broadcast": handle_cli_agents_broadcast,
    "cli-agents_output": handle_cli_agents_output,
    "cli-agents_stats": handle_cli_agents_stats,

    # Ollama Local LLM Tools (v2.80)
    **OLLAMA_HANDLERS,

    # TriStar System Management Tools (v2.80)
    **TRISTAR_HANDLERS,

    # Gemini Access Point Tools (v2.80)
    **GEMINI_ACCESS_HANDLERS,

    # Command Queue Tools (v2.80)
    **QUEUE_HANDLERS,

    # Hugging Face Inference Tools (v2.80)
    **HF_HANDLERS,

    # Adaptive Code Illumination Tools (v2.80)
    **ADAPTIVE_CODE_HANDLERS,

    # Extended Search Tools (v4.0)
    "multi_search": handle_multi_search_remote,
    "smart_search": handle_smart_search_remote,
    "quick_smart_search": handle_quick_smart_search_remote,
    "google_deep_search": handle_google_deep_search_remote,
    "ailinux_search": handle_ailinux_search_remote,
    "grokipedia_search": handle_grokipedia_search_remote,
    "image_search": handle_image_search_remote,
    "search_health": handle_search_health_remote,

    # Widget Tools
    "weather": handle_weather_remote,
    "crypto_prices": handle_crypto_prices_remote,
    "stock_indices": handle_stock_indices_remote,
    "market_overview": handle_market_overview_remote,
    "current_time": handle_current_time_remote,
    "list_timezones": handle_list_timezones_remote,
}

# Structured Admin Tools
TOOL_HANDLERS.update(STRUCTURED_ADMIN_HANDLERS)


# ============================================================================
# MCP Protocol Endpoints
# ============================================================================

@router.get("/.well-known/mcp.json")
@router.get("/v1/mcp/.well-known/mcp.json")
async def mcp_discovery(request: Request):
    """
    MCP server discovery endpoint.
    NO AUTH REQUIRED - this is a public discovery endpoint.
    Standard URL: /v1/mcp (localhost:9100/v1/mcp or api.ailinux.me/v1/mcp)
    """
    # Build base URL from request
    raw_base = str(request.base_url).rstrip("/")

    # Remove any path suffixes to get root
    if "/v1/mcp" in raw_base:
        base_url = raw_base.split("/v1/mcp")[0]
    elif "/mcp" in raw_base:
        base_url = raw_base.split("/mcp")[0]
    else:
        base_url = raw_base

    # Force HTTPS for external domains (not localhost)
    if "localhost" not in base_url and "127.0.0.1" not in base_url:
        base_url = base_url.replace("http://", "https://")

    # Standard MCP endpoint is /v1/mcp
    mcp_url = f"{base_url}/v1/mcp"

    return {
        "mcp_version": "2025-11-25",
        "server": MCP_SERVER_INFO,
        "capabilities": MCP_CAPABILITIES,
        "endpoints": {
            "mcp": mcp_url,
            "sse": f"{mcp_url}/sse",
            "rpc": mcp_url
        },
        # Simple HTTP Auth - NO OAuth redirect, NO login form!
        "authentication": {
            "type": "http",
            "scheme": "basic",
            "description": "Use Basic Auth with username:password from .env (MCP_OAUTH_USER:MCP_OAUTH_PASS)"
        }
    }


@router.get("/_legacy_mcp_disabled")
@router.get("/_legacy_mcp_disabled/")
@router.get("/_legacy_mcp_disabled/sse")
@router.get("/_legacy_mcp_disabled/sse-alias")
async def mcp_sse_endpoint(request: Request):
    """
    SSE endpoint for MCP communication.
    Claude.ai connects here to establish a session.
    Supports both /mcp and /mcp/ paths.
    """
    await require_mcp_auth(request)

    async def event_generator():
        # Send initial connection message
        session_id = str(uuid.uuid4())

        # Send server info
        init_message = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {
                "protocolVersion": "2024-11-05",
                "serverInfo": MCP_SERVER_INFO,
                "capabilities": {
                    "tools": {"listChanged": False}
                }
            }
        }
        yield f"data: {json.dumps(init_message)}\n\n"

        # Keep connection alive with heartbeat
        import asyncio
        try:
            while True:
                await asyncio.sleep(30)
                yield f": heartbeat\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# Store active sessions for Streamable HTTP
_mcp_sessions: Dict[str, datetime] = {}


@router.post("/_legacy_mcp_disabled")
@router.post("/_legacy_mcp_disabled/")
async def mcp_rpc_endpoint(request: Request):
    """
    JSON-RPC endpoint for MCP tool calls.
    Supports both regular JSON-RPC and Streamable HTTP transport.
    Supports both /mcp and /mcp/ paths.
    """
    import time as _time
    import logging
    from ..utils.triforce_logging import multi_logger

    # Enforce authentication before processing request body
    await require_mcp_auth(request)

    _log = logging.getLogger("ailinux.mcp.remote")
    start_time = _time.time()
    method = None
    params = None

    # Check for session header (Streamable HTTP)
    session_id = request.headers.get("mcp-session-id")

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            }
        )

    jsonrpc = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")
    # Log the incoming MCP request
    _log.info(f"MCP request: method={method}")

    if jsonrpc != "2.0":
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": req_id
            }
        )

    # Generate session ID for new connections
    response_headers = {}
    if not session_id:
        session_id = str(uuid.uuid4())
        _mcp_sessions[session_id] = datetime.now(timezone.utc)
        response_headers["mcp-session-id"] = session_id

    # Handle MCP methods
    if method == "initialize":
        # Client protocol version (for compatibility checking)
        client_protocol = params.get("protocolVersion", "2024-11-05")
        client_info = params.get("clientInfo", {})

        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": MCP_SERVER_INFO,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "prompts": {"listChanged": False},
                        "resources": {"listChanged": False},
                    }
                },
                "id": req_id
            },
            headers={
                **response_headers,
                "Content-Type": "application/json",
            }
        )

    elif method == "tools/list":
        tools_result = get_tools()

        latency_ms = (_time.time() - start_time) * 1000
        await multi_logger.log_mcp(method, params, {"tools_count": len(tools_result)}, latency_ms)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {"tools": tools_result},
                "id": req_id
            },
            headers=response_headers
        )

    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                    "id": req_id
                },
                headers=response_headers
            )

        try:
            result = await handler(arguments)
            latency_ms = (_time.time() - start_time) * 1000
            await multi_logger.log_mcp(f"tools/call:{tool_name}", arguments, result, latency_ms)
            # v2.82: Dedicated tool call logging
            await multi_logger.log_mcp_tool_call(
                tool_name=tool_name,
                params=arguments,
                result_status="success",
                latency_ms=latency_ms,
                caller="mcp_remote",
                result_preview=str(result)[:300] if result else None
            )
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [
                            {"type": "text", "text": json.dumps(result, separators=(',', ':'))}
                        ],
                        "isError": False
                    },
                    "id": req_id
                },
                headers=response_headers
            )
        except Exception as exc:
            latency_ms = (_time.time() - start_time) * 1000
            await multi_logger.log_mcp(f"tools/call:{tool_name}", arguments, None, latency_ms, str(exc))
            # v2.82: Log failed tool calls
            await multi_logger.log_mcp_tool_call(
                tool_name=tool_name,
                params=arguments,
                result_status="error",
                latency_ms=latency_ms,
                caller="mcp_remote",
                error=str(exc)
            )
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "result": {
                        "content": [
                            {"type": "text", "text": f"Error: {str(exc)}"}
                        ],
                        "isError": True
                    },
                    "id": req_id
                },
                headers=response_headers
            )

    elif method == "prompts/list":
        # Return empty prompts list (standard MCP protocol)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {"prompts": []},
                "id": req_id
            },
            headers=response_headers
        )

    elif method == "prompts/get":
        # Return prompt not found (we don't have static prompts)
        prompt_name = params.get("name", "unknown")
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Prompt '{prompt_name}' not found"},
                "id": req_id
            },
            headers=response_headers
        )

    elif method == "resources/list":
        # Return empty resources list (standard MCP protocol)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {"resources": []},
                "id": req_id
            },
            headers=response_headers
        )

    elif method == "resources/read":
        # Return resource not found
        uri = params.get("uri", "unknown")
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Resource '{uri}' not found"},
                "id": req_id
            },
            headers=response_headers
        )

    elif method == "ping":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {},
                "id": req_id
            },
            headers=response_headers
        )

    # Handle notifications (no response required for JSON-RPC notifications)
    elif method and method.startswith("notifications/"):
        # Notifications don't have an id and don't expect a response
        # Return 202 Accepted for Streamable HTTP Transport compatibility
        # This is required for Codex/Claude CLI MCP clients
        return Response(
            status_code=202,
            headers=response_headers
        )

    else:
        # Return proper JSON-RPC error without 404 status (which can cause issues)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method '{method}' not found"},
                "id": req_id
            },
            headers=response_headers
        )


# ============================================================================
# Direct Agent MCP Routes (/mcp/claude, /mcp/codex, /mcp/gemini)
# ============================================================================

async def _handle_agent_mcp_call(agent_id: str, request: Request):
    """
    Handle MCP JSON-RPC calls routed to a specific CLI agent.
    Starts the agent if not running, then forwards the message.
    """
    await require_mcp_auth(request)
    from ..services.tristar.agent_controller import agent_controller

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None
            }
        )

    jsonrpc = body.get("jsonrpc")
    method = body.get("method")
    params = body.get("params", {})
    req_id = body.get("id")

    if jsonrpc != "2.0":
        return JSONResponse(
            status_code=400,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": req_id
            }
        )

    # Get agent info
    agent = await agent_controller.get_agent(agent_id)
    if not agent:
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": f"Agent '{agent_id}' not found"},
                "id": req_id
            }
        )

    # Handle agent-specific methods
    if method == "initialize":
        # Return agent info as server info
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {
                        "name": agent.get("name", agent_id),
                        "version": "2.80",
                        "description": f"Direct MCP access to {agent_id} CLI agent"
                    },
                    "capabilities": {
                        "tools": {"listChanged": False}
                    },
                    "agent": agent
                },
                "id": req_id
            }
        )

    elif method == "agent/status":
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": agent,
                "id": req_id
            }
        )

    elif method == "agent/start":
        result = await agent_controller.start_agent(agent_id)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id
            }
        )

    elif method == "agent/stop":
        force = params.get("force", False)
        result = await agent_controller.stop_agent(agent_id, force=force)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id
            }
        )

    elif method == "agent/call":
        message = params.get("message")
        if not message:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32602, "message": "Missing 'message' parameter"},
                    "id": req_id
                }
            )

        timeout = params.get("timeout", 120)
        result = await agent_controller.call_agent(agent_id, message, timeout=timeout)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": result,
                "id": req_id
            }
        )

    elif method == "agent/output":
        lines = params.get("lines", 50)
        output = await agent_controller.get_agent_output(agent_id, lines)
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {"agent_id": agent_id, "output": output, "lines": len(output)},
                "id": req_id
            }
        )

    elif method == "tools/list":
        # Return available methods for this agent
        tools = [
            {
                "name": "agent/status",
                "description": f"Get status of {agent_id} agent",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "agent/start",
                "description": f"Start {agent_id} agent subprocess",
                "inputSchema": {"type": "object", "properties": {}}
            },
            {
                "name": "agent/stop",
                "description": f"Stop {agent_id} agent subprocess",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "force": {"type": "boolean", "default": False}
                    }
                }
            },
            {
                "name": "agent/call",
                "description": f"Send a message to {agent_id} agent",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {"type": "string", "description": "Message to send"},
                        "timeout": {"type": "integer", "default": 120}
                    },
                    "required": ["message"]
                }
            },
            {
                "name": "agent/output",
                "description": f"Get output buffer from {agent_id} agent",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "lines": {"type": "integer", "default": 50}
                    }
                }
            }
        ]
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {"tools": tools},
                "id": req_id
            }
        )

    elif method == "tools/call":
        # Route to appropriate handler
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "agent/status":
            result = agent
        elif tool_name == "agent/start":
            result = await agent_controller.start_agent(agent_id)
        elif tool_name == "agent/stop":
            result = await agent_controller.stop_agent(agent_id, force=arguments.get("force", False))
        elif tool_name == "agent/call":
            message = arguments.get("message")
            if not message:
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "error": {"code": -32602, "message": "Missing 'message' argument"},
                        "id": req_id
                    }
                )
            result = await agent_controller.call_agent(agent_id, message, timeout=arguments.get("timeout", 120))
        elif tool_name == "agent/output":
            output = await agent_controller.get_agent_output(agent_id, arguments.get("lines", 50))
            result = {"agent_id": agent_id, "output": output, "lines": len(output)}
        else:
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Tool '{tool_name}' not found"},
                    "id": req_id
                }
            )

        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, indent=2, ensure_ascii=False)}
                    ],
                    "isError": False
                },
                "id": req_id
            }
        )

    else:
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method '{method}' not found for agent {agent_id}"},
                "id": req_id
            }
        )


@router.post("/mcp/claude")
@router.post("/mcp/claude/")
async def mcp_claude_endpoint(request: Request):
    """
    Direct MCP endpoint for Claude Code agent.
    Bypasses routing for direct agent communication.

    Methods:
    - initialize: Get agent info
    - agent/status: Get agent status
    - agent/start: Start agent subprocess
    - agent/stop: Stop agent subprocess
    - agent/call: Send message to agent
    - agent/output: Get output buffer
    - tools/list: List available tools
    - tools/call: Call a tool
    """
    return await _handle_agent_mcp_call("claude-mcp", request)


@router.post("/mcp/codex")
@router.post("/mcp/codex/")
async def mcp_codex_endpoint(request: Request):
    """
    Direct MCP endpoint for Codex agent.
    Bypasses routing for direct agent communication.
    """
    return await _handle_agent_mcp_call("codex-mcp", request)


@router.post("/mcp/gemini")
@router.post("/mcp/gemini/")
async def mcp_gemini_endpoint(request: Request):
    """
    Direct MCP endpoint for Gemini agent.
    Bypasses routing for direct agent communication.
    """
    return await _handle_agent_mcp_call("gemini-mcp", request)


@router.get("/mcp/claude")
@router.get("/mcp/claude/")
async def mcp_claude_info(request: Request):
    """Get Claude agent info."""
    await require_mcp_auth(request)
    from ..services.tristar.agent_controller import agent_controller
    agent = await agent_controller.get_agent("claude-mcp")
    return {
        "agent": agent,
        "endpoints": {
            "post": "/mcp/claude",
            "methods": ["initialize", "agent/status", "agent/start", "agent/stop", "agent/call", "agent/output", "tools/list", "tools/call"]
        }
    }


@router.get("/mcp/codex")
@router.get("/mcp/codex/")
async def mcp_codex_info(request: Request):
    """Get Codex agent info."""
    await require_mcp_auth(request)
    from ..services.tristar.agent_controller import agent_controller
    agent = await agent_controller.get_agent("codex-mcp")
    return {
        "agent": agent,
        "endpoints": {
            "post": "/mcp/codex",
            "methods": ["initialize", "agent/status", "agent/start", "agent/stop", "agent/call", "agent/output", "tools/list", "tools/call"]
        }
    }


@router.get("/mcp/gemini")
@router.get("/mcp/gemini/")
async def mcp_gemini_info(request: Request):
    """Get Gemini agent info."""
    await require_mcp_auth(request)
    from ..services.tristar.agent_controller import agent_controller
    agent = await agent_controller.get_agent("gemini-mcp")
    return {
        "agent": agent,
        "endpoints": {
            "post": "/mcp/gemini",
            "methods": ["initialize", "agent/status", "agent/start", "agent/stop", "agent/call", "agent/output", "tools/list", "tools/call"]
        }
    }


# ============================================================================
# OAuth Endpoints - REMOVED (now in oauth_service.py)
# ============================================================================
# All OAuth endpoints (/authorize, /token, /.well-known/*) are now
# centralized in app/routes/oauth_service.py


# ============================================================================
# robots.txt - Allow all crawlers (required for Claude.ai MCP integration)
# ============================================================================

@router.get("/robots.txt")
async def robots_txt(request: Request):
    """
    robots.txt allowing all crawlers.
    Required for Claude.ai custom connector integration.
    """
    await require_mcp_auth(request)
    return Response(
        content="User-agent: *\nAllow: /\n",
        media_type="text/plain"
    )
