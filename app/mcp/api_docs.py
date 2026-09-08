"""
API Documentation for MCP Server v2.80

Provides comprehensive API documentation that Claude can query
to understand how to interact with the AILinux backend.

Includes:
- TriStar Multi-LLM Orchestration
- TriForce Agent Mesh Network
- CLI Agent Management (Claude, Codex, Gemini)
- Codebase Access for Self-Development
- MCP Protocol Integration
- Gemini Function Calling & Code Execution (v2.80 NEW)
- Hugging Face Inference API (v2.80 NEW)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

from app.config import VERSION


class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"


@dataclass
class Parameter:
    name: str
    type: str
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[str]] = None
    example: Any = None


@dataclass
class Endpoint:
    path: str
    method: HTTPMethod
    summary: str
    description: str
    parameters: List[Parameter] = field(default_factory=list)
    request_body: Optional[Dict[str, Any]] = None
    response_example: Optional[Dict[str, Any]] = None
    tags: List[str] = field(default_factory=list)
    mcp_method: Optional[str] = None  # Corresponding MCP method if available


# =============================================================================
# Complete API Documentation
# =============================================================================

API_DOCUMENTATION: Dict[str, Any] = {
    "info": {
        "title": "AILinux AI Backend API",
        "version": VERSION,
        "description": f"""
AILinux AI Server Backend v{VERSION} - TriStar/TriForce Release

CORE CAPABILITIES:
- Multi-provider LLM chat (Ollama, Gemini, Mistral, Anthropic, GPT-OSS)
- Vision analysis with image understanding
- Text-to-image generation via ComfyUI
- Web crawling with intelligent content discovery
- WordPress content publishing

TRISTAR ORCHESTRATION (v2.80):
- 113 LLM models registered with roles (admin, lead, worker, reviewer)
- Chain execution with autoprompt profiles
- Shared memory with confidence scoring
- Model initialization (Impfung) with system prompts

TRIFORCE MESH NETWORK:
- 16 LLM mesh for coordinated multi-model responses
- RBAC with 20 permissions across 5 roles
- Circuit breaker with automatic failover
- Audit logging with WebSocket streaming

CLI AGENTS (Claude, Codex, Gemini):
- Subprocess management via /tristar/cli-agents
- Direct MCP access via /mcp/claude, /mcp/codex, /mcp/gemini
- System prompt injection from TriForce
- Auto-restart and health monitoring

CODEBASE ACCESS:
- Full backend source code readable via MCP
- Route inspection for API discovery
- Service module analysis for architecture understanding

GEMINI FUNCTION CALLING (v2.80 NEW):
- Native Tool-Ausführung durch Gemini GenAI SDK
- Automatische Konvertierung von TriForce Tools zu Gemini Functions
- Auto-Execute mit Fallback-Modus
- Sandbox Code Execution via Gemini 2.0

HUGGING FACE INFERENCE API (v2.80 NEW):
- Text Generation (Llama 3.2, Mistral 7B, Qwen 2.5)
- Chat Completion (OpenAI-kompatibel)
- Embeddings (sentence-transformers, BGE)
- Text-to-Image (FLUX.1, Stable Diffusion)
- Summarization & Translation

Base URL: https://api.ailinux.me
Internal Port: 9100 | External Port: 443 (via nginx)
        """,
        "contact": {
            "name": "AILinux Support",
            "url": "https://ailinux.me"
        }
    },

    "endpoints": {
        # =================================================================
        # Chat Completions
        # =================================================================
        "chat_completions": Endpoint(
            path="/v1/chat/completions",
            method=HTTPMethod.POST,
            summary="Chat Completions",
            description="""
Send messages to an AI model and receive responses.
Supports streaming and non-streaming modes.
Automatically routes to the best provider based on model selection.
            """,
            parameters=[],
            request_body={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model ID (e.g., 'ollama/llama3.2', 'gemini/gemini-2.0-flash', 'anthropic/claude-sonnet-4')",
                        "examples": [
                            "ollama/llama3.2",
                            "gemini/gemini-2.0-flash",
                            "mistral/large",
                            "anthropic/claude-sonnet-4",
                            "gpt-oss:20b-cloud"
                        ]
                    },
                    "messages": {
                        "type": "array",
                        "description": "Array of message objects with role and content",
                        "items": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                "content": {"type": "string"}
                            }
                        }
                    },
                    "stream": {
                        "type": "boolean",
                        "description": "Enable streaming response",
                        "default": False
                    },
                    "temperature": {
                        "type": "number",
                        "description": "Sampling temperature (0.0-2.0)",
                        "default": 0.7
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Maximum tokens to generate"
                    }
                },
                "required": ["model", "messages"]
            },
            response_example={
                "id": "chatcmpl-abc123",
                "object": "chat.completion",
                "model": "ollama/llama3.2",
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "Hello! How can I help you today?"
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 8,
                    "total_tokens": 18
                }
            },
            tags=["Chat", "LLM"],
            mcp_method="llm.invoke"
        ),

        # =================================================================
        # OpenAI-Compatible Endpoint
        # =================================================================
        "openai_chat": Endpoint(
            path="/v1/openai/chat/completions",
            method=HTTPMethod.POST,
            summary="OpenAI-Compatible Chat",
            description="""
