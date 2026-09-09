# app/services/chat_router.py
"""
Chat Router - Intelligente Model-Auswahl für Chat-Anfragen
Routet zu lokalen Ollama-Modellen oder Cloud-APIs

Implementierung für TriForce Backend
Stand: 2025-12-13
"""

import asyncio
import json
import re
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from enum import Enum
import logging
import aiohttp

logger = logging.getLogger(__name__)


class ModelProvider(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    GEMINI = "gemini"
    MISTRAL = "mistral"
    GROQ = "groq"
    CEREBRAS = "cerebras"


@dataclass
class ModelConfig:
    """Model-Konfiguration"""
    provider: ModelProvider
    model_id: str
    display_name: str
    context_length: int = 8192
    strengths: List[str] = None  # ["code", "reasoning", "creative", "fast"]
    cost_tier: str = "free"  # free, low, medium, high
    
    @property
    def full_id(self) -> str:
        return f"{self.provider.value}/{self.model_id}"


# Verfügbare Modelle
AVAILABLE_MODELS = {
    # === Lokale Modelle (Ollama) ===
    "ollama/qwen2.5:14b": ModelConfig(
        provider=ModelProvider.OLLAMA,
        model_id="qwen2.5:14b",
        display_name="Qwen 2.5 14B",
        context_length=32768,
        strengths=["code", "reasoning", "multilingual"],
        cost_tier="free"
    ),
    "ollama/llama3.2:latest": ModelConfig(
        provider=ModelProvider.OLLAMA,
        model_id="llama3.2:latest",
        display_name="Llama 3.2",
        context_length=8192,
        strengths=["general", "fast"],
        cost_tier="free"
    ),
    "ollama/codellama:13b": ModelConfig(
        provider=ModelProvider.OLLAMA,
        model_id="codellama:13b",
        display_name="Code Llama 13B",
        context_length=16384,
        strengths=["code"],
        cost_tier="free"
    ),
    "ollama/mixtral:8x7b": ModelConfig(
        provider=ModelProvider.OLLAMA,
        model_id="mixtral:8x7b",
        display_name="Mixtral 8x7B",
        context_length=32768,
        strengths=["reasoning", "multilingual"],
        cost_tier="free"
    ),
    "ollama/deepseek-coder:6.7b": ModelConfig(
        provider=ModelProvider.OLLAMA,
        model_id="deepseek-coder:6.7b",
        display_name="DeepSeek Coder",
        context_length=16384,
        strengths=["code"],
        cost_tier="free"
    ),
    
    # === Cloud APIs ===
    "openai/gpt-5.5": ModelConfig(
        provider=ModelProvider.OPENAI,
        model_id="gpt-5.5",
        display_name="GPT-5.5",
        context_length=128000,
        strengths=["reasoning", "code", "creative", "vision"],
        cost_tier="high"
    ),
    "openai/gpt-5.4-mini": ModelConfig(
        provider=ModelProvider.OPENAI,
        model_id="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        context_length=128000,
        strengths=["general", "fast"],
        cost_tier="low"
    ),
    "anthropic/claude-sonnet-4-6": ModelConfig(
        provider=ModelProvider.ANTHROPIC,
        model_id="claude-sonnet-4-6",
        display_name="Claude Sonnet 4.6",
        context_length=200000,
        strengths=["code", "reasoning", "creative"],
        cost_tier="medium"
    ),
    "anthropic/claude-haiku-4-20250514": ModelConfig(
        provider=ModelProvider.ANTHROPIC,
        model_id="claude-haiku-4-20250514",
        display_name="Claude Haiku 4",
        context_length=200000,
        strengths=["fast", "general"],
        cost_tier="low"
    ),
    "gemini/gemini-3.5-flash": ModelConfig(
        provider=ModelProvider.GEMINI,
        model_id="gemini-3.5-flash",
        display_name="Gemini 3.5 Flash",
        context_length=1000000,
        strengths=["fast", "reasoning", "vision"],
        cost_tier="low"
    ),
    "gemini/gemini-2.5-pro": ModelConfig(
        provider=ModelProvider.GEMINI,
        model_id="gemini-2.5-pro",
        display_name="Gemini 2.5 Pro",
        context_length=2000000,
        strengths=["reasoning", "code", "creative"],
        cost_tier="medium"
    ),
    "mistral/mistral-medium-3.5": ModelConfig(
        provider=ModelProvider.MISTRAL,
        model_id="mistral-medium-3.5",
        display_name="Mistral Medium 3.5",
        context_length=128000,
        strengths=["reasoning", "multilingual"],
        cost_tier="medium"
    ),
    "groq/llama-3.3-70b-versatile": ModelConfig(
        provider=ModelProvider.GROQ,
        model_id="llama-3.3-70b-versatile",
        display_name="Llama 3.3 70B (Groq)",
        context_length=128000,
        strengths=["fast", "reasoning"],
        cost_tier="low"
    ),
    "cerebras/llama-3.3-70b": ModelConfig(
        provider=ModelProvider.CEREBRAS,
        model_id="llama-3.3-70b",
        display_name="Llama 3.3 70B (Cerebras)",
        context_length=8192,
        strengths=["ultra-fast"],
        cost_tier="low"
    ),
}


class ChatRouter:
    """
    Intelligenter Chat Router
    
    Wählt automatisch das beste Model basierend auf:
    - Anfrage-Typ (Code, Kreativ, Recherche, etc.)
    - Nachrichtenlänge
    - Verfügbare API Keys
    - Kosten-Präferenz
    """
    
    def __init__(self, ollama_url: str = "http://localhost:11434"):
        self.ollama_url = ollama_url
        self.default_local = "ollama/qwen2.5:14b"
        self.default_cloud = "anthropic/claude-sonnet-4-6"
    
    def detect_task_type(self, message: str) -> List[str]:
        """Erkennt den Task-Typ aus der Nachricht"""
        message_lower = message.lower()
        types = []
        
        # Code-Indikatoren
        code_patterns = [
            r'\b(code|script|function|class|debug|fix|error|bug|implement|python|javascript|bash|sql)\b',
            r'```',
            r'\b(def |class |import |from |const |let |var )\b'
        ]
        if any(re.search(p, message_lower) for p in code_patterns):
            types.append("code")
        
        # Kreativ-Indikatoren
        creative_patterns = [
            r'\b(schreib|write|story|gedicht|poem|kreativ|creative|erzähl|tell me a)\b',
            r'\b(blog|artikel|article|essay)\b'
        ]
        if any(re.search(p, message_lower) for p in creative_patterns):
            types.append("creative")
        
        # Recherche-Indikatoren
        research_patterns = [
            r'\b(such|search|find|recherch|was ist|what is|erklär|explain|vergleich|compare)\b',
            r'\?$'
        ]
        if any(re.search(p, message_lower) for p in research_patterns):
            types.append("research")
        
        # Reasoning-Indikatoren
        reasoning_patterns = [
            r'\b(analysier|analyze|berechne|calculate|warum|why|logic|math|proof)\b'
        ]
        if any(re.search(p, message_lower) for p in reasoning_patterns):
            types.append("reasoning")
        
        # Falls nichts erkannt → general
        if not types:
            types.append("general")
        
        return types
    
    async def select_model(
        self,
        message: str,
        preferences: Dict[str, Any] = None
    ) -> str:
        """
        Wählt das beste Model für die Anfrage
        
        Args:
            message: Die Nachricht
            preferences: Optional:
                - model: Explizite Model-Wahl
                - prefer_local: Lokale Modelle bevorzugen
                - prefer_cloud: Cloud-APIs bevorzugen
                - cost_limit: max cost_tier (free, low, medium, high)
                - strength: Gewünschte Stärke (code, creative, fast, etc.)
        
        Returns:
            Model-ID (z.B. "ollama/qwen2.5:14b")
        """
        preferences = preferences or {}
        
        # Explizite Wahl?
        if preferences.get("model"):
            model = preferences["model"]
            if model in AVAILABLE_MODELS:
                return model
            # Partial match versuchen
            for model_id in AVAILABLE_MODELS:
                if model in model_id:
                    return model_id
        
        # Task-Typ erkennen
        task_types = self.detect_task_type(message)
        
        # Präferenzen auswerten
        prefer_local = preferences.get("prefer_local", False)
        prefer_cloud = preferences.get("prefer_cloud", False)
        cost_limit = preferences.get("cost_limit", "medium")
        required_strength = preferences.get("strength")
        
        # Kandidaten filtern
        candidates = []
        for model_id, config in AVAILABLE_MODELS.items():
            # Cost-Filter
            cost_order = {"free": 0, "low": 1, "medium": 2, "high": 3}
            if cost_order.get(config.cost_tier, 0) > cost_order.get(cost_limit, 2):
                continue
            
            # Local/Cloud Präferenz
            if prefer_local and config.provider != ModelProvider.OLLAMA:
                continue
            if prefer_cloud and config.provider == ModelProvider.OLLAMA:
                continue
            
            # Strength-Filter
            if required_strength and required_strength not in (config.strengths or []):
                continue
            
            candidates.append((model_id, config))
        
        if not candidates:
            # Fallback
            return self.default_local if prefer_local else self.default_cloud
        
        # Scoring
        def score_model(item):
            model_id, config = item
            score = 0
            
            # Stärken-Match
            for task_type in task_types:
                if task_type in (config.strengths or []):
                    score += 10
            
            # Kurze Nachrichten → schnelle Modelle
            if len(message) < 100:
                if "fast" in (config.strengths or []):
                    score += 5
                if config.provider == ModelProvider.OLLAMA:
                    score += 3  # Lokal ist schneller bei kurzen Anfragen
            
            # Lange Nachrichten → große Context-Länge
            if len(message) > 2000:
                score += config.context_length // 10000
            
            # Code-Tasks → Code-Modelle
            if "code" in task_types:
                if config.provider == ModelProvider.ANTHROPIC:
                    score += 8  # Claude ist gut für Code
                if "code" in (config.strengths or []):
                    score += 5
            
            # Kosten-Präferenz (günstigere bevorzugen)
            cost_scores = {"free": 5, "low": 3, "medium": 1, "high": 0}
            score += cost_scores.get(config.cost_tier, 0)
            
            return score
        
        # Beste Wahl
        candidates.sort(key=score_model, reverse=True)
        return candidates[0][0]
    
    def get_available_models(self, include_cloud: bool = True) -> List[dict]:
        """Liste verfügbarer Modelle"""
        models = []
        for model_id, config in AVAILABLE_MODELS.items():
            if not include_cloud and config.provider != ModelProvider.OLLAMA:
                continue
            models.append({
                "id": model_id,
                "display_name": config.display_name,
                "provider": config.provider.value,
                "strengths": config.strengths,
                "cost_tier": config.cost_tier,
                "context_length": config.context_length
            })
        return models


class APIProxy:
    """
    Proxy für Cloud-API-Aufrufe
    Nutzt Keys aus dem Vault
    """
    
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=120)
    
    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096
    ) -> str:
        """
        Chat mit Cloud-API
        
        Args:
            model: Model-ID (z.B. "openai/gpt-5.5")
            messages: Chat-Messages [{role, content}]
            temperature: Sampling-Temperatur
            max_tokens: Max Output Tokens
        
        Returns:
            Assistant-Antwort als String
        """
        from .api_vault import api_vault

        # ENV-Fallback: Wenn Vault locked/uninitialisiert ODER Key nicht im Vault,
        # versuche os.environ. Konsistent mit handlers_v4 und nova_frontend.
        # Reaktiviert 2026-06-15 — Vault wurde nie initialisiert, blockt sonst Group Chat.
        import os
        _PROVIDER_ENV = {
            "openai":    ("OPENAI_API_KEY",),
            "anthropic": ("ANTHROPIC_API_KEY",),
            "google":    ("GEMINI_API_KEY", "GOOGLE_GEMINI_KEY"),
            "gemini":    ("GEMINI_API_KEY", "GOOGLE_GEMINI_KEY"),
            "mistral":   ("MISTRAL_API_KEY",),
            "groq":      ("GROQ_API_KEY",),
            "cerebras":  ("CEREBRAS_API_KEY",),
            "openrouter":("OPENROUTER_API_KEY",),
        }

        provider, model_id = model.split("/", 1)

        api_key = None
        if api_vault.is_unlocked:
            api_key = api_vault.get_key(provider)

        if not api_key:
            for env_name in _PROVIDER_ENV.get(provider, ()):
                v = os.environ.get(env_name)
                if v:
                    api_key = v
                    break

        if not api_key:
            raise RuntimeError(
                f"No API key for provider '{provider}' "
                f"(vault {'unlocked' if api_vault.is_unlocked else 'locked'}, "
                f"ENV checked: {_PROVIDER_ENV.get(provider, ())})"
            )
        
        # Provider-spezifische Implementierung
        if provider == "openai":
            return await self._openai_chat(api_key, model_id, messages, temperature, max_tokens)
        elif provider == "anthropic":
            return await self._anthropic_chat(api_key, model_id, messages, temperature, max_tokens)
        elif provider in ("google", "gemini"):
            return await self._gemini_chat(api_key, model_id, messages, temperature, max_tokens)
        elif provider == "mistral":
            return await self._mistral_chat(api_key, model_id, messages, temperature, max_tokens)
        elif provider == "groq":
            return await self._groq_chat(api_key, model_id, messages, temperature, max_tokens)
        elif provider == "cerebras":
            return await self._cerebras_chat(api_key, model_id, messages, temperature, max_tokens)
        else:
            raise RuntimeError(f"Unknown provider: {provider}")
    
    async def _openai_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """OpenAI API Call"""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tokens
                }
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"OpenAI API error: {error}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    
    async def _anthropic_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Anthropic API Call"""
        # System-Message extrahieren
        system = None
        chat_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system = msg["content"]
            else:
                chat_messages.append(msg)
        
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            body = {
                "model": model,
                "messages": chat_messages,
                "temperature": temp,
                "max_tokens": max_tokens
            }
            if system:
                # Prompt-Caching: convert long system prompts into cacheable block.
                # Threshold ~4000 chars (≈1024 tokens, Anthropic minimum for cache hit
                # on Sonnet/Opus). Cache TTL 5 min, write=1.25x, read=0.1x normal cost.
                # Patched 2026-06-04.
                if isinstance(system, str) and len(system) >= 4000:
                    body["system"] = [{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }]
                else:
                    body["system"] = system
            
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01"
                },
                json=body
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Anthropic API error: {error}")
                data = await resp.json()
                return data["content"][0]["text"]
    
    async def _gemini_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Google Gemini API Call"""
        system_parts = []
        contents = []
        for msg in messages:
            if msg["role"] == "system":
                system_parts.append(msg["content"])
                continue
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temp,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}

        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json=payload,
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Gemini API error: {error}")
                data = await resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
    
    async def _mistral_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Mistral API Call"""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                "https://api.mistral.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tokens
                }
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Mistral API error: {error}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    
    async def _groq_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Groq API Call"""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tokens
                }
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Groq API error: {error}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]
    
    async def _cerebras_chat(self, api_key: str, model: str, messages: list, temp: float, max_tokens: int) -> str:
        """Cerebras API Call"""
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(
                "https://api.cerebras.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tokens
                }
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    raise RuntimeError(f"Cerebras API error: {error}")
                data = await resp.json()
                return data["choices"][0]["message"]["content"]


