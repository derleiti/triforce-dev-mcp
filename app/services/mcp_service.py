from __future__ import annotations

import base64
import json
import logging
import re
import unicodedata
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from pathlib import Path

from app.paths import PROJECT_ROOT

from .crawler.user_crawler import get_user_crawler
from .crawler.manager import crawler_manager
from .wordpress import wordpress_service
from . import chat as chat_service
from .model_registry import registry
from ..utils.throttle import request_slot
from ..routes.admin_crawler import (
    CrawlerConfigUpdate,
    CrawlerConfigUpdateResponse,
    CrawlerControlRequest,
    control_crawler,
    get_crawler_config,
    update_crawler_config,
)
from ..mcp.api_docs import get_api_docs, get_endpoint_for_task
from ..mcp.translation import BidirectionalTranslator, APIToMCPTranslator, MCPToAPITranslator
from ..mcp.specialists import specialist_router, SPECIALISTS
from ..mcp.context import context_manager, prompt_library, workflow_manager
from .compatibility_layer import compatibility_layer
from .system_control import system_control
from .mcp_debugger import mcp_debugger
from .remote_task import remote_task_service, TaskType, TaskStatus
# === NEW CLIENT-SERVER ARCHITECTURE TOOLS ===
from .nova_chat_agent import nova_chat_agent_service as account_specialists

# Constants
BACKEND_ROOT = PROJECT_ROOT
# Comprehensive file extensions for codebase search
ALLOWED_EXTENSIONS = {
    # Python
    ".py", ".pyi", ".pyx", ".pxd", ".pyw",
    # JavaScript/TypeScript
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    # Web
    ".html", ".htm", ".css", ".scss", ".sass", ".less", ".vue", ".svelte",
    # PHP
    ".php", ".php3", ".php4", ".php5", ".php7", ".php8", ".phtml", ".inc",
    # Java/Kotlin/Scala
    ".java", ".kt", ".kts", ".scala", ".groovy", ".gradle",
    # C/C++/C#
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hxx", ".cs",
    # Go/Rust/Zig
    ".go", ".rs", ".zig",
    # Ruby/Perl
    ".rb", ".erb", ".rake", ".pl", ".pm", ".perl",
    # Shell/Bash
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".psm1", ".bat", ".cmd",
    # Config/Data
    ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
    ".env.example", ".env.sample", ".env.template",
    # Documentation
    ".md", ".markdown", ".rst", ".txt", ".adoc",
    # SQL/Database
    ".sql", ".sqlite", ".prisma",
    # DevOps/Infra
    ".dockerfile", ".containerfile", ".tf", ".tfvars", ".hcl",
    # Misc
    ".graphql", ".gql", ".proto", ".thrift", ".avsc",
    ".r", ".R", ".jl", ".lua", ".nim", ".ex", ".exs", ".erl", ".hrl",
    ".swift", ".m", ".mm",  # Apple
    ".dart", ".kt",  # Mobile
    ".v", ".sv", ".vhd", ".vhdl",  # Hardware
    ".asm", ".s",  # Assembly
    ".lisp", ".cl", ".el", ".clj", ".cljs", ".cljc", ".edn",  # Lisp family
    ".hs", ".lhs", ".ml", ".mli", ".fs", ".fsi", ".fsx",  # Functional
    ".coffee", ".litcoffee",  # CoffeeScript
}
BLOCKED_PATHS = {
    ".env", ".git", ".ssh", "secrets", "credentials",
    "__pycache__", ".venv", "node_modules", ".claude",
}
SAFE_HIDDEN_PATHS = {".github", ".env.example"}
BACKUP_DIR = BACKEND_ROOT / ".backups"
EDIT_LOG_FILE = BACKEND_ROOT / ".edit_log.jsonl"
EDIT_FORBIDDEN_PATHS = {
    ".env", ".env.local", ".env.production",
    "credentials.json", "secrets.py",
}

_mcp_logger = logging.getLogger("ailinux.mcp.service")

def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text.split()))

def _serialize_job(job) -> Dict[str, Any]:
    payload = job.to_dict()
    payload["allowed_domains"] = list(job.allowed_domains)
    return payload

def _resolve_code_path(path_value: str, root_value: str | None = None) -> Path:
    """Resolve a read-only code-tool path inside an approved dev workspace root."""
    if not isinstance(path_value, str) or not path_value:
        raise ValueError("path is required")
    normalized = unicodedata.normalize("NFC", path_value)
    if "\x00" in normalized or "\0" in normalized:
        raise ValueError("invalid path")
    candidates = [normalized]
    if root_value:
        candidates.append(unicodedata.normalize("NFC", str(root_value)))
    for candidate_text in candidates:
        parts = [part for part in candidate_text.replace("\\", "/").split("/") if part not in {"", ".", ".."}]
        for part in parts:
            lower = part.lower()
            if lower in BLOCKED_PATHS or (part.startswith(".") and lower not in SAFE_HIDDEN_PATHS):
                raise ValueError(f"blocked path component: {part}")

    from app.mcp.dev_tools import _resolve_dev_path

    if root_value:
        base = _resolve_dev_path(".", root=str(root_value))
        target = _resolve_dev_path(normalized, root=str(base))
        if target != base and base not in target.parents:
            raise ValueError("path is outside requested project root")
        return target

    candidate = Path(normalized).expanduser()
    if candidate.is_absolute():
        return _resolve_dev_path(str(candidate))
    return _resolve_dev_path(normalized, root=str(BACKEND_ROOT))


def _code_display_path(path: Path, root_value: str | None = None) -> str:
    try:
        if root_value:
            from app.mcp.dev_tools import _resolve_dev_path
            base = _resolve_dev_path(".", root=str(root_value))
            return str(path.relative_to(base))
        return str(path.relative_to(BACKEND_ROOT))
    except (ValueError, OSError):
        return str(path)


def _safe_path(relative_path: str) -> Optional[Path]:
    """Validates and returns safe path within backend root."""
    try:
        if "\x00" in relative_path or "\0" in relative_path:
            _mcp_logger.warning(f"Null byte injection attempt: {repr(relative_path[:50])}")
            return None
        
        normalized_path = unicodedata.normalize("NFC", relative_path)
        
        if any(ord(c) > 0xFFFF for c in normalized_path):
            _mcp_logger.warning(f"Suspicious Unicode in path: {repr(relative_path[:50])}")
            return None
            
        path_parts = normalized_path.replace("\\", "/").split("/")
        for part in path_parts:
            lower_part = part.lower()
            if lower_part in BLOCKED_PATHS or (
                part.startswith(".")
                and part not in {".", ".."}
                and lower_part not in SAFE_HIDDEN_PATHS
            ):
                _mcp_logger.warning(f"Blocked path component: {part}")
                return None
                    
        full_path = (BACKEND_ROOT / normalized_path).resolve()
        
        if full_path.is_symlink():
            real_target = full_path.resolve()
            if not real_target.is_relative_to(BACKEND_ROOT):
                _mcp_logger.warning(f"Symlink escape attempt: {normalized_path} -> {real_target}")
                return None
                
        if not full_path.is_relative_to(BACKEND_ROOT):
            _mcp_logger.warning(f"Path traversal attempt: {normalized_path}")
            return None
            
        return full_path
    except Exception as e:
        _mcp_logger.warning(f"Path validation error for {repr(relative_path[:50])}: {e}")
        return None