Drop-in replacement for OpenAI's chat completions API.
Use model aliases configured via OPENAI_MODEL_ALIASES environment variable.
Supports streaming via SSE.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Model name (uses aliases: 'gpt-4' -> 'anthropic/claude-sonnet-4')"
                    },
                    "messages": {"type": "array"},
                    "stream": {"type": "boolean", "default": False},
                    "temperature": {"type": "number"},
                    "max_tokens": {"type": "integer"},
                    "top_p": {"type": "number"},
                    "stop": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["model", "messages"]
            },
            tags=["Chat", "OpenAI"],
            mcp_method="llm.invoke"
        ),

        # =================================================================
        # Vision
        # =================================================================
        "vision_chat": Endpoint(
            path="/v1/vision/chat/completions",
            method=HTTPMethod.POST,
            summary="Vision Chat Completions",
            description="""
Analyze images using vision-capable AI models.
Supports both URL and base64-encoded images.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Vision model (e.g., 'gemini/gemini-2.0-flash', 'anthropic/claude-sonnet-4')"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Question or instruction about the image"
                    },
                    "image_url": {
                        "type": "string",
                        "description": "URL of the image to analyze"
                    },
                    "image_base64": {
                        "type": "string",
                        "description": "Base64-encoded image data (alternative to URL)"
                    }
                },
                "required": ["model", "prompt"]
            },
            tags=["Vision", "LLM"],
            mcp_method="analyze_image"
        ),

        # =================================================================
        # Models
        # =================================================================
        "list_models": Endpoint(
            path="/v1/models",
            method=HTTPMethod.GET,
            summary="List Available Models",
            description="""
Get a list of all available AI models with their capabilities.
Returns models from all configured providers (Ollama, Gemini, Mistral, Anthropic, etc.)
            """,
            response_example={
                "data": [
                    {
                        "id": "ollama/llama3.2",
                        "provider": "ollama",
                        "capabilities": ["chat"],
                        "context_length": 8192
                    },
                    {
                        "id": "gemini/gemini-2.0-flash",
                        "provider": "gemini",
                        "capabilities": ["chat", "vision"],
                        "context_length": 128000
                    }
                ]
            },
            tags=["Models"],
            mcp_method="list_models"
        ),

        # =================================================================
        # Crawler
        # =================================================================
        "crawler_create_job": Endpoint(
            path="/v1/crawler/jobs",
            method=HTTPMethod.POST,
            summary="Create Crawler Job",
            description="""
Start a new web crawling job. Crawls websites with configurable depth,
page limits, and keyword filtering. Results are stored for later retrieval.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keywords for relevance filtering"
                    },
                    "seeds": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Starting URLs to crawl"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum link depth",
                        "default": 2
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to crawl",
                        "default": 60
                    },
                    "allow_external": {
                        "type": "boolean",
                        "description": "Follow links to external domains",
                        "default": False
                    },
                    "relevance_threshold": {
                        "type": "number",
                        "description": "Minimum relevance score (0.0-1.0)",
                        "default": 0.35
                    }
                },
                "required": ["seeds"]
            },
            response_example={
                "id": "job_abc123",
                "status": "running",
                "seeds": ["https://example.com"],
                "pages_crawled": 0,
                "created_at": "2025-01-15T10:00:00Z"
            },
            tags=["Crawler"],
            mcp_method="crawl.site"
        ),

        "crawler_get_job": Endpoint(
            path="/v1/crawler/jobs/{job_id}",
            method=HTTPMethod.GET,
            summary="Get Crawler Job Status",
            description="Get the current status and results of a crawler job.",
            parameters=[
                Parameter(name="job_id", type="string", description="Job ID", required=True)
            ],
            tags=["Crawler"],
            mcp_method="crawl.status"
        ),

        "crawler_results_ready": Endpoint(
            path="/v1/crawler/results/ready",
            method=HTTPMethod.GET,
            summary="Get Ready Results",
            description="Get crawler results that are ready for publishing.",
            parameters=[
                Parameter(name="limit", type="integer", description="Max results to return", default=5)
            ],
            tags=["Crawler"]
        ),

        # =================================================================
        # Text-to-Image
        # =================================================================
        "txt2img": Endpoint(
            path="/v1/txt2img",
            method=HTTPMethod.POST,
            summary="Generate Image from Text",
            description="""
Generate images from text prompts using ComfyUI/Stable Diffusion.
Supports SD 1.5, SDXL, and SD 3.5 workflows.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Text prompt describing the image"
                    },
                    "negative_prompt": {
                        "type": "string",
                        "description": "Negative prompt (what to avoid)"
                    },
                    "width": {
                        "type": "integer",
                        "description": "Image width (64-4096, aligned to 64)",
                        "default": 1024
                    },
                    "height": {
                        "type": "integer",
                        "description": "Image height (64-4096, aligned to 64)",
                        "default": 1024
                    },
                    "workflow_type": {
                        "type": "string",
                        "enum": ["sd15", "sdxl", "sd35"],
                        "description": "Stable Diffusion workflow type",
                        "default": "sdxl"
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of inference steps",
                        "default": 20
                    },
                    "cfg_scale": {
                        "type": "number",
                        "description": "Classifier-free guidance scale",
                        "default": 7.0
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Random seed (-1 for random)"
                    }
                },
                "required": ["prompt"]
            },
            response_example={
                "images": ["base64_encoded_image_data..."],
                "seed": 12345,
                "workflow_type": "sdxl"
            },
            tags=["Image Generation"]
        ),

        # =================================================================
        # WordPress / Posts
        # =================================================================
        "posts_create": Endpoint(
            path="/v1/posts",
            method=HTTPMethod.POST,
            summary="Create WordPress Post",
            description="Create a new post on the connected WordPress site.",
            request_body={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Post title"},
                    "content": {"type": "string", "description": "Post content (HTML)"},
                    "status": {
                        "type": "string",
                        "enum": ["publish", "draft", "pending"],
                        "default": "publish"
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "Category IDs"
                    },
                    "featured_media": {
                        "type": "integer",
                        "description": "Featured image media ID"
                    }
                },
                "required": ["title", "content"]
            },
            tags=["WordPress"],
            mcp_method="posts.create"
        ),

        # =================================================================
        # MCP
        # =================================================================
        "mcp_status": Endpoint(
            path="/v1/mcp/status",
            method=HTTPMethod.GET,
            summary="MCP Server Status",
            description="Health check for MCP subsystem with available methods.",
            response_example={
                "status": "ok",
                "methods": ["crawl.url", "crawl.site", "llm.invoke", "posts.create"],
                "model_count": 15,
                "timestamp": "2025-01-15T10:00:00Z"
            },
            tags=["MCP"]
        ),

        "mcp_jsonrpc": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="MCP JSON-RPC Endpoint",
            description="""
JSON-RPC 2.0 endpoint for MCP method invocation.
Available methods: crawl.url, crawl.site, crawl.status, posts.create,
media.upload, llm.invoke, admin.crawler.control, admin.crawler.config.get/set
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"type": "string", "const": "2.0"},
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                },
                "required": ["jsonrpc", "method"]
            },
            tags=["MCP"]
        ),

        # =================================================================
        # Admin - Crawler Control
        # =================================================================
        "admin_crawler_status": Endpoint(
            path="/v1/admin/crawler/status",
            method=HTTPMethod.GET,
            summary="Crawler Subsystem Status",
            description="Get status of all crawler instances (user, auto, publisher).",
            tags=["Admin", "Crawler"],
            mcp_method="admin.crawler.config.get"
        ),

        "admin_crawler_control": Endpoint(
            path="/v1/admin/crawler/control",
            method=HTTPMethod.POST,
            summary="Control Crawler Instance",
            description="Start, stop, or restart crawler instances.",
            request_body={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["start", "stop", "restart"]},
                    "instance": {"type": "string", "enum": ["user", "auto", "publisher"]}
                },
                "required": ["action", "instance"]
            },
            tags=["Admin", "Crawler"],
            mcp_method="admin.crawler.control"
        ),

        # =================================================================
        # Health
        # =================================================================
        "health": Endpoint(
            path="/healthz",
            method=HTTPMethod.GET,
            summary="Health Check",
            description="Basic health check endpoint.",
            response_example={"status": "ok"},
            tags=["Health"]
        ),

        # =================================================================
        # TriStar Orchestration (v2.80)
        # =================================================================
        "tristar_status": Endpoint(
            path="/v1/tristar/status",
            method=HTTPMethod.GET,
            summary="TriStar System Status",
            description="Get full TriStar system status including chains, projects, agents, and models.",
            response_example={
                "status": "online",
                "version": VERSION,
                "chains": {"total": 5, "running": 1, "completed": 4},
                "projects": {"total": 3},
                "agents": {"total": 16, "enabled": 14},
                "timestamp": "2025-12-04T16:00:00Z"
            },
            tags=["TriStar"],
            mcp_method="tristar.status"
        ),

        "tristar_models": Endpoint(
            path="/v1/tristar/models",
            method=HTTPMethod.GET,
            summary="List TriStar Models",
            description="Get all registered LLM models with roles, capabilities, and init status.",
            parameters=[
                Parameter(name="role", type="string", description="Filter by role (admin, lead, worker, reviewer)", required=False),
                Parameter(name="capability", type="string", description="Filter by capability (code, math, reasoning, vision)", required=False),
                Parameter(name="provider", type="string", description="Filter by provider (ollama, gemini, anthropic, mistral)", required=False),
            ],
            response_example={
                "models": [
                    {"model_id": "gemini-lead", "role": "lead", "capabilities": ["reasoning", "code"], "initialized": True}
                ],
                "count": 113
            },
            tags=["TriStar"],
            mcp_method="tristar.models"
        ),

        "tristar_init": Endpoint(
            path="/v1/tristar/init/{model_id}",
            method=HTTPMethod.POST,
            summary="Initialize (Impfen) a Model",
            description="Initialize a model with system prompt and configuration. Returns init payload to inject.",
            parameters=[
                Parameter(name="model_id", type="string", description="Model ID to initialize", required=True)
            ],
            tags=["TriStar"],
            mcp_method="tristar.init"
        ),

        "tristar_memory_store": Endpoint(
            path="/v1/tristar/memory/store",
            method=HTTPMethod.POST,
            summary="Store Memory Entry",
            description="Store a new memory entry with confidence scoring and TTL.",
            request_body={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Memory content"},
                    "memory_type": {"type": "string", "enum": ["fact", "decision", "code", "summary", "context", "todo"]},
                    "llm_id": {"type": "string", "description": "LLM that created this memory"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "project_id": {"type": "string"},
                    "ttl_seconds": {"type": "integer", "default": 86400}
                },
                "required": ["content"]
            },
            tags=["TriStar", "Memory"],
            mcp_method="tristar.memory.store"
        ),

        "tristar_memory_search": Endpoint(
            path="/v1/tristar/memory/search",
            method=HTTPMethod.POST,
            summary="Search Memory",
            description="Search memory entries with confidence and tag filtering.",
            request_body={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "min_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "project_id": {"type": "string"},
                    "memory_type": {"type": "string"},
                    "limit": {"type": "integer", "default": 20}
                }
            },
            tags=["TriStar", "Memory"],
            mcp_method="tristar.memory.search"
        ),

        "tristar_chain_start": Endpoint(
            path="/v1/tristar/chain/start",
            method=HTTPMethod.POST,
            summary="Start Chain Execution",
            description="Start a new TriStar chain with autoprompt support.",
            request_body={
                "type": "object",
                "properties": {
                    "user_prompt": {"type": "string", "description": "The task/prompt to execute"},
                    "project_id": {"type": "string"},
                    "max_cycles": {"type": "integer", "default": 10},
                    "autoprompt_profile": {"type": "string"},
                    "aggressive": {"type": "boolean", "default": False}
                },
                "required": ["user_prompt"]
            },
            tags=["TriStar", "Chain"]
        ),

        # =================================================================
        # CLI Agents (Claude, Codex, Gemini Subprocess Management)
        # =================================================================
        "cli_agents_list": Endpoint(
            path="/v1/tristar/cli-agents",
            method=HTTPMethod.GET,
            summary="List CLI Agents",
            description="List all CLI agents (Claude, Codex, Gemini) with their status.",
            response_example={
                "cli_agents": [
                    {"agent_id": "claude-mcp", "agent_type": "claude", "status": "running", "pid": 12345},
                    {"agent_id": "codex-mcp", "agent_type": "codex", "status": "stopped"},
                    {"agent_id": "gemini-mcp", "agent_type": "gemini", "status": "stopped"}
                ],
                "count": 3
            },
            tags=["CLI Agents"],
            mcp_method="cli-agents.list"
        ),

        "cli_agents_start": Endpoint(
            path="/v1/tristar/cli-agents/{agent_id}/start",
            method=HTTPMethod.POST,
            summary="Start CLI Agent",
            description="Start a CLI agent subprocess. Fetches system prompt from TriForce automatically.",
            parameters=[
                Parameter(name="agent_id", type="string", description="Agent ID (claude-mcp, codex-mcp, gemini-mcp)", required=True)
            ],
            response_example={
                "status": "started",
                "agent": {"agent_id": "claude-mcp", "status": "running", "pid": 12345}
            },
            tags=["CLI Agents"],
            mcp_method="cli-agents.start"
        ),

        "cli_agents_stop": Endpoint(
            path="/v1/tristar/cli-agents/{agent_id}/stop",
            method=HTTPMethod.POST,
            summary="Stop CLI Agent",
            description="Stop a running CLI agent subprocess.",
            parameters=[
                Parameter(name="agent_id", type="string", description="Agent ID", required=True),
                Parameter(name="force", type="boolean", description="Force kill", required=False, default=False)
            ],
            tags=["CLI Agents"],
            mcp_method="cli-agents.stop"
        ),

        "cli_agents_call": Endpoint(
            path="/v1/tristar/cli-agents/{agent_id}/call",
            method=HTTPMethod.POST,
            summary="Call CLI Agent",
            description="Send a message to a CLI agent and get a response.",
            parameters=[
                Parameter(name="agent_id", type="string", description="Agent ID", required=True)
            ],
            request_body={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to send"},
                    "timeout": {"type": "integer", "default": 120}
                },
                "required": ["message"]
            },
            tags=["CLI Agents"],
            mcp_method="cli-agents.call"
        ),

        "cli_agents_broadcast": Endpoint(
            path="/v1/tristar/cli-agents/broadcast",
            method=HTTPMethod.POST,
            summary="Broadcast to CLI Agents",
            description="Send a message to multiple or all CLI agents.",
            request_body={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to broadcast"},
                    "agent_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific agents (omit for all)"}
                },
                "required": ["message"]
            },
            tags=["CLI Agents"],
            mcp_method="cli-agents.broadcast"
        ),

        # =================================================================
        # Direct Agent MCP Routes (/mcp/claude, /mcp/codex, /mcp/gemini)
        # =================================================================
        "mcp_claude": Endpoint(
            path="/mcp/claude",
            method=HTTPMethod.POST,
            summary="Direct Claude MCP Access",
            description="Direct JSON-RPC endpoint for Claude Code MCP agent. Bypasses routing.",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"type": "string", "const": "2.0"},
                    "method": {"type": "string", "description": "MCP method to call"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                },
                "required": ["jsonrpc", "method"]
            },
            tags=["MCP", "CLI Agents"]
        ),

        "mcp_codex": Endpoint(
            path="/mcp/codex",
            method=HTTPMethod.POST,
            summary="Direct Codex MCP Access",
            description="Direct JSON-RPC endpoint for Codex MCP agent. Bypasses routing.",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"type": "string", "const": "2.0"},
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                },
                "required": ["jsonrpc", "method"]
            },
            tags=["MCP", "CLI Agents"]
        ),

        "mcp_gemini": Endpoint(
            path="/mcp/gemini",
            method=HTTPMethod.POST,
            summary="Direct Gemini MCP Access",
            description="Direct JSON-RPC endpoint for Gemini MCP agent. Bypasses routing.",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"type": "string", "const": "2.0"},
                    "method": {"type": "string"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                },
                "required": ["jsonrpc", "method"]
            },
            tags=["MCP", "CLI Agents"]
        ),

        # =================================================================
        # Codebase Access (Self-Development)
        # =================================================================
        "codebase_structure": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Get Codebase Structure",
            description="Get the backend directory structure. MCP method: codebase.structure",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "codebase.structure"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "default": "app"},
                            "include_files": {"type": "boolean", "default": True},
                            "max_depth": {"type": "integer", "default": 4}
                        }
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Codebase", "MCP"],
            mcp_method="codebase.structure"
        ),

        "codebase_file": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Read Codebase File",
            description="Read a specific file from the backend. MCP method: codebase.file",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "codebase.file"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Relative path (e.g., 'app/routes/mcp.py')"}
                        },
                        "required": ["path"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Codebase", "MCP"],
            mcp_method="codebase.file"
        ),

        "codebase_search": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Search Codebase",
            description="Search for patterns in the backend code. MCP method: codebase.search",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "codebase.search"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search pattern (regex supported)"},
                            "path": {"type": "string", "default": "app"},
                            "file_pattern": {"type": "string", "default": "*.py"},
                            "max_results": {"type": "integer", "default": 50},
                            "context_lines": {"type": "integer", "default": 2}
                        },
                        "required": ["query"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Codebase", "MCP"],
            mcp_method="codebase.search"
        ),

        "codebase_routes": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Get All API Routes",
            description="Get all API routes with HTTP methods and handlers. MCP method: codebase.routes",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "codebase.routes"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Codebase", "MCP"],
            mcp_method="codebase.routes"
        ),

        "codebase_services": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Get Service Modules",
            description="Get all service modules with classes and functions. MCP method: codebase.services",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "codebase.services"},
                    "params": {"type": "object"},
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Codebase", "MCP"],
            mcp_method="codebase.services"
        ),

        # =================================================================
        # TriForce Multi-LLM Mesh
        # =================================================================
        "triforce_status": Endpoint(
            path="/v1/triforce/status",
            method=HTTPMethod.GET,
            summary="TriForce System Status",
            description="Get full TriForce status including LLMs, tools, and RBAC.",
            tags=["TriForce"]
        ),

        "triforce_mesh_call": Endpoint(
            path="/v1/triforce/mesh/call",
            method=HTTPMethod.POST,
            summary="Call Single LLM",
            description="Call a single LLM in the mesh network.",
            request_body={
                "type": "object",
                "properties": {
                    "llm_id": {"type": "string", "description": "LLM ID (gemini, claude, codex, etc.)"},
                    "message": {"type": "string"},
                    "context": {"type": "object"}
                },
                "required": ["llm_id", "message"]
            },
            tags=["TriForce", "Mesh"]
        ),

        "triforce_mesh_broadcast": Endpoint(
            path="/v1/triforce/mesh/broadcast",
            method=HTTPMethod.POST,
            summary="Broadcast to Multiple LLMs",
            description="Send a message to multiple LLMs and collect responses.",
            request_body={
                "type": "object",
                "properties": {
                    "llm_ids": {"type": "array", "items": {"type": "string"}},
                    "message": {"type": "string"}
                },
                "required": ["llm_ids", "message"]
            },
            tags=["TriForce", "Mesh"]
        ),

        "triforce_mesh_consensus": Endpoint(
            path="/v1/triforce/mesh/consensus",
            method=HTTPMethod.POST,
            summary="Get LLM Consensus",
            description="Query multiple LLMs and aggregate responses for consensus.",
            tags=["TriForce", "Mesh"]
        ),

        # =================================================================
        # Gemini Function Calling & Code Execution (v2.80 NEW)
        # =================================================================
        "gemini_function_call": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Gemini Function Calling",
            description="""
Execute Gemini with native function calling capabilities.
Gemini can autonomously call TriForce tools to complete tasks.
Supports auto-execute mode where Gemini decides which tools to use.
Falls back to prompt-based tool detection if GenAI SDK unavailable.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "gemini_function_call"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Task/question for Gemini"},
                            "tools": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "TriForce tools to expose (default: memory, web_search, llm_call, code_exec)"
                            },
                            "auto_execute": {"type": "boolean", "default": True},
                            "max_iterations": {"type": "integer", "default": 5}
                        },
                        "required": ["prompt"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "prompt": "Search memory for architecture decisions",
                "timestamp": "2025-12-05T10:00:00Z",
                "function_calls": [
                    {"name": "memory_recall", "response": {"results": []}, "success": True}
                ],
                "iterations": 2,
                "response": "I found the following architecture decisions...",
                "success": True
            },
            tags=["Gemini", "Function Calling", "MCP"],
            mcp_method="gemini_function_call"
        ),

        "gemini_code_exec": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Gemini Code Execution",
            description="""