# Singletons
chat_router = ChatRouter()
api_proxy = APIProxy()


# =============================================================================
# MCP Tool Handlers
# =============================================================================

async def handle_chat_smart(params: dict) -> dict:
    """Intelligenter Chat - automatische Model-Wahl"""
    message = params.get("message")
    if not message:
        return {"error": "message required"}
    
    preferences = {
        "prefer_local": params.get("prefer_local", False),
        "prefer_cloud": params.get("prefer_cloud", False),
        "cost_limit": params.get("cost_limit", "medium"),
        "strength": params.get("strength"),
        "model": params.get("model")
    }
    
    # Model auswählen
    model = await chat_router.select_model(message, preferences)
    
    # Chat ausführen
    messages = [{"role": "user", "content": message}]
    
    if params.get("system_prompt"):
        messages.insert(0, {"role": "system", "content": params["system_prompt"]})
    
    config = AVAILABLE_MODELS.get(model)
    
    try:
        if config and config.provider == ModelProvider.OLLAMA:
            # Lokales Ollama-Model
            # Hier würde der bestehende Ollama-Handler aufgerufen
            from app.services.ollama_mcp import ollama_mcp
            result = await ollama_mcp.chat(
                model=config.model_id,
                messages=messages
            )
            response = result.get("message", {}).get("content", "")
        else:
            # Cloud-API
            response = await api_proxy.chat(model, messages)
        
        return {
            "model_used": model,
            "response": response,
            "provider": config.provider.value if config else "unknown"
        }
        
    except Exception as e:
        return {"error": str(e), "model_attempted": model}


