"""
Anthropic Claude API Tool für TriForce MCP
Dokumentation: https://docs.anthropic.com/en/api/messages

Modelle:
- claude-opus-4-5-20251101 (stärkstes)
- claude-sonnet-4-5-20250929 (balanced)  
- claude-haiku-4-5-20251001 (schnellstes)
"""

import os
import httpx
from typing import Optional, List, Dict, Any

ANTHROPIC_API_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


def get_api_key() -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        env_path = "/home/zombie/workspace/triforce/config/triforce.env"
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.startswith("ANTHROPIC_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
    return api_key


class AnthropicAPI:
    def __init__(self):
        self.api_key = get_api_key()
        self.headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json"
        }
    
    async def chat(self, message: str, model: str = "claude-sonnet-4-5-20250929",
                   system: Optional[str] = None, max_tokens: int = 4096,
                   temperature: float = 1.0) -> Dict[str, Any]:
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": message}],
            "temperature": temperature
        }
        if system:
            payload["system"] = system
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{ANTHROPIC_API_URL}/messages",
                headers=self.headers, json=payload
            )
            response.raise_for_status()
            return response.json()
    
    async def vision(self, prompt: str, image_url: str = None,
                     image_base64: str = None, media_type: str = "image/jpeg",
                     model: str = "claude-sonnet-4-5-20250929") -> Dict[str, Any]:
        content = []
        if image_url:
            content.append({"type": "image", "source": {"type": "url", "url": image_url}})
        elif image_base64:
            content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_base64}})
        content.append({"type": "text", "text": prompt})
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{ANTHROPIC_API_URL}/messages",
                headers=self.headers,
                json={"model": model, "max_tokens": 4096, "messages": [{"role": "user", "content": content}]}
            )
            response.raise_for_status()
            return response.json()
    
    async def count_tokens(self, text: str, model: str = "claude-sonnet-4-5-20250929") -> Dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{ANTHROPIC_API_URL}/messages/count_tokens",
                headers=self.headers,
                json={"model": model, "messages": [{"role": "user", "content": text}]}
            )
            response.raise_for_status()
            return response.json()
    
    async def list_models(self) -> Dict:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{ANTHROPIC_API_URL}/models", headers=self.headers)
            response.raise_for_status()
            return response.json()


# MCP Tool Definitions
TOOL_DEFINITIONS = [
    {"name": "anthropic_chat", "description": "Chat with Claude (opus/sonnet/haiku)",
     "parameters": {"type": "object", "properties": {
         "message": {"type": "string"}, "model": {"type": "string", "default": "claude-sonnet-4-5-20250929"},
         "system": {"type": "string"}, "max_tokens": {"type": "integer", "default": 4096}
     }, "required": ["message"]}},
    {"name": "anthropic_vision", "description": "Analyze images with Claude Vision",
     "parameters": {"type": "object", "properties": {
         "prompt": {"type": "string"}, "image_url": {"type": "string"}, "image_base64": {"type": "string"}
     }, "required": ["prompt"]}},
    {"name": "anthropic_count_tokens", "description": "Count tokens in text",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "anthropic_models", "description": "List Claude models",
     "parameters": {"type": "object", "properties": {}}}
]


async def handle_tool(tool_name: str, params: dict) -> dict:
    api = AnthropicAPI()
    try:
        if tool_name == "anthropic_chat":
            r = await api.chat(params["message"], params.get("model", "claude-sonnet-4-5-20250929"),
                               params.get("system"), params.get("max_tokens", 4096))
            return {"text": r.get("content", [{}])[0].get("text", ""), "usage": r.get("usage")}
        elif tool_name == "anthropic_vision":
            r = await api.vision(params["prompt"], params.get("image_url"), params.get("image_base64"))
            return {"text": r.get("content", [{}])[0].get("text", "")}
        elif tool_name == "anthropic_count_tokens":
            return await api.count_tokens(params["text"])
        elif tool_name == "anthropic_models":
            return await api.list_models()
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import asyncio
    async def test():
        api = AnthropicAPI()
        r = await api.chat("Say 'Hello TriForce!' briefly")
        print(f"Response: {r.get('content', [{}])[0].get('text')}")
    asyncio.run(test())