Execute Python code via Gemini's native sandbox or local fallback.
Gemini 2.0 supports secure code execution in a sandboxed environment.
If native execution unavailable, falls back to local subprocess execution.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "gemini_code_exec"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "Python code to execute"},
                            "context": {"type": "string", "description": "Optional context/description"},
                            "timeout": {"type": "integer", "default": 30}
                        },
                        "required": ["code"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "code": "print(sum(range(10)))",
                "language": "python",
                "timestamp": "2025-12-05T10:00:00Z",
                "execution_mode": "gemini_native",
                "stdout": "45",
                "outcome": "OUTCOME_OK",
                "success": True
            },
            tags=["Gemini", "Code Execution", "MCP"],
            mcp_method="gemini_code_exec"
        ),

        # =================================================================
        # Hugging Face Inference API (v2.80 NEW)
        # =================================================================
        "hf_generate": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Text Generation",
            description="""
Text generation via Hugging Face Inference API.
Supports Llama 3.2, Mistral 7B, Qwen 2.5, Phi-3 and more.
Free tier with rate limiting and retry logic.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_generate"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Input prompt"},
                            "model": {"type": "string", "default": "meta-llama/Llama-3.2-3B-Instruct"},
                            "max_new_tokens": {"type": "integer", "default": 512},
                            "temperature": {"type": "number", "default": 0.7}
                        },
                        "required": ["prompt"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "generated_text": "Machine learning is a subset of AI...",
                "model": "meta-llama/Llama-3.2-3B-Instruct",
                "usage": {"prompt_tokens": 10, "completion_tokens": 100}
            },
            tags=["Hugging Face", "LLM", "MCP"],
            mcp_method="hf_generate"
        ),

        "hf_chat": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Chat Completion",
            description="""