async def handle_chat_list_models(params: dict) -> dict:
    """Verfügbare Modelle auflisten"""
    include_cloud = params.get("include_cloud", True)
    models = chat_router.get_available_models(include_cloud)
    return {"models": models, "count": len(models)}


# Tool-Definitionen
CHAT_ROUTER_TOOLS = [
    {
        "name": "chat_smart",
        "description": "Intelligenter Chat - wählt automatisch das beste Model (lokal oder Cloud)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Die Nachricht"},
                "model": {"type": "string", "description": "Optional: Explizite Model-Wahl"},
                "system_prompt": {"type": "string", "description": "Optional: System-Prompt"},
                "prefer_local": {"type": "boolean", "default": False},
                "prefer_cloud": {"type": "boolean", "default": False},
                "cost_limit": {
                    "type": "string",
                    "enum": ["free", "low", "medium", "high"],
                    "default": "medium"
                },
                "strength": {
                    "type": "string",
                    "enum": ["code", "creative", "reasoning", "fast", "general"],
                    "description": "Gewünschte Stärke des Models"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "chat_list_models",
        "description": "Verfügbare Chat-Modelle auflisten (lokal und Cloud)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_cloud": {"type": "boolean", "default": True}
            }
        }
    }
]

CHAT_ROUTER_HANDLERS = {
    "chat_smart": handle_chat_smart,
    "chat_list_models": handle_chat_list_models,
}