def _validate_python_syntax(content: str) -> tuple[bool, Optional[str]]:
    """Validates Python syntax without executing."""
    import ast
    try:
        ast.parse(content)
        return True, None
    except SyntaxError as e:
        return False, f"Line {e.lineno}: {e.msg}"

def _is_edit_forbidden_path(file_path: str) -> bool:
    normalized = unicodedata.normalize("NFC", file_path).replace("\\", "/")
    parts = {part.lower() for part in normalized.split("/") if part}
    return any(name.lower() in parts for name in EDIT_FORBIDDEN_PATHS)


def _create_backup(file_path: Path) -> Optional[Path]:
    """Creates timestamped backup of file."""
    if not file_path.exists():
        return None

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rel_path = file_path.relative_to(BACKEND_ROOT)
    backup_name = f"{rel_path.as_posix().replace('/', '_')}_{timestamp}.bak"
    backup_path = BACKUP_DIR / backup_name

    import shutil
    shutil.copy2(file_path, backup_path)
    return backup_path

def _log_edit(action: str, path: str, details: Dict[str, Any]):
    """Logs edit operations for audit trail."""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        "path": path,
        "details": details,
    }
    with open(EDIT_LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
async def handle_crawl_url(params: Dict[str, Any]) -> Dict[str, Any]:
    url = params.get("url")
    if not url:
        raise ValueError("'url' parameter is required for crawl.url")

    keywords = params.get("keywords")
    if keywords is not None and not isinstance(keywords, Iterable):
        raise ValueError("'keywords' must be an iterable of strings")

    job = await get_user_crawler().crawl_url(
        url=url,
        keywords=list(keywords) if keywords else None,
        max_pages=int(params.get("max_pages", 10)),
        idempotency_key=params.get("idempotency_key"),
    )
    return {"job": _serialize_job(job)}

async def handle_crawl_site(params: Dict[str, Any]) -> Dict[str, Any]:
    site_url = params.get("site_url")
    if not site_url:
        raise ValueError("'site_url' parameter is required for crawl.site")

    seeds = params.get("seeds") or [site_url]
    if not isinstance(seeds, Iterable):
        raise ValueError("'seeds' must be an iterable of URLs")

    keywords = params.get("keywords") or []
    if keywords and not isinstance(keywords, Iterable):
        raise ValueError("'keywords' must be iterable when provided")

    job = await crawler_manager.create_job(
        keywords=list(keywords) if keywords else [site_url],
        seeds=[str(seed) for seed in seeds],
        max_depth=int(params.get("max_depth", 2)),
        max_pages=int(params.get("max_pages", 40)),
        allow_external=bool(params.get("allow_external", False)),
        relevance_threshold=float(params.get("relevance_threshold", 0.35)),
        requested_by="mcp",
        priority=params.get("priority", "low"),
        idempotency_key=params.get("idempotency_key"),
    )
    return {"job": _serialize_job(job)}

async def handle_crawl_status(params: Dict[str, Any]) -> Dict[str, Any]:
    job_id = params.get("job_id")
    if not job_id:
        raise ValueError("'job_id' parameter is required for crawl.status")

    _uc = get_user_crawler()
    job = await _uc.get_job(job_id)
    source = "user"
    manager = _uc
    if not job:
        job = await crawler_manager.get_job(job_id)
        source = "manager"
        manager = crawler_manager
    if not job:
        raise ValueError(f"Crawler job '{job_id}' not found")

    include_results = params.get("include_results", False)
    include_content = params.get("include_content", False)
    results: List[Dict[str, Any]] = []
    if include_results:
        for result_id in job.results:
            result = await manager.get_result(result_id)
            if result:
                results.append(result.to_dict(include_content=include_content))

    payload = _serialize_job(job)
    payload["source"] = source
    payload["results"] = results
    return payload

async def handle_posts_create(params: Dict[str, Any]) -> Dict[str, Any]:
    title = params.get("title")
    content = params.get("content")
    status_value = params.get("status", "publish")
    categories = params.get("categories")
    featured_media = params.get("featured_media")

    if not title or not content:
        raise ValueError("'title' and 'content' are required for posts.create")

    result = await wordpress_service.create_post(
        title=title,
        content=content,
        status=status_value,
        categories=categories,
        featured_media=featured_media,
    )
    return result

async def handle_media_upload(params: Dict[str, Any]) -> Dict[str, Any]:
    file_data = params.get("file_data")
    filename = params.get("filename")
    content_type = params.get("content_type", "application/octet-stream")

    if not file_data or not filename:
        raise ValueError("'file_data' and 'filename' are required for media.upload")

    try:
        binary = base64.b64decode(file_data)
    except Exception as exc:
        raise ValueError(f"Invalid base64 payload: {exc}") from exc

    result = await wordpress_service.upload_media(
        filename=filename,
        file_content=binary,
        content_type=content_type,
    )
    return result

async def handle_llm_invoke(params: Dict[str, Any]) -> Dict[str, Any]:
    model_id = params.get("model") or params.get("provider_id")
    messages = params.get("messages")
    options = params.get("options") or {}

    if not model_id or not messages:
        raise ValueError("'model' (or provider_id) and 'messages' are required for llm.invoke")
    if not isinstance(messages, list):
        raise ValueError("'messages' must be a list of role/content dictionaries")

    # Auto-prefix: wenn kein Provider angegeben, versuche bekannte Prefixe
    if "/" not in model_id:
        for prefix in ["gemini", "ollama", "mistral", "groq", "anthropic", "openrouter"]:
            test_id = f"{prefix}/{model_id}"
            test_model = await registry.get_model(test_id)
            if test_model and "chat" in test_model.capabilities:
                model_id = test_id
                break
    
    # Auto-prefix: wenn kein Provider angegeben, versuche bekannte Prefixe
    if "/" not in model_id:
        for prefix in ["gemini", "ollama", "mistral", "groq", "anthropic", "openrouter"]:
            test_id = f"{prefix}/{model_id}"
            test_model = await registry.get_model(test_id)
            if test_model and "chat" in test_model.capabilities:
                model_id = test_id
                break
    
    model = await registry.get_model(model_id)
    if not model or "chat" not in model.capabilities:
        raise ValueError(f"Model '{model_id}' does not support chat capability")

    formatted_messages: List[Dict[str, str]] = []
    for entry in messages:
        role = entry.get("role") if isinstance(entry, dict) else None
        content = entry.get("content") if isinstance(entry, dict) else None
        if not role or content is None:
            raise ValueError("Each message must include 'role' and 'content'")
        formatted_messages.append({"role": role, "content": content})

    temperature = options.get("temperature")
    stream = bool(options.get("stream", False))

    chunks: List[str] = []
    async with request_slot():
        async for chunk in chat_service.stream_chat(
            model,
            model_id,
            (message for message in formatted_messages),
            stream=stream,
            temperature=temperature,
        ):
            if chunk:
                chunks.append(chunk)

    completion = "".join(chunks)
    prompt_tokens = sum(_estimate_tokens(item["content"]) for item in formatted_messages)
    completion_tokens = _estimate_tokens(completion)

    return {
        "model": model_id,
        "provider": model.provider,
        "output": completion,
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }

async def handle_admin_control(params: Dict[str, Any]) -> Dict[str, Any]:
    action = params.get("action")
    instance = params.get("instance")
    if not action or not instance:
        raise ValueError("'action' and 'instance' parameters are required")
    request = CrawlerControlRequest(action=action, instance=instance)
    result = await control_crawler(request)
    return result

async def handle_admin_config_get(_: Dict[str, Any]) -> Dict[str, Any]:
    return await get_crawler_config()

async def handle_admin_config_set(params: Dict[str, Any]) -> Dict[str, Any]:
    allowed_fields = {"user_crawler_workers", "user_crawler_max_concurrent", "auto_crawler_enabled"}
    updates = {key: value for key, value in (params or {}).items() if key in allowed_fields}
    if not updates:
        raise ValueError("No allowed configuration fields provided")
    update_request = CrawlerConfigUpdate(**updates)
    response: CrawlerConfigUpdateResponse = await update_crawler_config(update_request)
    return {"updated": response.updated, "config": response.config.dict()}
# =============================================================================
# API Documentation Handlers
# =============================================================================

async def handle_api_docs(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get API documentation for Claude Code integration."""
    section = params.get("section")
    return get_api_docs(section)

async def handle_api_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search for relevant API endpoints for a task."""
    task = params.get("task")
    if not task:
        raise ValueError("'task' parameter is required")
    endpoints = get_endpoint_for_task(task)
    return {
        "task": task,
        "endpoints": [
            {
                "path": ep.path,
                "method": ep.method.value,
                "summary": ep.summary,
                "mcp_method": ep.mcp_method
            }
            for ep in endpoints
        ]
    }

# =============================================================================
# Translation Layer Handlers
# =============================================================================

_translator = BidirectionalTranslator()

async def handle_translate(params: Dict[str, Any]) -> Dict[str, Any]:
    """Translate between API and MCP formats."""
    request = params.get("request")
    if not request:
        raise ValueError("'request' parameter is required")

    output_format = params.get("output_format", "auto")
    result = _translator.translate_and_format(request, output_format)

    if isinstance(result, str):
        return {"format": "curl", "command": result}
    return {"format": "json", "data": result}

async def handle_api_to_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    """Convert REST API call to MCP JSON-RPC."""
    method = params.get("method", "GET")
    path = params.get("path")
    body = params.get("body", {})

    if not path:
        raise ValueError("'path' parameter is required")

    api_translator = APIToMCPTranslator()
    result = api_translator.translate(method, path, body)

    if not result.success:
        raise ValueError(result.error)

    return {
        "mcp_method": result.method,
        "params": result.data,
        "jsonrpc": {
            "jsonrpc": "2.0",
            "method": result.method,
            "params": result.data,
            "id": 1
        }
    }

async def handle_mcp_to_api(params: Dict[str, Any]) -> Dict[str, Any]:
    """Convert MCP method call to REST API request."""
    mcp_method = params.get("method")
    mcp_params = params.get("params", {})

    if not mcp_method:
        raise ValueError("'method' parameter is required")

    mcp_translator = MCPToAPITranslator()
    result = mcp_translator.translate(mcp_method, mcp_params)

    if not result.success:
        raise ValueError(result.error)

    return {
        "http_method": result.method,
        "body": result.data,
        "query_params": result.query_params,
        "curl": mcp_translator.to_curl(mcp_method, mcp_params)
    }

# =============================================================================
# Specialist Routing Handlers
# =============================================================================

async def handle_models_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """
    List available AI models, optimized for context window usage.
    Returns models grouped by provider and capability.
    """
    models = await registry.list_models()
    
    # Group by provider
    by_provider = {}
    for m in models:
        if m.provider not in by_provider:
            by_provider[m.provider] = []
        by_provider[m.provider].append(m.id)

    # Group by key capabilities (saving context by not listing every model for every cap)
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
            elif cap == "image_gen": # Map specific caps to broader categories if needed
                 if "vision" not in by_capability: by_capability["vision"] = []
                 by_capability["vision"].append(m.id)

    # Simplify lists - strictly limit to ID strings to save tokens
    return {
        "summary": {
            "total_models": len(models),
            "providers": list(by_provider.keys())
        },
        "by_provider": by_provider,
        "by_capability": {k: v[:50] for k, v in by_capability.items()}, # Limit to top 50 per cap to prevent overflow
        "note": "Lists are truncated to top 50 per capability to save context."
    }

async def handle_specialists_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List all available model specialists."""
    return {
        "specialists": specialist_router.list_specialists(),
        "count": len(SPECIALISTS)
    }

async def handle_specialists_route(params: Dict[str, Any]) -> Dict[str, Any]:
    """Route a task to the best specialist(s)."""
    task = params.get("task")
    if not task:
        raise ValueError("'task' parameter is required")

    preferred_speed = params.get("preferred_speed")
    max_cost = params.get("max_cost_tier")

    specialists = specialist_router.route(
        task,
        preferred_speed=preferred_speed,
        max_cost_tier=max_cost
    )

    return {
        "task": task,
        "recommended": [s.to_dict() for s in specialists[:3]],
        "all_matches": len(specialists)
    }

async def handle_specialists_invoke(params: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a specialist model with automatic routing."""
    task = params.get("task")
    message = params.get("message")
    specialist_id = params.get("specialist_id")

    if not message:
        raise ValueError("'message' parameter is required")

    if specialist_id and specialist_id.startswith("nova-account/"):
        return await account_specialists.invoke_specialized_agent(
            specialist_id=specialist_id,
            message=message,
            messages=params.get("messages"),
            system=params.get("system", ""),
            model=params.get("model"),
            temperature=params.get("temperature", 0.4),
            max_tokens=params.get("max_tokens", 1200),
            timeout=params.get("timeout", 120),
        )

    # Get specialist - either specified or auto-routed
    if specialist_id:
        specialist = specialist_router.get_specialist_by_id(specialist_id)
        if not specialist:
            raise ValueError(f"Specialist '{specialist_id}' not found")
    elif task:
        specialist = specialist_router.get_best_specialist(task)
        if not specialist:
            raise ValueError("No suitable specialist found for task")
    else:
        raise ValueError("Either 'task' or 'specialist_id' is required")

    # Build messages with optional system prompt
    messages = []
    if specialist.system_prompt_template:
        messages.append({"role": "system", "content": specialist.system_prompt_template})
    messages.append({"role": "user", "content": message})

    # Invoke the model
    result = await handle_llm_invoke({
        "model": specialist.id,
        "messages": messages,
        "options": params.get("options", {})
    })

    result["specialist"] = specialist.to_dict()
    return result

# =============================================================================
# Context Management Handlers
# =============================================================================

async def handle_context_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new conversation context."""
    session_id = params.get("session_id")
    system_prompt = params.get("system_prompt")
    metadata = params.get("metadata", {})

    context = context_manager.create_context(session_id, system_prompt, metadata)
    return context.get_summary()

async def handle_context_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get an existing conversation context."""
    session_id = params.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")

    context = context_manager.get_context(session_id)
    if not context:
        raise ValueError(f"Context '{session_id}' not found or expired")

    return context.to_dict()

async def handle_context_message(params: Dict[str, Any]) -> Dict[str, Any]:
    """Add a message to a context and optionally get LLM response."""
    session_id = params.get("session_id")
    message = params.get("message")
    get_response = params.get("get_response", False)
    model = params.get("model")

    if not session_id or not message:
        raise ValueError("'session_id' and 'message' are required")

    context = context_manager.get_or_create_context(session_id)
    context.add_user_message(message)

    result: Dict[str, Any] = {"session_id": session_id, "message_added": True}

    if get_response and model:
        # Get LLM response
        messages = context.get_messages_for_api()
        llm_result = await handle_llm_invoke({
            "model": model,
            "messages": messages,
            "options": params.get("options", {})
        })
        context.add_assistant_message(llm_result["output"])
        result["response"] = llm_result

    result["context_summary"] = context.get_summary()
    return result

async def handle_context_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List all active contexts."""
    return {"contexts": context_manager.list_contexts()}

async def handle_context_clear(params: Dict[str, Any]) -> Dict[str, Any]:
    """Clear messages from a context."""
    session_id = params.get("session_id")
    if not session_id:
        raise ValueError("'session_id' is required")

    context = context_manager.get_context(session_id)
    if not context:
        raise ValueError(f"Context '{session_id}' not found")

    context.clear()
    return {"session_id": session_id, "cleared": True}
# =============================================================================
# Prompt Library Handlers
# =============================================================================

async def handle_prompts_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List available prompt templates."""
    templates = prompt_library.list_templates()
    return {
        "templates": [
            prompt_library.get_template_info(t)
            for t in templates
        ]
    }

async def handle_prompts_render(params: Dict[str, Any]) -> Dict[str, Any]:
    """Render a prompt template with variables."""
    name = params.get("name")
    variables = params.get("variables", {})

    if not name:
        raise ValueError("'name' is required")

    rendered = prompt_library.render(name, **variables)
    return {"name": name, "rendered": rendered}

async def handle_prompts_add(params: Dict[str, Any]) -> Dict[str, Any]:
    """Add a custom prompt template."""
    name = params.get("name")
    template = params.get("template")

    if not name or not template:
        raise ValueError("'name' and 'template' are required")

    prompt_library.add_template(name, template)
    return {"name": name, "added": True}

# =============================================================================
# Workflow Orchestration Handlers
# =============================================================================

async def handle_workflows_list(_: Dict[str, Any]) -> Dict[str, Any]:
    """List workflow templates and active workflows."""
    return {
        "templates": workflow_manager.list_templates(),
        "active": workflow_manager.list_workflows()
    }

async def handle_workflows_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new workflow from a template."""
    template_name = params.get("template")
    workflow_id = params.get("workflow_id")
    initial_context = params.get("context", {})

    if not template_name:
        raise ValueError("'template' is required")

    workflow = workflow_manager.create_workflow(
        template_name, workflow_id, initial_context
    )
    return workflow.to_dict()

async def handle_workflows_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get workflow status."""
    workflow_id = params.get("workflow_id")
    if not workflow_id:
        raise ValueError("'workflow_id' is required")

    workflow = workflow_manager.get_workflow(workflow_id)
    if not workflow:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    return workflow.to_dict()

# ============================================================================
# TriStar Integration Handlers
# ============================================================================

async def handle_tristar_models(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all registered TriStar models."""
    from .tristar.model_init import model_init_service

    role = params.get("role")
    capability = params.get("capability")
    provider = params.get("provider")

    models = await model_init_service.list_models()

    # Filter by parameters
    if role:
        models = [m for m in models if m.role.value == role]
    if capability:
        models = [m for m in models if any(c.value == capability for c in m.capabilities)]
    if provider:
        models = [m for m in models if m.provider == provider]

    return {
        "models": [
            {
                "model_id": m.model_id,
                "model_name": m.model_name,
                "provider": m.provider,
                "role": m.role.value,
                "capabilities": [c.value for c in m.capabilities],
                "initialized": m.initialized,
                "healthy": m.healthy,
            }
            for m in models
        ],
        "count": len(models),
    }

async def handle_tristar_init(params: Dict[str, Any]) -> Dict[str, Any]:
    """Initialize (impfen) a model with system prompt."""
    from .tristar.model_init import model_init_service

    model_id = params.get("model_id")
    if not model_id:
        raise ValueError("'model_id' parameter is required")

    try:
        init_data = await model_init_service.init_model(model_id)
        return {
            "status": "initialized",
            "model_id": model_id,
            "init_data": init_data,
        }
    except ValueError as e:
        raise ValueError(str(e))

async def handle_tristar_memory_store(params: Dict[str, Any]) -> Dict[str, Any]:
    """Store a memory entry in TriStar memory."""
    from .tristar.memory_controller import memory_controller

    content = params.get("content")
    if not content:
        raise ValueError("'content' parameter is required")

    entry = await memory_controller.store(
        content=content,
        memory_type=params.get("memory_type", "fact"),
        llm_id=params.get("llm_id", "mcp-client"),
        initial_confidence=params.get("initial_confidence", 0.8),
        ttl_seconds=params.get("ttl_seconds", 86400),
        tags=params.get("tags", []),
        project_id=params.get("project_id"),
    )

    return {
        "entry_id": entry.entry_id,
        "content_hash": entry.content_hash,
        "aggregate_confidence": entry.aggregate_confidence,
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
        "status": "stored",
    }

async def handle_tristar_memory_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Search TriStar memory."""
    from .tristar.memory_controller import memory_controller

    query = params.get("query", "")
    results = await memory_controller.search(
        query=query,
        min_confidence=params.get("min_confidence", 0.0),
        tags=params.get("tags"),
        memory_type=params.get("memory_type"),
        limit=params.get("limit", 10),
    )

    return {
        "results": [e.to_dict() for e in results],
        "count": len(results),
    }
# ============================================================================
# Codebase Access Handlers
# ============================================================================

async def handle_codebase_structure(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get the codebase directory structure as a concise tree string."""
    include_files = params.get("include_files", True)
    max_depth = params.get("max_depth", 4)
    path = params.get("path", "app")

    safe_root = _safe_path(path)
    if not safe_root or not safe_root.exists():
        raise ValueError(f"Path not found: {path}")

    lines = []

    def build_tree(dir_path: Path, prefix: str = "", current_depth: int = 0):
        if current_depth > max_depth:
            lines.append(f"{prefix}└── ... (max depth)")
            return

        try:
            # Sort: directories first, then files
            items = sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            
            # Filter items
            filtered_items = []
            for item in items:
                if item.name.startswith(".") or item.name == "__pycache__":
                    continue
                if item.is_file() and not include_files:
                    continue
                if item.is_file() and item.suffix not in ALLOWED_EXTENSIONS:
                    continue
                filtered_items.append(item)

            for i, item in enumerate(filtered_items):
                is_last = (i == len(filtered_items) - 1)
                connector = "└── " if is_last else "├── "
                
                if item.is_dir():
                    lines.append(f"{prefix}{connector}{item.name}/")
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    build_tree(item, new_prefix, current_depth + 1)
                else:
                    lines.append(f"{prefix}{connector}{item.name}")

        except PermissionError:
            lines.append(f"{prefix}└── (access denied)")

    lines.append(f"{path}/")
    build_tree(safe_root)
    
    return {
        "root": path,
        "structure": "\n".join(lines),
        "backend_version": "2.80 (Optimized Tree)",
    }

async def handle_codebase_file(params: Dict[str, Any]) -> Dict[str, Any]:
    """Read a text/source file from an approved project root."""
    file_path = params.get("path")
    if not file_path:
        raise ValueError("'path' parameter is required")
    root_value = params.get("root")
    safe_path = _resolve_code_path(str(file_path), str(root_value) if root_value else None)
    if not safe_path.exists() or not safe_path.is_file():
        raise ValueError(f"File not found: {file_path}")
    if safe_path.suffix not in ALLOWED_EXTENSIONS and safe_path.name not in {"Dockerfile", "Makefile", "Rakefile", "Taskfile", "Vagrantfile"}:
        raise ValueError(f"File type not allowed: {safe_path.suffix or safe_path.name}")
    if safe_path.stat().st_size > 500_000:
        raise ValueError("File too large (max 500KB)")
    try:
        lines = safe_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("File is not valid UTF-8 text") from exc
    start = max(1, int(params.get("start_line") or 1))
    end = min(len(lines), int(params.get("end_line") or len(lines)))
    if end < start:
        raise ValueError("end_line must be >= start_line")
    selected = lines[start - 1:end]
    content = "\n".join(f"{number}: {lines[number - 1]}" for number in range(start, end + 1))
    return {
        "path": _code_display_path(safe_path, str(root_value) if root_value else None),
        "content": content,
        "size": safe_path.stat().st_size,
        "lines": len(lines),
        "start_line": start,
        "end_line": end,
    }

async def handle_codebase_search(params: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively search source/text files inside an approved project root."""
    query = params.get("query")
    if not isinstance(query, str) or not query:
        raise ValueError("'query' parameter is required")
    root_value = params.get("root")
    path_value = str(params.get("path") or ".")
    safe_root = _resolve_code_path(path_value, str(root_value) if root_value else None)
    if not safe_root.exists():
        raise ValueError(f"Path not found: {path_value}")
    file_pattern = str(params.get("file_pattern") or "*")
    max_results = max(1, min(int(params.get("max_results") or 50), 100))
    context_lines = max(0, min(int(params.get("context_lines") or 2), 5))
    flags = 0 if bool(params.get("case_sensitive")) else re.IGNORECASE
    expression = query if bool(params.get("regex")) else re.escape(query)
    try:
        pattern = re.compile(expression, flags)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc

    files = [safe_root] if safe_root.is_file() else safe_root.rglob(file_pattern)
    results = []
    ignored = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}
    for source_file in files:
        if not source_file.is_file() or any(part in ignored for part in source_file.parts):
            continue
        if source_file.suffix not in ALLOWED_EXTENSIONS and source_file.name not in {"Dockerfile", "Makefile", "Rakefile", "Taskfile", "Vagrantfile"}:
            continue
        try:
            if source_file.stat().st_size > 2_000_000:
                continue
            lines = source_file.read_text(encoding="utf-8").splitlines()
        except (PermissionError, UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(lines):
            if not pattern.search(line):
                continue
            left = max(0, i - context_lines)
            right = min(len(lines), i + context_lines + 1)
            results.append({
                "file": _code_display_path(source_file, str(root_value) if root_value else None),
                "line": i + 1,
                "match": line.strip(),
                "context": lines[left:right],
            })
            if len(results) >= max_results:
                break
        if len(results) >= max_results:
            break
    return {
        "query": query,
        "root": str(root_value or BACKEND_ROOT),
        "path": path_value,
        "results": results,
        "count": len(results),
        "truncated": len(results) >= max_results,
    }

async def handle_codebase_routes(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all API routes with their handlers."""
    routes_dir = BACKEND_ROOT / "app" / "routes"
    routes = []

    route_pattern = re.compile(
        r'@router\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\\]+)["\\]', 
        re.IGNORECASE
    )

    for py_file in routes_dir.glob("*.py"):
        if py_file.name.startswith("_"):
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
            lines = content.splitlines()

            for i, line in enumerate(lines):
                match = route_pattern.search(line)
                if match:
                    method = match.group(1).upper()
                    path = match.group(2)

                    func_name = None
                    for j in range(i + 1, min(i + 5, len(lines))):
                        if "async def " in lines[j] or "def " in lines[j]:
                            func_match = re.search(r"def\s+(\w+)", lines[j])
                            if func_match:
                                func_name = func_match.group(1)
                            break

                    routes.append({
                        "file": py_file.name,
                        "method": method,
                        "path": path,
                        "handler": func_name,
                        "line": i + 1,
                    })
        except (PermissionError, UnicodeDecodeError):
            continue

    by_file = {}
    for route in routes:
        fname = route["file"]
        if fname not in by_file:
            by_file[fname] = []
        by_file[fname].append(route)

    return {
        "routes": routes,
        "count": len(routes),
        "by_file": by_file,
        "files": list(by_file.keys()),
    }

async def handle_codebase_services(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get all service modules with classes and functions."""
    services_dir = BACKEND_ROOT / "app" / "services"
    services = []

    class_pattern = re.compile(r"^class\s+(\w+)")
    func_pattern = re.compile(r"^(?:async\s+)?def\s+(\w+)")

    def scan_service(service_path: Path, prefix: str = "") -> List[Dict[str, Any]]:
        results = []

        for item in sorted(service_path.iterdir()):
            if item.name.startswith("_") and item.name != "__init__.py":
                continue

            if item.is_dir():
                results.extend(scan_service(item, f"{prefix}{item.name}/" ))
            elif item.suffix == ".py":
                try:
                    content = item.read_text(encoding="utf-8")
                    lines = content.splitlines()

                    classes = []
                    functions = []

                    for i, line in enumerate(lines):
                        class_match = class_pattern.match(line)
                        if class_match:
                            classes.append({
                                "name": class_match.group(1),
                                "line": i + 1,
                            })

                        func_match = func_pattern.match(line)
                        if func_match and not line.strip().startswith("#"):
                            functions.append({
                                "name": func_match.group(1),
                                "line": i + 1,
                                "async": "async def" in line,
                            })

                    if classes or functions:
                        results.append({
                            "file": f"{prefix}{item.name}",
                            "path": str(item.relative_to(BACKEND_ROOT)),
                            "classes": classes,
                            "functions": functions,
                            "lines": len(lines),
                        })
                except (PermissionError, UnicodeDecodeError):
                    continue

        return results

    services = scan_service(services_dir)

    total_classes = sum(len(s["classes"]) for s in services)
    total_functions = sum(len(s["functions"]) for s in services)

    return {
        "services": services,
        "count": len(services),
        "total_classes": total_classes,
        "total_functions": total_functions,
    }

async def handle_codebase_edit(params: Dict[str, Any]) -> Dict[str, Any]:
    """Edit a file in the codebase with safety checks."""
    file_path = params.get("path")
    mode = params.get("mode")

    if not file_path:
        raise ValueError("'path' parameter is required")
    if not mode:
        raise ValueError("'mode' parameter is required")

    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    if _is_edit_forbidden_path(file_path):
        raise ValueError(f"Editing forbidden for security-sensitive files: {file_path}")

    if not safe_path.exists():
        raise ValueError(f"File not found: {file_path}")

    if safe_path.suffix not in ALLOWED_EXTENSIONS:
        raise ValueError(f"File type not allowed for editing: {safe_path.suffix}")

    try:
        original_content = safe_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError("File is not valid UTF-8 text")

    original_lines = original_content.splitlines(keepends=True)
    dry_run = params.get("dry_run", False)
    create_backup = params.get("create_backup", True)

    if mode == "replace":
        old_text = params.get("old_text")
        new_text = params.get("new_text", "")

        if not old_text:
            raise ValueError("'old_text' parameter required for replace mode")

        if old_text not in original_content:
            raise ValueError(f"old_text not found in file. File has {len(original_content)} chars.")

        occurrences = original_content.count(old_text)
        if occurrences > 1:
            raise ValueError(f"old_text found {occurrences} times. Please provide more unique text.")

        new_content = original_content.replace(old_text, new_text, 1)

    elif mode == "insert":
        line_number = params.get("line_number")
        new_text = params.get("new_text", "")

        if not line_number or line_number < 1:
            raise ValueError("'line_number' (>= 1) required for insert mode")

        lines = original_lines.copy()
        insert_idx = min(line_number - 1, len(lines))

        if new_text and not new_text.endswith("\n"):
            new_text += "\n"

        lines.insert(insert_idx, new_text)
        new_content = "".join(lines)

    elif mode == "append":
        new_text = params.get("new_text", "")

        if not new_text:
            raise ValueError("'new_text' parameter required for append mode")

        new_content = original_content
        if not new_content.endswith("\n"):
            new_content += "\n"
        new_content += new_text
        if not new_content.endswith("\n"):
            new_content += "\n"

    elif mode == "delete_lines":
        start_line = params.get("start_line")
        end_line = params.get("end_line")

        if not start_line or not end_line:
            raise ValueError("'start_line' and 'end_line' required for delete_lines mode")
        if start_line < 1 or end_line < start_line:
            raise ValueError("Invalid line range")

        lines = original_lines.copy()
        del lines[start_line - 1:end_line]
        new_content = "".join(lines)

    else:
        raise ValueError(f"Unknown mode: {mode}")

    if safe_path.suffix == ".py":
        is_valid, error_msg = _validate_python_syntax(new_content)
        if not is_valid:
            raise ValueError(f"Python syntax error in modified content: {error_msg}")

    original_line_count = len(original_lines)
    new_line_count = new_content.count("\n") + (0 if new_content.endswith("\n") else 1)
    lines_changed = abs(new_line_count - original_line_count)

    result = {
        "path": file_path,
        "mode": mode,
        "dry_run": dry_run,
        "original_lines": original_line_count,
        "new_lines": new_line_count,
        "lines_changed": lines_changed,
        "syntax_valid": True if safe_path.suffix == ".py" else None,
    }

    if dry_run:
        result["preview"] = new_content[:2000] + ("..." if len(new_content) > 2000 else "")
        result["message"] = "Dry run - no changes written"
    else:
        if create_backup:
            backup_path = _create_backup(safe_path)
            result["backup"] = str(backup_path.relative_to(BACKEND_ROOT)) if backup_path else None

        safe_path.write_text(new_content, encoding="utf-8")

        _log_edit("edit", file_path, {
            "mode": mode,
            "original_lines": original_line_count,
            "new_lines": new_line_count,
            "backup": result.get("backup"),
        })

        result["message"] = f"File edited successfully ({mode})"
    
    return result

async def handle_codebase_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new file in the backend codebase."""
    file_path = params.get("path")
    content = params.get("content")
    template = params.get("template")

    if not file_path:
        raise ValueError("'path' parameter is required")

    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    if _is_edit_forbidden_path(file_path):
        raise ValueError(f"Creating forbidden for security-sensitive file: {file_path}")

    if safe_path.exists():
        raise ValueError(f"File already exists: {file_path}")

    if template:
        templates = {
            "empty": "",
            "python_module": '"""\nModule description.\n"""\n\n',
            "fastapi_route": '"""\nRoute module.\n"""\nfrom fastapi import APIRouter, HTTPException\nfrom typing import Dict, Any\n\nrouter = APIRouter()\n\n@router.get("/")\nasync def get_root() -> Dict[str, str]:\n    return {"message": "Hello"}\n',
            "service_class": '"""\nService class.\n"""\nclass Service:\n    def __init__(self):\n        pass\n'
        }
        content = templates.get(template, "")

    
    if content is None:
        content = ""

    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(content, encoding="utf-8")
    
    return {"created": True, "path": file_path}

async def handle_codebase_backup(params: Dict[str, Any]) -> Dict[str, Any]:
    """Manage backups of codebase files."""
    file_path = params.get("path")
    action = params.get("action")

    if not file_path:
        raise ValueError("'path' parameter is required")
    if not action:
        raise ValueError("'action' parameter is required")

    safe_path = _safe_path(file_path)
    if not safe_path:
        raise ValueError(f"Invalid path: {file_path}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    rel_path = file_path.replace("/", "_")
    backup_pattern = f"{rel_path}_*.bak"

    if action == "create":
        if not safe_path.exists():
            raise ValueError(f"File not found: {file_path}")

        backup_path = _create_backup(safe_path)
        _log_edit("backup_create", file_path, {"backup": str(backup_path)})

        return {
            "action": "create",
            "backup": str(backup_path.relative_to(BACKEND_ROOT)) if backup_path else None
        }

    elif action == "list":
        backups = sorted(BACKUP_DIR.glob(backup_pattern), reverse=True)
        return {
            "action": "list",
            "backups": [str(b.relative_to(BACKEND_ROOT)) for b in backups],
            "count": len(backups)
        }

    elif action == "restore":
        backups = sorted(BACKUP_DIR.glob(backup_pattern), reverse=True)
        if not backups:
            raise ValueError("No backups found")
        
        latest = backups[0]
        import shutil
        shutil.copy2(latest, safe_path)
        _log_edit("restore", file_path, {"from": str(latest)})
        
        return {
            "action": "restore",
            "restored_from": str(latest.relative_to(BACKEND_ROOT))
        }

    else:
        raise ValueError(f"Unknown action: {action}")

# ============================================================================
# CLI Agent MCP Handlers
# ============================================================================

async def handle_cli_agents_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """List all CLI agents (Claude, Codex, Gemini subprocesses) - Optimized Summary."""
    from .tristar.agent_controller import agent_controller
    agents = await agent_controller.list_agents()
    
    summary = []
    for a in agents:
        agent_id = a.get("id", "unknown")
        status = a.get("status", "unknown")
        pid = a.get("pid", "-")
        summary.append(f"{agent_id}: {status} (pid={pid})")

    return {
        "summary": summary,
        "count": len(agents),
    }

async def handle_cli_agents_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get details for a specific CLI agent."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    agent = await agent_controller.get_agent(agent_id)
    if not agent:
        raise ValueError(f"CLI agent not found: {agent_id}")

    return agent

async def handle_cli_agents_start(params: Dict[str, Any]) -> Dict[str, Any]:
    """Start a CLI agent subprocess."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    try:
        result = await agent_controller.start_agent(agent_id)
        return result
    except ValueError as e:
        raise ValueError(str(e))

async def handle_cli_agents_stop(params: Dict[str, Any]) -> Dict[str, Any]:
    """Stop a CLI agent subprocess."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    force = params.get("force", False)

    try:
        result = await agent_controller.stop_agent(agent_id, force=force)
        return result
    except ValueError as e:
        raise ValueError(str(e))

async def handle_cli_agents_restart(params: Dict[str, Any]) -> Dict[str, Any]:
    """Restart a CLI agent."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    try:
        result = await agent_controller.restart_agent(agent_id)
        return result
    except ValueError as e:
        raise ValueError(str(e))

async def handle_cli_agents_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send a message to a CLI agent."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    message = params.get("message")

    if not agent_id:
        raise ValueError("'agent_id' parameter is required")
    if not message:
        raise ValueError("'message' parameter is required")

    timeout = params.get("timeout", 120)

    try:
        result = await agent_controller.call_agent(agent_id, message, timeout=timeout)
        return result
    except ValueError as e:
        raise ValueError(str(e))

async def handle_cli_agents_broadcast(params: Dict[str, Any]) -> Dict[str, Any]:
    """Broadcast a message to multiple CLI agents."""
    from .tristar.agent_controller import agent_controller

    message = params.get("message")
    if not message:
        raise ValueError("'message' parameter is required")

    agent_ids = params.get("agent_ids")  # Optional, None = all agents

    result = await agent_controller.broadcast(message, agent_ids=agent_ids)
    return result

async def handle_cli_agents_output(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get output buffer for a CLI agent."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    if not agent_id:
        raise ValueError("'agent_id' parameter is required")

    lines = params.get("lines", 50)
    output = await agent_controller.get_agent_output(agent_id, lines)

    return {
        "agent_id": agent_id,
        "output": output,
        "lines": len(output),
    }

async def handle_cli_agents_stats(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get CLI agent statistics."""
    from .tristar.agent_controller import agent_controller
    return await agent_controller.get_stats()

async def handle_cli_agents_update_prompt(params: Dict[str, Any]) -> Dict[str, Any]:
    """Update the system prompt for a CLI agent."""
    from .tristar.agent_controller import agent_controller

    agent_id = params.get("agent_id")
    prompt = params.get("prompt")

    if not agent_id:
        raise ValueError("'agent_id' parameter is required")
    if not prompt:
        raise ValueError("'prompt' parameter is required")

    try:
        result = await agent_controller.update_system_prompt(agent_id, prompt)
        return result
    except ValueError as e:
        raise ValueError(str(e))

async def handle_cli_agents_reload_prompts(params: Dict[str, Any]) -> Dict[str, Any]:
    """Reload system prompts from TriForce for all agents."""
    from .tristar.agent_controller import agent_controller
    return await agent_controller.reload_system_prompts()
# ============================================================================
# System & Compatibility Handlers
# ============================================================================

async def handle_check_compatibility(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle check_compatibility tool."""
    tools_response = await handle_tools_list({})
    tools = tools_response["tools"]
    return compatibility_layer.check_compatibility(tools)

async def handle_debug_mcp_request(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle debug_mcp_request tool."""
    return await mcp_debugger.debug_mcp_request(
        params.get("method", ""), params.get("params", {})
    )

async def handle_restart_backend(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle restart_backend tool."""
    return await system_control.restart_backend(params.get("delay", 2))

async def handle_restart_agent(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle restart_agent tool."""
    return await system_control.restart_agent(params.get("agent_id", ""))

async def handle_prompts_list_mcp(_: Dict[str, Any]) -> Dict[str, Any]:
    """MCP prompts/list method - returns available prompts (standard MCP protocol)."""
    templates = prompt_library.list_templates()
    return {
        "prompts": [
            {
                "name": t,
                "description": prompt_library.get_template_info(t).get("description", ""),
            }
            for t in templates
        ]
    }

async def handle_resources_list_mcp(_: Dict[str, Any]) -> Dict[str, Any]:
    """MCP resources/list method - returns available resources (standard MCP protocol)."""
    # Our server doesn't expose static resources, return empty list
    return {"resources": []}

async def handle_resources_read_mcp(params: Dict[str, Any]) -> Dict[str, Any]:
    """MCP resources/read method - read a specific resource."""
    uri = params.get("uri")
    if not uri:
        raise ValueError("'uri' parameter is required")
    # Our server doesn't expose static resources
    raise ValueError(f"Resource not found: {uri}")

async def handle_initialize(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility entry point; canonical MCP initialize lives in routes.mcp."""
    from ..routes.mcp import handle_initialize as canonical_initialize
    return await canonical_initialize(params)

async def handle_tools_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility entry point for the canonical MCP discovery surface.

    Keep this symbol for older imports, but never maintain a second tool schema
    inventory here. All discovery policy lives in app.routes.mcp and
    app.mcp.tool_registry_unified.
    """
    from ..routes.mcp import handle_tools_list as canonical_tools_list
    return await canonical_tools_list(params)

async def handle_tools_call(params: Dict[str, Any]) -> Dict[str, Any]:
    """Compatibility entry point for the canonical MCP tool dispatcher.

    Legacy callers importing app.services.mcp_service continue to work, while
    aliases, authorization-independent normalization and handler resolution are
    defined exactly once in app.routes.mcp.handle_tools_call.
    """
    from ..routes.mcp import handle_tools_call as canonical_tools_call
    return await canonical_tools_call(params)

# ============================================================================
# Remote Task Handlers - CLI Agents arbeiten auf Remote-Hosts
# ============================================================================

async def handle_remote_host_register(params: Dict[str, Any]) -> Dict[str, Any]:
    """Registriert einen Remote-Host für Task-Ausführung."""
    hostname = params.get("hostname")
    username = params.get("username")
    password = params.get("password")
    
    if not hostname or not username:
        raise ValueError("'hostname' and 'username' are required")
    
    host = remote_task_service.register_host(
        hostname=hostname,
        username=username,
        password=password,
        port=params.get("port", 22),
        description=params.get("description", "")
    )
    return host.to_dict()

async def handle_remote_host_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Listet alle registrierten Remote-Hosts."""
    return {"hosts": remote_task_service.list_hosts()}

async def handle_remote_task_submit(params: Dict[str, Any]) -> Dict[str, Any]:
    """Reicht einen Remote-Task ein - startet CLI-Agent der per SSH arbeitet."""
    host_id = params.get("host_id")
    task_type = params.get("task_type", "custom")
    description = params.get("description", "")
    agent_id = params.get("agent_id")
    
    if not host_id:
        raise ValueError("'host_id' is required")
    
    try:
        task_type_enum = TaskType(task_type)
    except ValueError:
        task_type_enum = TaskType.CUSTOM
    
    task = await remote_task_service.submit_task(
        host_id=host_id,
        task_type=task_type_enum,
        description=description,
        agent_id=agent_id
    )
    
    from dataclasses import asdict
    return asdict(task)

async def handle_remote_task_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Holt den Status eines Remote-Tasks."""
    task_id = params.get("task_id")
    if not task_id:
        raise ValueError("'task_id' is required")
    
    task = remote_task_service.get_task(task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")
    
    from dataclasses import asdict
    return asdict(task)

async def handle_remote_task_output(params: Dict[str, Any]) -> Dict[str, Any]:
    """Holt den Output eines laufenden/abgeschlossenen Tasks."""
    task_id = params.get("task_id")
    last_n = params.get("last_n", 50)
    
    if not task_id:
        raise ValueError("'task_id' is required")
    
    output = remote_task_service.get_task_output(task_id, last_n)
    return {"task_id": task_id, "output": output, "lines": len(output)}

async def handle_remote_task_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """Listet alle Tasks, optional gefiltert nach Host oder Status."""
    host_id = params.get("host_id")
    status = params.get("status")
    
    status_enum = None
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            pass
    
    tasks = remote_task_service.list_tasks(host_id=host_id, status=status_enum)
    return {"tasks": tasks, "count": len(tasks)}

# Remote Task Tools für handle_tools_list
REMOTE_TASK_TOOLS = [
    {
        "name": "remote_host_register",
        "description": "Registriert einen Remote-Host (PC/Server) für Task-Ausführung via SSH",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hostname": {"type": "string", "description": "IP oder Hostname des Remote-Systems"},
                "username": {"type": "string", "description": "SSH Username"},
                "password": {"type": "string", "description": "SSH Passwort"},
                "port": {"type": "integer", "description": "SSH Port (default: 22)"},
                "description": {"type": "string", "description": "Beschreibung des Hosts"}
            },
            "required": ["hostname", "username"]
        }
    },
    {
        "name": "remote_host_list",
        "description": "Listet alle registrierten Remote-Hosts",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "remote_task_submit",
        "description": "Startet einen Task auf einem Remote-Host. Der Server spawnt einen CLI-Agent (Claude/Codex/Gemini) der per SSH auf dem Host arbeitet.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Host-ID des Zielrechners"},
                "task_type": {
                    "type": "string",
                    "enum": ["gaming_optimize", "system_optimize", "analyze", "install", "configure", "debug", "custom"],
                    "description": "Art des Tasks"
                },
                "description": {"type": "string", "description": "Beschreibung/Details für den Task"},
                "agent_id": {"type": "string", "description": "Spezifischer Agent (default: auto)"}
            },
            "required": ["host_id"]
        }
    },
    {
        "name": "remote_task_status",
        "description": "Holt den Status eines Remote-Tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task-ID"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "remote_task_output",
        "description": "Holt den Live-Output eines Remote-Tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task-ID"},
                "last_n": {"type": "integer", "description": "Letzte N Zeilen (default: 50)"}
            },
            "required": ["task_id"]
        }
    },
    {
        "name": "remote_task_list",
        "description": "Listet alle Remote-Tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "host_id": {"type": "string", "description": "Filter nach Host"},
                "status": {"type": "string", "enum": ["pending", "running", "completed", "failed", "cancelled"]}
            }
        }
    }
]

REMOTE_TASK_HANDLERS = {
    "remote_host_register": handle_remote_host_register,
    "remote_host_list": handle_remote_host_list,
    "remote_task_submit": handle_remote_task_submit,
    "remote_task_status": handle_remote_task_status,
    "remote_task_output": handle_remote_task_output,
    "remote_task_list": handle_remote_task_list,
}

Handler = Callable[[Dict[str, Any]], Awaitable[Any]]
MCP_HANDLERS: Dict[str, Handler] = {
    # Standard MCP Protocol Methods (Slash notation - required for Gemini/Claude/Codex compatibility)
    "initialize": handle_initialize,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "prompts/list": handle_prompts_list_mcp,
    "prompts/get": handle_prompts_render,  # prompts/get maps to render
    "resources/list": handle_resources_list_mcp,
    "resources/read": handle_resources_read_mcp,

    # Original handlers
    "crawl.url": handle_crawl_url,
    "crawl.site": handle_crawl_site,
    "crawl.status": handle_crawl_status,
    "posts.create": handle_posts_create,
    "media.upload": handle_media_upload,
    "llm.invoke": handle_llm_invoke,
    "admin.crawler.control": handle_admin_control,
    "admin.crawler.config.get": handle_admin_config_get,
    "admin.crawler.config.set": handle_admin_config_set,

    # API Documentation (for Claude Code integration)
    "api.docs": handle_api_docs,
    "api.search": handle_api_search,

    # Translation Layer (API ↔ MCP)
    "translate": handle_translate,
    "translate.api_to_mcp": handle_api_to_mcp,
    "translate.mcp_to_api": handle_mcp_to_api,

    # Model Specialists
    "specialists.list": handle_specialists_list,
    "specialists.route": handle_specialists_route,
    "specialists.invoke": handle_specialists_invoke,

    # Context Management
    "context.create": handle_context_create,
    "context.get": handle_context_get,
    "context.message": handle_context_message,
    "context.list": handle_context_list,
    "context.clear": handle_context_clear,

    # Prompt Library
    "prompts.list": handle_prompts_list,
    "prompts.render": handle_prompts_render,
    "prompts.add": handle_prompts_add,

    # Workflow Orchestration
    "workflows.list": handle_workflows_list,
    "workflows.create": handle_workflows_create,
    "workflows.status": handle_workflows_status,

    # TriStar Integration (v2.80)
    "tristar.models": handle_tristar_models,
    "tristar.models.list": handle_tristar_models,
    "tristar.init": handle_tristar_init,
    "tristar.memory.store": handle_tristar_memory_store,
    "tristar.memory.search": handle_tristar_memory_search,

    # Codebase Access (v2.80)
    "codebase.structure": handle_codebase_structure,
    "codebase.file": handle_codebase_file,
    "codebase.search": handle_codebase_search,
    "codebase.routes": handle_codebase_routes,
    "codebase.services": handle_codebase_services,
    # Codebase Edit (v2.90) - Self-modification capability
    "codebase.edit": handle_codebase_edit,
    "codebase.create": handle_codebase_create,
    "codebase.backup": handle_codebase_backup,

    # CLI Agents (v2.80) - Claude, Codex, Gemini Subprocess Management
    "cli-agents.list": handle_cli_agents_list,
    "cli-agents.get": handle_cli_agents_get,
    "cli-agents.start": handle_cli_agents_start,
    "cli-agents.stop": handle_cli_agents_stop,
    "cli-agents.restart": handle_cli_agents_restart,
    "cli-agents.call": handle_cli_agents_call,
    "cli-agents.broadcast": handle_cli_agents_broadcast,
    "cli-agents.output": handle_cli_agents_output,
    "cli-agents.stats": handle_cli_agents_stats,
    "cli-agents.update-prompt": handle_cli_agents_update_prompt,
    "cli-agents.reload-prompts": handle_cli_agents_reload_prompts,
}