Chat completion via Hugging Face (OpenAI-compatible format).
Supports multi-turn conversations with system prompts.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_chat"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "messages": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "role": {"type": "string", "enum": ["system", "user", "assistant"]},
                                        "content": {"type": "string"}
                                    }
                                }
                            },
                            "model": {"type": "string", "default": "meta-llama/Llama-3.2-3B-Instruct"},
                            "max_tokens": {"type": "integer", "default": 512},
                            "temperature": {"type": "number", "default": 0.7}
                        },
                        "required": ["messages"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Hugging Face", "Chat", "MCP"],
            mcp_method="hf_chat"
        ),

        "hf_embed": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Embeddings",
            description="""
Generate embeddings via Hugging Face sentence-transformers.
Supports MiniLM, BGE, and multilingual E5 models.
Returns vector representations for semantic search.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_embed"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "texts": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Texts to embed"
                            },
                            "model": {"type": "string", "default": "sentence-transformers/all-MiniLM-L6-v2"}
                        },
                        "required": ["texts"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "embeddings": [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
                "model": "sentence-transformers/all-MiniLM-L6-v2",
                "dimension": 384,
                "count": 2
            },
            tags=["Hugging Face", "Embeddings", "MCP"],
            mcp_method="hf_embed"
        ),

        "hf_image": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Text-to-Image",
            description="""
