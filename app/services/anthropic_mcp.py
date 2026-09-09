"""
Anthropic Claude API - Complete MCP Integration
================================================

Endpoints:
- POST /v1/messages - Chat/Vision/Tools
- POST /v1/messages (stream) - Streaming
- POST /v1/messages/count_tokens - Token counting
- GET /v1/models - List models
- GET /v1/models/{id} - Get model
- POST /v1/messages/batches - Create batch (50% cheaper)
- GET /v1/messages/batches - List batches
- GET /v1/messages/batches/{id} - Get batch
- POST /v1/messages/batches/{id}/cancel - Cancel batch
- GET /v1/messages/batches/{id}/results - Get results
- POST /v1/files (beta) - Upload file
- GET /v1/files (beta) - List files

API Docs: https://docs.anthropic.com/en/api/
"""

import os
import json
import httpx
from typing import Dict, Any, Optional, List
from datetime import datetime

ANTHROPIC_API_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


def _get_api_key() -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if api_key:
        return api_key
    try:
        from app.settings_store import load_snapshot
        return str(load_snapshot().values.get("ANTHROPIC_API_KEY") or "")
    except Exception:
        return ""


def _headers(beta: str = None) -> dict:
    h = {
        "x-api-key": _get_api_key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json"
    }
    if beta:
        h["anthropic-beta"] = beta
    return h


# ============== MESSAGES API ==============

async def handle_anthropic_chat(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/messages - Chat with Claude"""
    message = params.get("message")
    model = params.get("model", "claude-sonnet-4-6")
    system = params.get("system")
    max_tokens = params.get("max_tokens", 4096)
    temperature = params.get("temperature", 1.0)
    tools = params.get("tools")  # For tool use
    
    # Build messages
    messages = params.get("messages") or [{"role": "user", "content": message}]
    
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "temperature": temperature
    }
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/messages",
            headers=_headers(),
            json=payload
        )
        response.raise_for_status()
        result = response.json()
    
    text = result.get("content", [{}])[0].get("text", "")
    return {
        "text": text,
        "model": result.get("model"),
        "usage": result.get("usage"),
        "stop_reason": result.get("stop_reason")
    }


async def handle_anthropic_vision(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/messages - Analyze image with Claude Vision"""
    prompt = params.get("prompt")
    image_url = params.get("image_url")
    image_base64 = params.get("image_base64")
    media_type = params.get("media_type", "image/jpeg")
    model = params.get("model", "claude-sonnet-4-6")
    
    content = []
    if image_url:
        content.append({"type": "image", "source": {"type": "url", "url": image_url}})
    elif image_base64:
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}})
    content.append({"type": "text", "text": prompt})
    
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/messages",
            headers=_headers(),
            json={"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": content}]}
        )
        response.raise_for_status()
        result = response.json()
    
    return {"text": result.get("content", [{}])[0].get("text", ""), "usage": result.get("usage")}


