# fallback metadata contract: fallback_used attempted_models fallback_from fallback_to fallback_count
"""
API-based Agent Runner — Direct Provider API with Tool Calling
==============================================================
ReAct-loop agent calling provider APIs directly (Groq, OpenRouter, etc.)
with native OpenAI-compatible function calling.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("ailinux.api_agent")

# ── Provider configs ──────────────────────────────────────────────
PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "models": ["llama-3.3-70b-versatile"],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "models": ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3-super-120b-a12b:free", "qwen/qwen3-coder:free"],
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "env_key": "CEREBRAS_API_KEY",
        "models": ["llama-3.3-70b"],
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "env_key": "NVIDIA_API_KEY",
        "models": ["nvidia/nemotron-3-ultra-550b-a55b"],
    },
}

# Default tools
DEFAULT_AGENT_TOOLS = [
    "mail_inbox", "mail_read", "mail_send", "mail_mark_seen",
    "notify_send", "notify_list", "notify_read", "notify_status",
    "code_read", "code_search", "code_tree",
    "memory_search", "memory_store",
    "health", "status",
    "flarum_discussions", "flarum_discussion_get", "flarum_post_create",
    "search",
]

MODEL_PRIORITY = [
    "groq/llama-3.3-70b-versatile",
    "cerebras/llama-3.3-70b",
    "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free",
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
]


def _get_api_key(provider: str) -> str:
    cfg = PROVIDERS.get(provider, {})
    env_key = cfg.get("env_key", "")
    return os.getenv(env_key, "")


def _parse_model(model_str: str) -> tuple:
    """Parse 'provider/model' → (provider, model, base_url, api_key)"""
    parts = model_str.split("/", 1)
    if len(parts) != 2:
        return None, None, None, None
    provider, model = parts
    cfg = PROVIDERS.get(provider)
    if not cfg:
        # Ollama fallback — route through TriForce
        return provider, model, None, None
    api_key = _get_api_key(provider)
    return provider, model, cfg["base_url"], api_key


def _build_tool_schemas(tool_names: List[str]) -> List[Dict]:
    """Build OpenAI-compatible tool schemas from V5 registry."""
    try:
        from app.mcp.tool_registry_v5 import V5_TOOLS
        schemas = []
        for tool_def in V5_TOOLS:
            if tool_def["name"] in tool_names:
                input_schema = tool_def.get("inputSchema", {"type": "object", "properties": {}})
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool_def["name"],
                        "description": tool_def.get("description", "")[:300],
                        "parameters": input_schema,
                    }
                })
        return schemas
    except Exception as e:
        logger.warning(f"Failed to build tool schemas: {e}")
        return []


def _get_all_handlers() -> Dict:
    """Build combined handler map from all MCP modules."""
    handlers = {}
    # Prefer current multi-root code handlers over legacy codebase aliases.
    # The dev-path resolver still enforces approved workspace roots and blocks
    # sensitive components such as .ssh, .env, .git, secrets and credentials.
    try:
        from app.mcp.adaptive_code import handle_code_scout
        from app.services.mcp_service import handle_codebase_file, handle_codebase_search
        handlers.update({
            "code_tree": handle_code_scout,
            "code_read": handle_codebase_file,
            "code_search": handle_codebase_search,
        })
    except Exception as exc:
        logger.warning("Could not register multi-root code handlers: %s", exc)
    try:
        from app.mcp.structured_admin import STRUCTURED_ADMIN_HANDLERS
        handlers.update(STRUCTURED_ADMIN_HANDLERS)
    except Exception:
        pass
    try:
        from app.mcp.mail_tools import MAIL_TOOL_HANDLERS
        handlers.update(MAIL_TOOL_HANDLERS)
    except Exception:
        pass
    try:
        from app.mcp.notification_manager import handle_notify_list, handle_notify_read, handle_notify_send, handle_notify_clear, handle_notify_status
        handlers.update({
            "notify_list": handle_notify_list, "notify_read": handle_notify_read,
            "notify_send": handle_notify_send, "notify_clear": handle_notify_clear,
            "notify_status": handle_notify_status,
        })
    except Exception:
        pass
    try:
        from app.routes.mcp_remote import TOOL_HANDLERS as REMOTE_HANDLERS
        for k, v in REMOTE_HANDLERS.items():
            if k not in handlers:
                handlers[k] = v
    except Exception:
        pass
    try:
        from app.routes.mcp import MCP_HANDLERS
        for k, v in MCP_HANDLERS.items():
            if k not in handlers:
                handlers[k] = v
    except Exception:
        pass
    # Apply V5 aliases: register handlers under canonical V5 names
    try:
        from app.mcp.tool_registry_v5 import V5_ALIASES
        for old_name, canon_name in V5_ALIASES.items():
            if canon_name not in handlers and old_name in handlers:
                handlers[canon_name] = handlers[old_name]
    except Exception:
        pass
    return handlers


_handler_cache: Optional[Dict] = None


async def _execute_tool(tool_name: str, arguments: Dict) -> str:
    """Execute MCP tool directly in-process."""
    global _handler_cache
    if _handler_cache is None:
        _handler_cache = _get_all_handlers()
    handler = _handler_cache.get(tool_name)
    if not handler:
        return json.dumps({"error": f"Tool '{tool_name}' not found"})
    try:
        result = await handler(arguments)
        return json.dumps(result, ensure_ascii=False, default=str)[:4000]
    except Exception as e:
        return json.dumps({"error": f"Tool error: {str(e)[:200]}"})


async def _call_provider(
    base_url: str,
    api_key: str,
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    timeout: int = 30,
) -> Dict:
    """Direct OpenAI-compatible API call to provider."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.3,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
        if r.status_code != 200:
            raise Exception(f"Provider returned {r.status_code}: {r.text[:200]}")
        return r.json()