Generate images from text via Hugging Face.
Supports FLUX.1-schnell, Stable Diffusion XL, and SD 1.5.
Returns base64-encoded PNG image.
            """,
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_image"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "prompt": {"type": "string", "description": "Image description"},
                            "model": {"type": "string", "default": "black-forest-labs/FLUX.1-schnell"},
                            "negative_prompt": {"type": "string"},
                            "width": {"type": "integer", "default": 1024},
                            "height": {"type": "integer", "default": 1024}
                        },
                        "required": ["prompt"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "image_base64": "iVBORw0KGgo...",
                "model": "black-forest-labs/FLUX.1-schnell",
                "prompt": "A futuristic city at sunset",
                "width": 1024,
                "height": 1024,
                "format": "png"
            },
            tags=["Hugging Face", "Image Generation", "MCP"],
            mcp_method="hf_image"
        ),

        "hf_summarize": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Summarization",
            description="Summarize text using BART or Pegasus models.",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_summarize"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Text to summarize"},
                            "model": {"type": "string", "default": "facebook/bart-large-cnn"},
                            "max_length": {"type": "integer", "default": 150}
                        },
                        "required": ["text"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            tags=["Hugging Face", "NLP", "MCP"],
            mcp_method="hf_summarize"
        ),

        "hf_translate": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="Hugging Face Translation",
            description="Translate text using OPUS-MT models (de-en, en-de, etc.).",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_translate"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Text to translate"},
                            "model": {"type": "string", "default": "Helsinki-NLP/opus-mt-de-en"}
                        },
                        "required": ["text"]
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "translation": "Good day",
                "model": "Helsinki-NLP/opus-mt-de-en"
            },
            tags=["Hugging Face", "Translation", "MCP"],
            mcp_method="hf_translate"
        ),

        "hf_models": Endpoint(
            path="/v1/mcp",
            method=HTTPMethod.POST,
            summary="List Hugging Face Models",
            description="List recommended Hugging Face models by task type.",
            request_body={
                "type": "object",
                "properties": {
                    "jsonrpc": {"const": "2.0"},
                    "method": {"const": "hf_models"},
                    "params": {
                        "type": "object",
                        "properties": {
                            "task": {
                                "type": "string",
                                "enum": ["text_generation", "chat", "embeddings", "text_to_image", "summarization", "translation"],
                                "description": "Task type (optional)"
                            }
                        }
                    },
                    "id": {"type": ["string", "integer"]}
                }
            },
            response_example={
                "models": {
                    "text_generation": ["meta-llama/Llama-3.2-3B-Instruct", "mistralai/Mistral-7B-Instruct-v0.3"],
                    "embeddings": ["sentence-transformers/all-MiniLM-L6-v2"],
                    "text_to_image": ["black-forest-labs/FLUX.1-schnell"]
                }
            },
            tags=["Hugging Face", "Models", "MCP"],
            mcp_method="hf_models"
        )
    },

    # =========================================================================
    # MCP Methods Documentation
    # =========================================================================
    "mcp_methods": {
        "crawl.url": {
            "description": "Fast crawl of a single URL using the user crawler (4 workers)",
            "parameters": {
                "url": {"type": "string", "required": True, "description": "URL to crawl"},
                "keywords": {"type": "array", "description": "Optional keywords for filtering"},
                "max_pages": {"type": "integer", "default": 10},
                "idempotency_key": {"type": "string", "description": "Prevent duplicate jobs"}
            },
            "returns": {"job": "Job object with id, status, seeds, etc."},
            "example": {
                "jsonrpc": "2.0",
                "method": "crawl.url",
                "params": {"url": "https://example.com", "keywords": ["AI", "technology"]},
                "id": 1
            }
        },

        "crawl.site": {
            "description": "Multi-URL crawl with depth control using crawler manager",
            "parameters": {
                "site_url": {"type": "string", "required": True},
                "seeds": {"type": "array", "description": "Starting URLs (defaults to site_url)"},
                "keywords": {"type": "array"},
                "max_depth": {"type": "integer", "default": 2},
                "max_pages": {"type": "integer", "default": 40},
                "allow_external": {"type": "boolean", "default": False},
                "relevance_threshold": {"type": "number", "default": 0.35},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]}
            },
            "returns": {"job": "Job object"}
        },

        "crawl.status": {
            "description": "Get crawler job status with optional results",
            "parameters": {
                "job_id": {"type": "string", "required": True},
                "include_results": {"type": "boolean", "default": False},
                "include_content": {"type": "boolean", "default": False}
            },
            "returns": {"job": "Job object", "results": "Array of result objects if requested"}
        },

        "llm.invoke": {
            "description": "Invoke an LLM model for chat completion",
            "parameters": {
                "model": {"type": "string", "required": True, "description": "Model ID"},
                "messages": {
                    "type": "array",
                    "required": True,
                    "description": "Array of {role, content} objects"
                },
                "options": {
                    "type": "object",
                    "properties": {
                        "temperature": {"type": "number"},
                        "stream": {"type": "boolean", "default": False}
                    }
                }
            },
            "returns": {
                "model": "Model ID used",
                "provider": "Provider name",
                "output": "Generated text",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
        },

        "posts.create": {
            "description": "Create a WordPress post",
            "parameters": {
                "title": {"type": "string", "required": True},
                "content": {"type": "string", "required": True},
                "status": {"type": "string", "default": "publish"},
                "categories": {"type": "array"},
                "featured_media": {"type": "integer"}
            },
            "returns": {"id": "Post ID", "link": "Post URL"}
        },

        "media.upload": {
            "description": "Upload media to WordPress",
            "parameters": {
                "file_data": {"type": "string", "required": True, "description": "Base64 encoded"},
                "filename": {"type": "string", "required": True},
                "content_type": {"type": "string", "default": "application/octet-stream"}
            },
            "returns": {"id": "Media ID", "url": "Media URL"}
        },

        "admin.crawler.control": {
            "description": "Control crawler instances",
            "parameters": {
                "action": {"type": "string", "required": True, "enum": ["start", "stop", "restart"]},
                "instance": {"type": "string", "required": True, "enum": ["user", "auto", "publisher"]}
            },
            "returns": {"status": "Instance status after action"}
        },

        "admin.crawler.config.get": {
            "description": "Get current crawler configuration",
            "parameters": {},
            "returns": {
                "user_crawler_workers": 4,
                "user_crawler_max_concurrent": 8,
                "auto_crawler_enabled": True
            }
        },

        "admin.crawler.config.set": {
            "description": "Update crawler configuration",
            "parameters": {
                "user_crawler_workers": {"type": "integer"},
                "user_crawler_max_concurrent": {"type": "integer"},
                "auto_crawler_enabled": {"type": "boolean"}
            },
            "returns": {"updated": True, "config": "Updated config object"}
        },

        # =====================================================================
        # TriStar Integration (v2.80)
        # =====================================================================
        "tristar.models": {
            "description": "Get all registered TriStar LLM models with roles and capabilities",
            "parameters": {
                "role": {"type": "string", "enum": ["admin", "lead", "worker", "reviewer"]},
                "capability": {"type": "string", "description": "Filter by capability"},
                "provider": {"type": "string", "description": "Filter by provider"}
            },
            "returns": {"models": "Array of model objects", "count": "Total count"},
            "example": {
                "jsonrpc": "2.0",
                "method": "tristar.models",
                "params": {"role": "lead"},
                "id": 1
            }
        },

        "tristar.init": {
            "description": "Initialize (impfen) a model with system prompt and configuration",
            "parameters": {
                "model_id": {"type": "string", "required": True, "description": "Model ID to initialize"}
            },
            "returns": {"status": "initialized", "init_data": "Payload to inject"}
        },

        "tristar.memory.store": {
            "description": "Store a memory entry in TriStar shared memory",
            "parameters": {
                "content": {"type": "string", "required": True},
                "memory_type": {"type": "string", "enum": ["fact", "decision", "code", "summary", "context", "todo"]},
                "llm_id": {"type": "string", "default": "system"},
                "initial_confidence": {"type": "number", "default": 0.8},
                "tags": {"type": "array"},
                "project_id": {"type": "string"},
                "ttl_seconds": {"type": "integer", "default": 86400}
            },
            "returns": {"entry_id": "Memory ID", "content_hash": "Hash", "aggregate_confidence": 0.8}
        },

        "tristar.memory.search": {
            "description": "Search TriStar shared memory",
            "parameters": {
                "query": {"type": "string", "required": True},
                "min_confidence": {"type": "number", "default": 0.0},
                "tags": {"type": "array"},
                "project_id": {"type": "string"},
                "memory_type": {"type": "string"},
                "limit": {"type": "integer", "default": 20}
            },
            "returns": {"results": "Array of memory entries", "count": "Result count"}
        },

        # =====================================================================
        # Codebase Access (v2.80) - Self-Development
        # =====================================================================
        "codebase.structure": {
            "description": "Get the backend codebase directory structure",
            "parameters": {
                "path": {"type": "string", "default": "app", "description": "Relative path to scan"},
                "include_files": {"type": "boolean", "default": True},
                "max_depth": {"type": "integer", "default": 4}
            },
            "returns": {"tree": "Directory tree object", "total_dirs": 0, "total_files": 0},
            "example": {
                "jsonrpc": "2.0",
                "method": "codebase.structure",
                "params": {"path": "app/services", "max_depth": 2},
                "id": 1
            }
        },

        "codebase.file": {
            "description": "Read a specific file from the backend codebase",
            "parameters": {
                "path": {"type": "string", "required": True, "description": "Relative path (e.g., 'app/routes/mcp.py')"}
            },
            "returns": {"path": "File path", "content": "File content", "size": 0, "lines": 0},
            "example": {
                "jsonrpc": "2.0",
                "method": "codebase.file",
                "params": {"path": "app/main.py"},
                "id": 1
            }
        },

        "codebase.search": {
            "description": "Search for patterns/text in the codebase (regex supported)",
            "parameters": {
                "query": {"type": "string", "required": True, "description": "Search pattern"},
                "path": {"type": "string", "default": "app"},
                "file_pattern": {"type": "string", "default": "*.py"},
                "max_results": {"type": "integer", "default": 50},
                "context_lines": {"type": "integer", "default": 2}
            },
            "returns": {"matches": "Array of match objects with file, line, context", "count": 0}
        },

        "codebase.routes": {
            "description": "Get all API routes with HTTP methods, paths, and handlers",
            "parameters": {},
            "returns": {"routes": "Array of route objects", "count": 0},
            "example": {
                "jsonrpc": "2.0",
                "method": "codebase.routes",
                "params": {},
                "id": 1
            }
        },

        "codebase.services": {
            "description": "Get all service modules with classes and functions",
            "parameters": {},
            "returns": {"services": "Array of service objects", "total_classes": 0, "total_functions": 0}
        },

        # =====================================================================
        # CLI Agents (v2.80) - Subprocess Management
        # =====================================================================
        "cli-agents.list": {
            "description": "List all CLI agents (Claude, Codex, Gemini) with their status",
            "parameters": {},
            "returns": {"cli_agents": "Array of agent objects", "count": 3},
            "example": {
                "jsonrpc": "2.0",
                "method": "cli-agents.list",
                "params": {},
                "id": 1
            }
        },

        "cli-agents.get": {
            "description": "Get details for a specific CLI agent",
            "parameters": {
                "agent_id": {"type": "string", "required": True, "description": "Agent ID (claude-mcp, codex-mcp, gemini-mcp)"}
            },
            "returns": {"agent_id": "ID", "status": "running/stopped", "pid": 12345, "output_lines": 0}
        },

        "cli-agents.start": {
            "description": "Start a CLI agent subprocess (auto-fetches system prompt from TriForce)",
            "parameters": {
                "agent_id": {"type": "string", "required": True}
            },
            "returns": {"status": "started", "agent": "Agent object with PID"}
        },

        "cli-agents.stop": {
            "description": "Stop a CLI agent subprocess",
            "parameters": {
                "agent_id": {"type": "string", "required": True},
                "force": {"type": "boolean", "default": False}
            },
            "returns": {"status": "stopped", "agent": "Agent object"}
        },

        "cli-agents.restart": {
            "description": "Restart a CLI agent (stop + start)",
            "parameters": {
                "agent_id": {"type": "string", "required": True}
            },
            "returns": {"status": "started", "agent": "Agent object"}
        },

        "cli-agents.call": {
            "description": "Send a message to a CLI agent",
            "parameters": {
                "agent_id": {"type": "string", "required": True},
                "message": {"type": "string", "required": True},
                "timeout": {"type": "integer", "default": 120}
            },
            "returns": {"agent_id": "ID", "status": "message_queued", "message": "Truncated message"}
        },

        "cli-agents.broadcast": {
            "description": "Broadcast a message to multiple or all CLI agents",
            "parameters": {
                "message": {"type": "string", "required": True},
                "agent_ids": {"type": "array", "description": "Specific agents (omit for all)"}
            },
            "returns": {"broadcast": True, "results": "Dict of agent_id -> result"}
        },

        "cli-agents.output": {
            "description": "Get output buffer for a CLI agent",
            "parameters": {
                "agent_id": {"type": "string", "required": True},
                "lines": {"type": "integer", "default": 50}
            },
            "returns": {"agent_id": "ID", "output": "Array of output lines", "lines": 0}
        },

        "cli-agents.stats": {
            "description": "Get CLI agent statistics",
            "parameters": {},
            "returns": {"total_agents": 3, "by_status": {"stopped": 3}, "by_type": {"claude": 1, "codex": 1, "gemini": 1}}
        },

        "cli-agents.update-prompt": {
            "description": "Update the system prompt for a CLI agent",
            "parameters": {
                "agent_id": {"type": "string", "required": True},
                "prompt": {"type": "string", "required": True}
            },
            "returns": {"status": "updated", "agent": "Agent object"}
        },

        "cli-agents.reload-prompts": {
            "description": "Reload system prompts from TriForce for all agents",
            "parameters": {},
            "returns": {"status": "reloaded", "agents": "List of reloaded agent IDs"}
        },

        # =====================================================================
        # Gemini Function Calling & Code Execution (v2.80 NEW)
        # =====================================================================
        "gemini_function_call": {
            "description": "Execute Gemini with native function calling - Gemini can call TriForce tools autonomously",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Task/question for Gemini"},
                "tools": {"type": "array", "description": "TriForce tools to expose (default: memory, web_search, llm_call, code_exec)"},
                "auto_execute": {"type": "boolean", "default": True, "description": "Auto-execute function calls"},
                "max_iterations": {"type": "integer", "default": 5, "description": "Max function call rounds"}
            },
            "returns": {
                "prompt": "Original prompt",
                "timestamp": "ISO timestamp",
                "function_calls": "Array of executed function calls with results",
                "iterations": "Number of iterations used",
                "response": "Final response from Gemini",
                "success": "Boolean success status"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "gemini_function_call",
                "params": {"prompt": "Search memory for architecture decisions and summarize them"},
                "id": 1
            }
        },

        "gemini_code_exec": {
            "description": "Execute Python code in Gemini's secure sandbox or local fallback",
            "parameters": {
                "code": {"type": "string", "required": True, "description": "Python code to execute"},
                "context": {"type": "string", "description": "Optional context/description"},
                "timeout": {"type": "integer", "default": 30, "description": "Timeout in seconds"}
            },
            "returns": {
                "code": "Executed code",
                "language": "python",
                "timestamp": "ISO timestamp",
                "execution_mode": "gemini_native or local_sandbox",
                "stdout": "Standard output",
                "stderr": "Standard error (if any)",
                "return_code": "Exit code (local mode only)",
                "outcome": "OUTCOME_OK or OUTCOME_ERROR (Gemini mode)",
                "success": "Boolean success status"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "gemini_code_exec",
                "params": {"code": "import math\nprint(math.pi * 2)"},
                "id": 1
            }
        },

        # =====================================================================
        # Hugging Face Inference API (v2.80 NEW)
        # =====================================================================
        "hf_generate": {
            "description": "Text generation via Hugging Face Inference API (Llama, Mistral, Qwen, etc.)",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Input prompt"},
                "model": {"type": "string", "default": "meta-llama/Llama-3.2-3B-Instruct", "description": "HF Model ID"},
                "max_new_tokens": {"type": "integer", "default": 512},
                "temperature": {"type": "number", "default": 0.7}
            },
            "returns": {
                "generated_text": "Generated text output",
                "model": "Model used",
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_generate",
                "params": {"prompt": "Explain quantum computing in simple terms", "max_new_tokens": 200},
                "id": 1
            }
        },

        "hf_chat": {
            "description": "Chat completion via Hugging Face (OpenAI-compatible format)",
            "parameters": {
                "messages": {"type": "array", "required": True, "description": "Chat messages [{role, content}]"},
                "model": {"type": "string", "default": "meta-llama/Llama-3.2-3B-Instruct"},
                "max_tokens": {"type": "integer", "default": 512},
                "temperature": {"type": "number", "default": 0.7}
            },
            "returns": {
                "choices": "Array of response choices",
                "model": "Model used",
                "usage": "Token usage stats"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_chat",
                "params": {
                    "messages": [
                        {"role": "system", "content": "You are a helpful assistant."},
                        {"role": "user", "content": "Hello!"}
                    ]
                },
                "id": 1
            }
        },

        "hf_embed": {
            "description": "Generate embeddings via Hugging Face (sentence-transformers)",
            "parameters": {
                "texts": {"type": "array", "required": True, "description": "Texts to embed"},
                "model": {"type": "string", "default": "sentence-transformers/all-MiniLM-L6-v2"}
            },
            "returns": {
                "embeddings": "Array of embedding vectors",
                "model": "Model used",
                "dimension": "Vector dimension",
                "count": "Number of embeddings"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_embed",
                "params": {"texts": ["Hello world", "Goodbye world"]},
                "id": 1
            }
        },

        "hf_image": {
            "description": "Text-to-Image via Hugging Face (FLUX, Stable Diffusion)",
            "parameters": {
                "prompt": {"type": "string", "required": True, "description": "Image description"},
                "model": {"type": "string", "default": "black-forest-labs/FLUX.1-schnell"},
                "negative_prompt": {"type": "string", "description": "What to avoid"},
                "width": {"type": "integer", "default": 1024},
                "height": {"type": "integer", "default": 1024}
            },
            "returns": {
                "image_base64": "Base64-encoded PNG image",
                "model": "Model used",
                "prompt": "Original prompt",
                "width": "Image width",
                "height": "Image height",
                "format": "png"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_image",
                "params": {"prompt": "A cyberpunk cityscape at night, neon lights", "width": 1024, "height": 768},
                "id": 1
            }
        },

        "hf_summarize": {
            "description": "Summarize text via Hugging Face (BART, Pegasus)",
            "parameters": {
                "text": {"type": "string", "required": True, "description": "Text to summarize"},
                "model": {"type": "string", "default": "facebook/bart-large-cnn"},
                "max_length": {"type": "integer", "default": 150}
            },
            "returns": {
                "summary": "Summarized text",
                "model": "Model used"
            }
        },

        "hf_translate": {
            "description": "Translate text via Hugging Face (OPUS-MT)",
            "parameters": {
                "text": {"type": "string", "required": True, "description": "Text to translate"},
                "model": {"type": "string", "default": "Helsinki-NLP/opus-mt-de-en", "description": "Translation model (de-en, en-de, etc.)"}
            },
            "returns": {
                "translation": "Translated text",
                "model": "Model used"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_translate",
                "params": {"text": "Guten Morgen, wie geht es Ihnen?", "model": "Helsinki-NLP/opus-mt-de-en"},
                "id": 1
            }
        },

        "hf_models": {
            "description": "List recommended Hugging Face models by task",
            "parameters": {
                "task": {"type": "string", "enum": ["text_generation", "chat", "embeddings", "text_to_image", "summarization", "translation"], "description": "Task type (optional)"}
            },
            "returns": {
                "models": "Dict of task -> model list (or filtered by task)",
                "task": "Task filter if provided"
            },
            "example": {
                "jsonrpc": "2.0",
                "method": "hf_models",
                "params": {"task": "embeddings"},
                "id": 1
            }
        }
    },

    # =========================================================================
    # Model Providers
    # =========================================================================
    "providers": {
        "ollama": {
            "description": "Local Ollama server for open-source models",
            "base_url": "http://localhost:11434",
            "models": ["llama3.2", "qwen2.5:14b", "mistral", "codellama"],
            "capabilities": ["chat", "code", "vision (some models)"]
        },
        "gemini": {
            "description": "Google Gemini API",
            "models": ["gemini-2.0-flash", "gemini-2.0-flash-thinking", "gemini-1.5-pro"],
            "capabilities": ["chat", "vision", "code", "long context"]
        },
        "mistral": {
            "description": "Mistral AI API",
            "models": ["large", "small", "codestral"],
            "capabilities": ["chat", "code"]
        },
        "anthropic": {
            "description": "Anthropic Claude API",
            "models": ["claude-sonnet-4", "claude-opus-4", "claude-3.5-sonnet"],
            "capabilities": ["chat", "vision", "code", "analysis"]
        },
        "gpt-oss": {
            "description": "GPT-OSS API (German)",
            "models": ["20b-cloud", "70b-cloud"],
            "capabilities": ["chat", "German language"]
        },
        "huggingface": {
            "description": "Hugging Face Inference API (Free Tier)",
            "base_url": "https://router.huggingface.co/hf-inference",
            "models": {
                "text_generation": ["meta-llama/Llama-3.2-3B-Instruct", "mistralai/Mistral-7B-Instruct-v0.3", "Qwen/Qwen2.5-7B-Instruct"],
                "embeddings": ["sentence-transformers/all-MiniLM-L6-v2", "BAAI/bge-small-en-v1.5"],
                "text_to_image": ["black-forest-labs/FLUX.1-schnell", "stabilityai/stable-diffusion-xl-base-1.0"],
                "summarization": ["facebook/bart-large-cnn"],
                "translation": ["Helsinki-NLP/opus-mt-de-en", "Helsinki-NLP/opus-mt-en-de"]
            },
            "capabilities": ["text generation", "chat", "embeddings", "text-to-image", "summarization", "translation"],
            "rate_limiting": "Free tier with automatic retry"
        }
    },

    # =========================================================================
    # Usage Examples for Claude
    # =========================================================================
    "usage_examples": {
        "chat_with_model": """