async def handle_anthropic_count_tokens(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/messages/count_tokens - Count tokens before sending"""
    text = params.get("text")
    model = params.get("model", "claude-sonnet-4-6")
    system = params.get("system")
    
    payload = {"model": model, "messages": [{"role": "user", "content": text}]}
    if system:
        payload["system"] = system
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/messages/count_tokens",
            headers=_headers(),
            json=payload
        )
        response.raise_for_status()
        return response.json()


# ============== MODELS API ==============

async def handle_anthropic_models(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/models - List all available Claude models"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{ANTHROPIC_API_URL}/models", headers=_headers())
        response.raise_for_status()
        return response.json()


async def handle_anthropic_model_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/models/{model_id} - Get specific model details"""
    model_id = params.get("model_id")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{ANTHROPIC_API_URL}/models/{model_id}", headers=_headers())
        response.raise_for_status()
        return response.json()


# ============== BATCHES API (50% cheaper) ==============

async def handle_anthropic_batch_create(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/messages/batches - Create batch for async processing (50% cost reduction)"""
    requests = params.get("requests", [])  # List of {custom_id, params}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/messages/batches",
            headers=_headers(),
            json={"requests": requests}
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_batch_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/messages/batches - List all batches"""
    limit = params.get("limit", 20)
    before_id = params.get("before_id")
    after_id = params.get("after_id")
    
    url = f"{ANTHROPIC_API_URL}/messages/batches?limit={limit}"
    if before_id:
        url += f"&before_id={before_id}"
    if after_id:
        url += f"&after_id={after_id}"
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(url, headers=_headers())
        response.raise_for_status()
        return response.json()


async def handle_anthropic_batch_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/messages/batches/{batch_id} - Get batch status"""
    batch_id = params.get("batch_id")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{ANTHROPIC_API_URL}/messages/batches/{batch_id}",
            headers=_headers()
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_batch_cancel(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/messages/batches/{batch_id}/cancel - Cancel batch"""
    batch_id = params.get("batch_id")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/messages/batches/{batch_id}/cancel",
            headers=_headers()
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_batch_results(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/messages/batches/{batch_id}/results - Get batch results"""
    batch_id = params.get("batch_id")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(
            f"{ANTHROPIC_API_URL}/messages/batches/{batch_id}/results",
            headers=_headers()
        )
        response.raise_for_status()
        # Results are JSONL format
        results = []
        for line in response.text.strip().split("\n"):
            if line:
                results.append(json.loads(line))
        return {"results": results, "count": len(results)}


# ============== FILES API (Beta) ==============

async def handle_anthropic_file_upload(params: Dict[str, Any]) -> Dict[str, Any]:
    """POST /v1/files - Upload file (beta)"""
    file_path = params.get("file_path")
    file_data = params.get("file_data")  # Base64
    filename = params.get("filename", "upload.txt")
    purpose = params.get("purpose", "assistants")
    
    if file_path and os.path.exists(file_path):
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        filename = os.path.basename(file_path)
    elif file_data:
        import base64
        file_bytes = base64.b64decode(file_data)
    else:
        return {"error": "No file provided"}
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{ANTHROPIC_API_URL}/files",
            headers={"x-api-key": _get_api_key(), "anthropic-version": ANTHROPIC_VERSION, "anthropic-beta": "files-api-2025-04-14"},
            files={"file": (filename, file_bytes)},
            data={"purpose": purpose}
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_file_list(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/files - List uploaded files"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{ANTHROPIC_API_URL}/files",
            headers=_headers("files-api-2025-04-14")
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_file_get(params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /v1/files/{file_id} - Get file info"""
    file_id = params.get("file_id")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{ANTHROPIC_API_URL}/files/{file_id}",
            headers=_headers("files-api-2025-04-14")
        )
        response.raise_for_status()
        return response.json()


async def handle_anthropic_file_delete(params: Dict[str, Any]) -> Dict[str, Any]:
    """DELETE /v1/files/{file_id} - Delete file"""
    file_id = params.get("file_id")
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.delete(
            f"{ANTHROPIC_API_URL}/files/{file_id}",
            headers=_headers("files-api-2025-04-14")
        )
        response.raise_for_status()
        return {"deleted": True, "file_id": file_id}


# ============== TOOL DEFINITIONS ==============

ANTHROPIC_TOOLS = [
    # Messages
    {"name": "anthropic_chat", "description": "Chat with Claude AI. Supports system prompts, tool use, and multi-turn conversations",
     "inputSchema": {"type": "object", "properties": {
         "message": {"type": "string", "description": "Message to send"},
         "model": {"type": "string", "default": "claude-sonnet-4-6", "description": "claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5"},
         "system": {"type": "string", "description": "System prompt"},
         "max_tokens": {"type": "integer", "default": 4096},
         "temperature": {"type": "number", "default": 1.0},
         "tools": {"type": "array", "description": "Tools for function calling"},
         "messages": {"type": "array", "description": "Multi-turn conversation history"}
     }, "required": ["message"]}},
    
    {"name": "anthropic_vision", "description": "Analyze images with Claude Vision (JPEG, PNG, GIF, WebP)",
     "inputSchema": {"type": "object", "properties": {
         "prompt": {"type": "string"}, "image_url": {"type": "string"}, "image_base64": {"type": "string"},
         "media_type": {"type": "string", "default": "image/jpeg"}, "model": {"type": "string", "default": "claude-sonnet-4-6"}
     }, "required": ["prompt"]}},
    
    {"name": "anthropic_count_tokens", "description": "Count tokens before sending to manage costs",
     "inputSchema": {"type": "object", "properties": {
         "text": {"type": "string"}, "model": {"type": "string", "default": "claude-sonnet-4-6"},
         "system": {"type": "string"}
     }, "required": ["text"]}},
    
    # Models
    {"name": "anthropic_models", "description": "List all Claude models",
     "inputSchema": {"type": "object", "properties": {}}},
    
    {"name": "anthropic_model_get", "description": "Get specific model details",
     "inputSchema": {"type": "object", "properties": {"model_id": {"type": "string"}}, "required": ["model_id"]}},
    
    # Batches (50% cheaper)
    {"name": "anthropic_batch_create", "description": "Create async batch (50% cost reduction). Process up to 10,000 requests",
     "inputSchema": {"type": "object", "properties": {
         "requests": {"type": "array", "description": "Array of {custom_id, params} objects"}
     }, "required": ["requests"]}},
    
    {"name": "anthropic_batch_list", "description": "List all message batches",
     "inputSchema": {"type": "object", "properties": {
         "limit": {"type": "integer", "default": 20}, "before_id": {"type": "string"}, "after_id": {"type": "string"}
     }}},
    
    {"name": "anthropic_batch_get", "description": "Get batch status and details",
     "inputSchema": {"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]}},
    
    {"name": "anthropic_batch_cancel", "description": "Cancel a running batch",
     "inputSchema": {"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]}},
    
    {"name": "anthropic_batch_results", "description": "Get batch results (JSONL format)",
     "inputSchema": {"type": "object", "properties": {"batch_id": {"type": "string"}}, "required": ["batch_id"]}},
    
    # Files (Beta)
    {"name": "anthropic_file_upload", "description": "Upload file to Anthropic (beta)",
     "inputSchema": {"type": "object", "properties": {
         "file_path": {"type": "string"}, "file_data": {"type": "string", "description": "Base64 encoded"},
         "filename": {"type": "string"}, "purpose": {"type": "string", "default": "assistants"}
     }}},
    
    {"name": "anthropic_file_list", "description": "List uploaded files",
     "inputSchema": {"type": "object", "properties": {}}},
    
    {"name": "anthropic_file_get", "description": "Get file info",
     "inputSchema": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}},
    
    {"name": "anthropic_file_delete", "description": "Delete a file",
     "inputSchema": {"type": "object", "properties": {"file_id": {"type": "string"}}, "required": ["file_id"]}}
]


# Handler Mapping
ANTHROPIC_HANDLERS = {
    "anthropic_chat": handle_anthropic_chat,
    "anthropic_vision": handle_anthropic_vision,
    "anthropic_count_tokens": handle_anthropic_count_tokens,
    "anthropic_models": handle_anthropic_models,
    "anthropic_model_get": handle_anthropic_model_get,
    "anthropic_batch_create": handle_anthropic_batch_create,
    "anthropic_batch_list": handle_anthropic_batch_list,
    "anthropic_batch_get": handle_anthropic_batch_get,
    "anthropic_batch_cancel": handle_anthropic_batch_cancel,
    "anthropic_batch_results": handle_anthropic_batch_results,
    "anthropic_file_upload": handle_anthropic_file_upload,
    "anthropic_file_list": handle_anthropic_file_list,
    "anthropic_file_get": handle_anthropic_file_get,
    "anthropic_file_delete": handle_anthropic_file_delete,
}


# ============== EXTENDED FEATURES ==============

async def handle_anthropic_thinking(params):
    """Extended Thinking mit sichtbarem Denkprozess"""
    message = params.get("message")
    model = params.get("model", "claude-sonnet-4-6")
    budget = params.get("budget_tokens", 5000)
    
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{ANTHROPIC_API_URL}/messages",
            headers=_headers("interleaved-thinking-2025-05-14"),
            json={"model": model, "max_tokens": 8000,
                  "thinking": {"type": "enabled", "budget_tokens": budget},
                  "messages": [{"role": "user", "content": message}]})
        r.raise_for_status()
        result = r.json()
    
    thinking, text = "", ""
    for b in result.get("content", []):
        if b.get("type") == "thinking": thinking = b.get("thinking", "")
        elif b.get("type") == "text": text = b.get("text", "")
    return {"thinking": thinking, "text": text, "usage": result.get("usage")}

async def handle_anthropic_cite(params):
    """Dokument-Analyse mit Quellenangaben"""
    q, doc = params.get("question"), params.get("document")
    content = [{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": doc},
                "title": params.get("title", "Doc"), "citations": {"enabled": True}},
               {"type": "text", "text": q}]
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{ANTHROPIC_API_URL}/messages", headers=_headers(),
            json={"model": params.get("model", "claude-haiku-4-5-20251001"), "max_tokens": 2000,
                  "messages": [{"role": "user", "content": content}]})
        r.raise_for_status()
        res = r.json()
    ans, cites = "", []
    for b in res.get("content", []):
        if b.get("type") == "text": ans, cites = b.get("text", ""), b.get("citations", [])
    return {"answer": ans, "citations": cites}

async def handle_anthropic_compare(params):
    """Multi-Model-Vergleich"""
    msg = params.get("message")
    models = params.get("models", ["claude-haiku-4-5-20251001", "claude-sonnet-4-6"])
    results = {}
    import time
    async with httpx.AsyncClient(timeout=120.0) as client:
        for m in models:
            try:
                t0 = time.time()
                r = await client.post(f"{ANTHROPIC_API_URL}/messages", headers=_headers(),
                    json={"model": m, "max_tokens": 300, "messages": [{"role": "user", "content": msg}]})
                r.raise_for_status()
                res = r.json()
                results[m] = {"text": res.get("content", [{}])[0].get("text", ""),
                              "latency_ms": int((time.time()-t0)*1000), "usage": res.get("usage")}
            except Exception as e:
                results[m] = {"error": str(e)}
    return {"comparisons": results}

async def handle_anthropic_cost_estimate(params):
    """Kosten-Schätzung"""
    text, model = params.get("text"), params.get("model", "claude-sonnet-4-6")
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(f"{ANTHROPIC_API_URL}/messages/count_tokens", headers=_headers(),
            json={"model": model, "messages": [{"role": "user", "content": text}]})
        r.raise_for_status()
    inp = r.json().get("input_tokens", 0)
    out = params.get("expected_output", 500)
    prices = {"opus": (15, 75), "sonnet": (3, 15), "haiku": (0.8, 4)}
    p = prices.get("haiku" if "haiku" in model else "sonnet" if "sonnet" in model else "opus", (3, 15))
    cost = (inp/1e6)*p[0] + (out/1e6)*p[1]
    return {"input_tokens": inp, "expected_output": out, "cost_usd": round(cost, 6), "batch_cost": round(cost*0.5, 6)}

ANTHROPIC_HANDLERS.update({
    "anthropic_thinking": handle_anthropic_thinking,
    "anthropic_cite": handle_anthropic_cite,
    "anthropic_compare": handle_anthropic_compare,
    "anthropic_cost_estimate": handle_anthropic_cost_estimate,
})