async def _call_ollama_local(
    model: str,
    messages: List[Dict],
    tools: Optional[List[Dict]] = None,
    timeout: int = 30,
) -> Dict:
    """Call Ollama local API with tool support."""
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post("http://localhost:11434/api/chat", json=payload)
        if r.status_code != 200:
            raise Exception(f"Ollama returned {r.status_code}: {r.text[:200]}")
        data = r.json()
        # Convert Ollama format → OpenAI format
        msg = data.get("message", {})
        tool_calls_raw = msg.get("tool_calls", [])
        tool_calls = []
        for i, tc in enumerate(tool_calls_raw):
            fn = tc.get("function", {})
            tool_calls.append({
                "id": f"call_{i}",
                "type": "function",
                "function": {
                    "name": fn.get("name", ""),
                    "arguments": json.dumps(fn.get("arguments", {})),
                }
            })
        return {
            "choices": [{
                "message": {
                    "content": msg.get("content", ""),
                    "tool_calls": tool_calls if tool_calls else None,
                }
            }]
        }


async def run_text_model(
    model: str,
    task: str,
    system_prompt: Optional[str] = None,
    timeout: int = 90,
) -> Dict[str, Any]:
    """Run one text-only model call without tool calling."""
    provider, model_name, base_url, api_key = _parse_model(model)
    if not provider:
        return {"status": "error", "model": model, "response": f"Invalid model: {model}"}

    messages: List[Dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": task})

    try:
        if base_url is None:
            result = await _call_ollama_local(model_name, messages, None, timeout)
        else:
            if not api_key:
                return {"status": "error", "model": model, "response": f"No API key for {provider}"}
            result = await _call_provider(base_url, api_key, model_name, messages, None, timeout)
    except Exception as e:
        return {"status": "error", "model": model, "response": str(e)[:300]}

    choices = result.get("choices", []) if isinstance(result, dict) else []
    if not choices:
        return {"status": "error", "model": model, "response": "No choices in response"}
    msg = choices[0].get("message", {})
    return {
        "status": "completed",
        "model": model,
        "response": str(msg.get("content") or "").strip(),
    }


async def run_api_agent(
    model: str,
    task: str,
    tools: Optional[List[str]] = None,
    system_prompt: Optional[str] = None,
    max_turns: int = 8,
    timeout: int = 120,
) -> Dict[str, Any]:
    """Run ReAct-loop agent with real tool calling."""
    start = time.time()
    provider, model_name, base_url, api_key = _parse_model(model)

    if not provider:
        return {"status": "error", "model": model, "response": f"Invalid model: {model}",
                "turns": 0, "tools_called": [], "elapsed_ms": 0}

    is_ollama = base_url is None
    if not is_ollama and not api_key:
        return {"status": "error", "model": model, "response": f"No API key for {provider}",
                "turns": 0, "tools_called": [], "elapsed_ms": 0}

    tool_names = tools or DEFAULT_AGENT_TOOLS
    tool_schemas = _build_tool_schemas(tool_names)
    tools_called = []

    if not system_prompt:
        system_prompt = (
            "Du bist Nova, ein autonomer KI-Agent von AILinux. "
            "Du hast Zugriff auf Tools um Aufgaben auszuführen. "
            "WICHTIG: Nutze die verfügbaren Tools aktiv — rufe sie auf statt zu raten. "
            "Wenn du fertig bist, antworte mit dem Ergebnis."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    final_response = ""

    for turn in range(max_turns):
        elapsed = time.time() - start
        if elapsed > timeout:
            return {"status": "timeout", "model": model, "response": final_response or "Timeout",
                    "turns": turn, "tools_called": tools_called, "elapsed_ms": int(elapsed * 1000)}

        try:
            remaining = max(10, int(timeout - elapsed))
            if is_ollama:
                result = await _call_ollama_local(model_name, messages, tool_schemas or None, remaining)
            else:
                result = await _call_provider(base_url, api_key, model_name, messages, tool_schemas or None, remaining)
        except Exception as e:
            logger.warning(f"API agent {model} turn {turn} failed: {e}")
            return {"status": "error", "model": model, "response": str(e)[:300],
                    "turns": turn, "tools_called": tools_called, "elapsed_ms": int((time.time() - start) * 1000)}

        choices = result.get("choices", [])
        if not choices:
            return {"status": "error", "model": model, "response": "No choices in response",
                    "turns": turn, "tools_called": tools_called, "elapsed_ms": int((time.time() - start) * 1000)}

        msg = choices[0].get("message", {})
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            final_response = content
            break

        # Append assistant message with tool calls. Ollama expects
        # function.arguments as an object, while OpenAI-compatible providers
        # use a JSON string. Normalize only for the Ollama conversation state.
        assistant_msg = {"role": "assistant"}
        if content:
            assistant_msg["content"] = content
        if is_ollama:
            ollama_tool_calls = []
            for tc in tool_calls:
                fn = dict(tc.get("function", {}))
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        raw_args = {}
                fn["arguments"] = raw_args
                ollama_tool_calls.append({"type": "function", "function": fn})
            assistant_msg["tool_calls"] = ollama_tool_calls
        else:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        # Execute each tool call
        for tc in tool_calls:
            fn = tc.get("function", {})
            fn_name = fn.get("name", "")
            fn_args_raw = fn.get("arguments", "{}")
            tc_id = tc.get("id", f"call_{turn}_{fn_name}")

            try:
                fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
            except json.JSONDecodeError:
                fn_args = {}

            # Security: only execute whitelisted tools
            if fn_name not in tool_names:
                tool_result = json.dumps({"error": f"Tool '{fn_name}' not allowed"})
            else:
                logger.info(f"API-Agent tool: {fn_name}({json.dumps(fn_args, ensure_ascii=False)[:80]})")
                tools_called.append(fn_name)
                tool_elapsed = time.time() - start
                tool_remaining = timeout - tool_elapsed
                if tool_remaining <= 0:
                    return {
                        "status": "timeout",
                        "model": model,
                        "response": final_response or "Timeout",
                        "turns": turn,
                        "tools_called": tools_called,
                        "elapsed_ms": int(tool_elapsed * 1000),
                    }
                tool_timeout = min(45.0, tool_remaining)
                try:
                    tool_result = await asyncio.wait_for(
                        _execute_tool(fn_name, fn_args),
                        timeout=tool_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning("API-Agent tool timeout: %s after %.1fs", fn_name, tool_timeout)
                    tool_result = json.dumps({
                        "error": f"Tool '{fn_name}' timed out after {int(tool_timeout)}s"
                    })

            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": tool_result,
            })
    else:
        final_response = content or "Max turns reached"
        return {"status": "max_turns", "model": model, "response": final_response,
                "turns": max_turns, "tools_called": tools_called, "elapsed_ms": int((time.time() - start) * 1000)}

    return {"status": "completed", "model": model, "response": final_response,
            "turns": turn + 1, "tools_called": tools_called, "elapsed_ms": int((time.time() - start) * 1000)}


async def run_api_agent_with_fallback(task: str, models: Optional[List[str]] = None, **kwargs) -> Dict[str, Any]:
    """Try multiple models in order until one succeeds."""
    model_list = models or MODEL_PRIORITY
    last_error = None
    for model in model_list:
        result = await run_api_agent(model=model, task=task, **kwargs)
        if result["status"] in ("completed", "max_turns"):
            return result
        last_error = result
        logger.info(f"API agent fallback: {model} → next...")
    return last_error or {"status": "error", "response": "All models failed"}