# Using REST API
curl -X POST https://api.ailinux.me/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model": "ollama/llama3.2", "messages": [{"role": "user", "content": "Hello"}]}'

# Using MCP
POST /v1/mcp
{"jsonrpc": "2.0", "method": "llm.invoke", "params": {"model": "ollama/llama3.2", "messages": [{"role": "user", "content": "Hello"}]}, "id": 1}
        """,

        "crawl_website": """
# Fast single URL crawl
POST /v1/mcp
{"jsonrpc": "2.0", "method": "crawl.url", "params": {"url": "https://example.com"}, "id": 1}

# Deep site crawl
POST /v1/mcp
{"jsonrpc": "2.0", "method": "crawl.site", "params": {"site_url": "https://example.com", "max_depth": 3, "max_pages": 100}, "id": 1}
        """,

        "generate_image": """
curl -X POST https://api.ailinux.me/v1/txt2img \\
  -H "Content-Type: application/json" \\
  -d '{"prompt": "A beautiful sunset over mountains", "workflow_type": "sdxl", "width": 1024, "height": 1024}'
        """,

        "create_wordpress_post": """
POST /v1/mcp
{"jsonrpc": "2.0", "method": "posts.create", "params": {"title": "My Post", "content": "<p>Hello World</p>", "status": "publish"}, "id": 1}
        """,

        "gemini_function_calling": """
# Let Gemini autonomously use tools to complete a task
POST /v1/mcp
{"jsonrpc": "2.0", "method": "gemini_function_call", "params": {"prompt": "Search memory for architecture decisions and create a summary"}, "id": 1}

# With specific tools
{"jsonrpc": "2.0", "method": "gemini_function_call", "params": {"prompt": "Find recent todos", "tools": ["memory_recall", "memory_search"]}, "id": 1}
        """,

        "gemini_code_execution": """
# Execute Python code in Gemini sandbox
POST /v1/mcp
{"jsonrpc": "2.0", "method": "gemini_code_exec", "params": {"code": "import math\\nprint(f'Pi = {math.pi:.10f}')"}, "id": 1}

# With timeout
{"jsonrpc": "2.0", "method": "gemini_code_exec", "params": {"code": "sum([i**2 for i in range(1000)])", "timeout": 10}, "id": 1}
        """,

        "huggingface_text_generation": """
# Generate text with Llama 3.2
POST /v1/mcp
{"jsonrpc": "2.0", "method": "hf_generate", "params": {"prompt": "Explain machine learning", "model": "meta-llama/Llama-3.2-3B-Instruct", "max_new_tokens": 200}, "id": 1}

# Chat completion
{"jsonrpc": "2.0", "method": "hf_chat", "params": {"messages": [{"role": "user", "content": "Hello!"}]}, "id": 1}
        """,

        "huggingface_embeddings": """
# Generate embeddings for semantic search
POST /v1/mcp
{"jsonrpc": "2.0", "method": "hf_embed", "params": {"texts": ["Machine learning is great", "AI is the future"]}, "id": 1}
        """,

        "huggingface_image_generation": """
# Generate image with FLUX
POST /v1/mcp
{"jsonrpc": "2.0", "method": "hf_image", "params": {"prompt": "A futuristic robot in a garden, cyberpunk style", "width": 1024, "height": 768}, "id": 1}

# With negative prompt
{"jsonrpc": "2.0", "method": "hf_image", "params": {"prompt": "A serene mountain landscape", "negative_prompt": "people, buildings, cars"}, "id": 1}
        """,

        "huggingface_translation": """
# German to English
POST /v1/mcp
{"jsonrpc": "2.0", "method": "hf_translate", "params": {"text": "Wie geht es Ihnen heute?", "model": "Helsinki-NLP/opus-mt-de-en"}, "id": 1}

# English to German
{"jsonrpc": "2.0", "method": "hf_translate", "params": {"text": "How are you today?", "model": "Helsinki-NLP/opus-mt-en-de"}, "id": 1}
        """
    }
}


def get_api_docs(section: Optional[str] = None) -> Dict[str, Any]:
    """
    Get API documentation. Returns a concise summary by default to save tokens.
    Use 'section' parameter to get detailed info.

    Args:
        section: Optional section name (endpoints, mcp_methods, providers, usage_examples, info)

    Returns:
        Documentation dictionary (summary or specific section)
    """
    if section:
        if section in API_DOCUMENTATION:
            return {section: API_DOCUMENTATION[section]}
        return {"error": f"Unknown section: {section}", "available": list(API_DOCUMENTATION.keys())}
    
    # Return summary by default
    return {
        "info": {
            "title": API_DOCUMENTATION["info"]["title"],
            "version": API_DOCUMENTATION["info"]["version"],
            "note": "This is a summary. Request specific sections for details."
        },
        "endpoints": [f"{e.method.value} {e.path}" for e in API_DOCUMENTATION["endpoints"].values()],
        "mcp_tools": list(API_DOCUMENTATION["mcp_methods"].keys()),
        "providers": list(API_DOCUMENTATION["providers"].keys()),
        "available_sections": list(API_DOCUMENTATION.keys())
    }


def get_endpoint_for_task(task_description: str) -> List[Endpoint]:
    """
    Find relevant endpoints for a given task description.
    Uses keyword matching to suggest appropriate endpoints.

    Args:
        task_description: Natural language description of the task

    Returns:
        List of relevant Endpoint objects
    """
    task_lower = task_description.lower()
    matches = []

    keywords_map = {
        "chat": ["chat", "message", "ask", "talk", "converse", "llm"],
        "vision": ["image", "picture", "photo", "analyze", "vision", "see", "look"],
        "crawler": ["crawl", "scrape", "website", "url", "fetch", "extract"],
        "txt2img": ["generate", "create image", "draw", "art", "stable diffusion"],
        "posts": ["post", "publish", "wordpress", "blog", "article"],
        "models": ["model", "list", "available", "provider"],
        # v2.80 NEW
        "gemini_function": ["function call", "tool use", "autonomous", "gemini tools"],
        "gemini_code": ["code execution", "execute code", "run code", "python exec", "sandbox"],
        "hf_": ["hugging face", "huggingface", "llama", "mistral", "flux", "embedding", "translate", "summarize"]
    }

    for endpoint_key, endpoint in API_DOCUMENTATION["endpoints"].items():
        if isinstance(endpoint, Endpoint):
            for keyword_group, keywords in keywords_map.items():
                if keyword_group in endpoint_key:
                    if any(kw in task_lower for kw in keywords):
                        matches.append(endpoint)
                        break

    return matches


def format_endpoint_for_claude(endpoint: Endpoint) -> str:
    """Format an endpoint for Claude to understand easily."""
    lines = [
        f"## {endpoint.summary}",
        f"**{endpoint.method.value} {endpoint.path}**",
        "",
        endpoint.description.strip(),
        ""
    ]

    if endpoint.parameters:
        lines.append("### Parameters")
        for param in endpoint.parameters:
            req = "required" if param.required else "optional"
            lines.append(f"- `{param.name}` ({param.type}, {req}): {param.description}")
        lines.append("")

    if endpoint.request_body:
        lines.append("### Request Body")
        lines.append("```json")
        import json
        lines.append(json.dumps(endpoint.request_body, indent=2))
        lines.append("```")
        lines.append("")

    if endpoint.mcp_method:
        lines.append(f"**MCP Method:** `{endpoint.mcp_method}`")
        lines.append("")

    return "\n".join(lines)
