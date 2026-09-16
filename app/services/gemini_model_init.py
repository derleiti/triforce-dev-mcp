"""
Gemini Model Init Service
==========================

Gemini Lead initialisiert lokale (Ollama) und Cloud-Modelle
mit System-Prompts über /v1/ und /mcp/ Endpoints.

Version: 1.0.0
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("ailinux.gemini_model_init")


# ============================================================================
# MODEL REGISTRY
# ============================================================================

# Lokale Ollama-Modelle
OLLAMA_MODELS = {
    "qwen2.5:14b": {
        "role": "worker",
        "capabilities": ["code", "reasoning", "german"],
        "system_prompt_key": "qwen-worker",
    },
    "qwen2.5:32b": {
        "role": "lead",
        "capabilities": ["code", "reasoning", "german", "complex"],
        "system_prompt_key": "qwen-worker",
    },
    "deepseek-r1:14b": {
        "role": "reasoning",
        "capabilities": ["reasoning", "math", "analysis"],
        "system_prompt_key": "deepseek-worker",
    },
    "cogito:latest": {
        "role": "reviewer",
        "capabilities": ["review", "analysis"],
        "system_prompt_key": "cogito-reviewer",
    },
    "mistral:latest": {
        "role": "reviewer",
        "capabilities": ["review", "code", "multilingual"],
        "system_prompt_key": "mistral-reviewer",
    },
    "llama3.2:latest": {
        "role": "worker",
        "capabilities": ["general", "code"],
        "system_prompt_key": None,  # Default prompt
    },
}

# Cloud-Modelle (via Mesh)
CLOUD_MODELS = {
    "gemini/gemini-3.5-flash": {
        "role": "lead",
        "provider": "gemini",
        "capabilities": ["reasoning", "code", "vision", "search"],
        "system_prompt_key": "gemini-lead",
    },
    "gemini/gemini-1.5-pro": {
        "role": "lead",
        "provider": "gemini",
        "capabilities": ["reasoning", "code", "long-context"],
        "system_prompt_key": "gemini-lead",
    },
    "mistral/mistral-medium-3.5": {
        "role": "reviewer",
        "provider": "mistral",
        "capabilities": ["code", "review", "multilingual"],
        "system_prompt_key": "mistral-reviewer",
    },
    "anthropic/claude-sonnet-4": {
        "role": "worker",
        "provider": "anthropic",
        "capabilities": ["code", "reasoning", "analysis"],
        "system_prompt_key": "claude-worker",
    },
}

GEMINI_MODEL_IDS = {
    "flash": "gemini/gemini-3.5-flash",
    "standard": "gemini/gemini-3.5-flash",
}


# ============================================================================
# GEMINI MODEL INIT SERVICE
# ============================================================================

class GeminiModelInitService:
    """
    Service für Gemini Lead zur Initialisierung aller Modelle.

    Gemini Lead:
    1. Ruft /mcp/init für sich selbst auf
    2. Initialisiert CLI Agents mit System-Prompts
    3. Initialisiert Ollama-Modelle mit System-Prompts
    4. Registriert Cloud-Modelle im Mesh
    """

    def __init__(self, base_url: str = "http://localhost:9100"):
        self.base_url = base_url
        self._initialized_models: Dict[str, Dict[str, Any]] = {}
        self._init_lock = asyncio.Lock()
        self._client: Optional[httpx.AsyncClient] = None

    def _create_function_declarations(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Map tool definitions to function declarations and log missing keys."""
        declarations: List[Dict[str, Any]] = []

        for tool in tools:
            try:
                name = tool["name"]
                schema = tool["inputSchema"]
            except KeyError as exc:
                logger.warning("Tool declaration missing key %s: %s", exc, tool)
                continue

            declarations.append({
                "name": name,
                "description": tool.get("description", ""),
                "parameters": schema,
            })

        return declarations

    def get_model(self, quality: str = "flash") -> Optional[str]:
        """Return configured Gemini model id for the requested quality."""
        quality_key = (quality or "flash").lower()
        model_id = GEMINI_MODEL_IDS.get(quality_key) or GEMINI_MODEL_IDS.get("flash")
        if not model_id:
            logger.warning("No Gemini model id configured for quality '%s'", quality)
            return None
        if model_id not in CLOUD_MODELS:
            logger.warning("Requested model id %s not found in CLOUD_MODELS", model_id)
            return None
        return model_id

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=30.0,
                limits=httpx.Limits(max_connections=20),
            )
        return self._client

    async def close(self):
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_system_prompt(self, prompt_key: str) -> Optional[str]:
        """Holt System-Prompt aus TriStar"""
        if not prompt_key:
            return None

        try:
            client = await self._get_client()
            response = await client.get(f"/v1/tristar/prompts/{prompt_key}")
            if response.status_code == 200:
                data = response.json()
                return data.get("content")
        except Exception as e:
            logger.warning(f"Failed to get prompt {prompt_key}: {e}")

        return None

    async def init_ollama_model(
        self,
        model_name: str,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Initialisiert ein Ollama-Modell mit System-Prompt.

        Sendet eine Init-Message an das Modell um es zu "wecken"
        und den System-Prompt zu setzen.
        """
        model_config = OLLAMA_MODELS.get(model_name, {})

        # System-Prompt holen falls nicht übergeben
        if not system_prompt and model_config.get("system_prompt_key"):
            system_prompt = await self.get_system_prompt(
                model_config["system_prompt_key"]
            )

        # Default System-Prompt falls keiner vorhanden
        if not system_prompt:
            system_prompt = f"""Du bist ein TriStar Worker-Modell ({model_name}).
Nutze das Shortcode-Protokoll v2.0 für Kommunikation.
Rufe /mcp/init für vollständige Dokumentation ab."""

        try:
            client = await self._get_client()

            # Init-Call an Ollama
            response = await client.post(
                "/v1/ollama/chat",
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": "INIT: Bestätige Bereitschaft mit 'READY'"},
                    ],
                    "options": {"temperature": 0.1},
                },
            )

            if response.status_code == 200:
                result = response.json()
                self._initialized_models[model_name] = {
                    "status": "initialized",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "role": model_config.get("role", "worker"),
                    "capabilities": model_config.get("capabilities", []),
                }
                logger.info(f"Initialized Ollama model: {model_name}")
                return {
                    "success": True,
                    "model": model_name,
                    "response": result.get("response", "")[:100],
                }
            else:
                logger.warning(f"Failed to init {model_name}: {response.status_code}")
                return {
                    "success": False,
                    "model": model_name,
                    "error": f"HTTP {response.status_code}",
                }

        except Exception as e:
            logger.error(f"Error initializing {model_name}: {e}")
            return {
                "success": False,
                "model": model_name,
                "error": str(e),
            }

    async def init_cloud_model(
        self,
        model_id: str,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Registriert ein Cloud-Modell im Mesh.

        Cloud-Modelle werden nicht direkt initialisiert,
        sondern im Mesh registriert für spätere Nutzung.
        """
        model_config = CLOUD_MODELS.get(model_id, {})

        # System-Prompt holen
        if not system_prompt and model_config.get("system_prompt_key"):
            system_prompt = await self.get_system_prompt(
                model_config["system_prompt_key"]
            )

        try:
            # Registriere im Mesh
            self._initialized_models[model_id] = {
                "status": "registered",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "provider": model_config.get("provider", "unknown"),
                "role": model_config.get("role", "worker"),
                "capabilities": model_config.get("capabilities", []),
                "system_prompt": system_prompt[:200] if system_prompt else None,
            }

            logger.info(f"Registered cloud model: {model_id}")
            return {
                "success": True,
                "model": model_id,
                "status": "registered",
            }

        except Exception as e:
            logger.error(f"Error registering {model_id}: {e}")
            return {
                "success": False,
                "model": model_id,
                "error": str(e),
            }

    async def init_cli_agent(
        self,
        agent_id: str,
        prompt_version: str = "v4",
    ) -> Dict[str, Any]:
        """
        Initialisiert einen CLI Agent mit /init Aufruf.

        Sendet Shortcode an den Agent:
        @g>@{agent} !init
        """
        # Mapping agent_id zu alias
        agent_aliases = {
            "claude-mcp": "c",
            "codex-mcp": "x",
            "gemini-mcp": "g",
            "mistral-mcp": "m",
            "deepseek-mcp": "d",
            "nova-mcp": "n",
        }

        alias = agent_aliases.get(agent_id)
        if not alias:
            return {
                "success": False,
                "agent": agent_id,
                "error": "Unknown agent",
            }

        try:
            client = await self._get_client()

            # Sende Init-Shortcode
            shortcode = f"@g>@{alias} !init"

            response = await client.post(
                f"/v1/tristar/cli-agents/{agent_id}/call",
                json={
                    "message": shortcode,
                    "timeout": 30,
                },
            )

            if response.status_code == 200:
                result = response.json()
                self._initialized_models[agent_id] = {
                    "status": "initialized",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "type": "cli_agent",
                    "prompt_version": prompt_version,
                }
                logger.info(f"Initialized CLI agent: {agent_id}")
                return {
                    "success": True,
                    "agent": agent_id,
                    "response": result.get("output", "")[:100],
                }
            else:
                return {
                    "success": False,
                    "agent": agent_id,
                    "error": f"HTTP {response.status_code}",
                }

        except Exception as e:
            logger.error(f"Error initializing agent {agent_id}: {e}")
            return {
                "success": False,
                "agent": agent_id,
                "error": str(e),
            }

    async def init_all(
        self,
        include_ollama: bool = True,
        include_cloud: bool = True,
        include_cli: bool = True,
    ) -> Dict[str, Any]:
        """
        Initialisiert alle Modelle und Agents.

        Reihenfolge:
        1. CLI Agents (parallel)
        2. Ollama-Modelle (parallel)
        3. Cloud-Modelle (registrieren)
        """
        async with self._init_lock:
            results = {
                "cli_agents": [],
                "ollama_models": [],
                "cloud_models": [],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # 1. CLI Agents
            if include_cli:
                cli_tasks = [
                    self.init_cli_agent(agent_id)
                    for agent_id in ["claude-mcp", "codex-mcp", "mistral-mcp"]
                ]
                cli_results = await asyncio.gather(*cli_tasks, return_exceptions=True)
                results["cli_agents"] = [
                    r if isinstance(r, dict) else {"error": str(r)}
                    for r in cli_results
                ]

            # 2. Ollama-Modelle
            if include_ollama:
                ollama_tasks = [
                    self.init_ollama_model(model_name)
                    for model_name in OLLAMA_MODELS.keys()
                ]
                ollama_results = await asyncio.gather(*ollama_tasks, return_exceptions=True)
                results["ollama_models"] = [
                    r if isinstance(r, dict) else {"error": str(r)}
                    for r in ollama_results
                ]

            # 3. Cloud-Modelle
            if include_cloud:
                cloud_tasks = [
                    self.init_cloud_model(model_id)
                    for model_id in CLOUD_MODELS.keys()
                ]
                cloud_results = await asyncio.gather(*cloud_tasks, return_exceptions=True)
                results["cloud_models"] = [
                    r if isinstance(r, dict) else {"error": str(r)}
                    for r in cloud_results
                ]

            # Summary
            results["summary"] = {
                "cli_success": sum(1 for r in results["cli_agents"] if r.get("success")),
                "cli_total": len(results["cli_agents"]),
                "ollama_success": sum(1 for r in results["ollama_models"] if r.get("success")),
                "ollama_total": len(results["ollama_models"]),
                "cloud_success": sum(1 for r in results["cloud_models"] if r.get("success")),
                "cloud_total": len(results["cloud_models"]),
            }

            return results

    def get_initialized_models(self) -> Dict[str, Any]:
        """Gibt alle initialisierten Modelle zurück"""
        return {
            "models": self._initialized_models,
            "count": len(self._initialized_models),
            "by_type": {
                "cli_agent": [k for k, v in self._initialized_models.items() if v.get("type") == "cli_agent"],
                "ollama": [k for k, v in self._initialized_models.items() if k in OLLAMA_MODELS],
                "cloud": [k for k, v in self._initialized_models.items() if k in CLOUD_MODELS],
            },
        }


# Singleton
gemini_model_init = GeminiModelInitService()


# ============================================================================
# MCP TOOLS
# ============================================================================

MODEL_INIT_TOOLS = [
    {
        "name": "gemini_init_all",
        "description": "Gemini Lead initialisiert alle Modelle und CLI Agents",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_ollama": {"type": "boolean", "default": True},
                "include_cloud": {"type": "boolean", "default": True},
                "include_cli": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "gemini_init_model",
        "description": "Initialisiert ein spezifisches Modell",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model name oder ID"},
                "system_prompt": {"type": "string", "description": "Optional custom prompt"},
            },
            "required": ["model"],
        },
    },
    {
        "name": "gemini_get_models",
        "description": "Gibt alle initialisierten Modelle zurück",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


async def handle_gemini_init_all(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini_init_all tool"""
    return await gemini_model_init.init_all(
        include_ollama=params.get("include_ollama", True),
        include_cloud=params.get("include_cloud", True),
        include_cli=params.get("include_cli", True),
    )


async def handle_gemini_init_model(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini_init_model tool"""
    model = params.get("model")
    if not model:
        raise ValueError("'model' is required")

    system_prompt = params.get("system_prompt")

    # Bestimme Modell-Typ
    if model in OLLAMA_MODELS:
        return await gemini_model_init.init_ollama_model(model, system_prompt)
    elif model in CLOUD_MODELS:
        return await gemini_model_init.init_cloud_model(model, system_prompt)
    elif model.endswith("-mcp"):
        return await gemini_model_init.init_cli_agent(model)
    else:
        return {
            "success": False,
            "error": f"Unknown model: {model}",
            "available_ollama": list(OLLAMA_MODELS.keys()),
            "available_cloud": list(CLOUD_MODELS.keys()),
        }


async def handle_gemini_get_models(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini_get_models tool"""
    return gemini_model_init.get_initialized_models()


MODEL_INIT_HANDLERS = {
    "gemini_init_all": handle_gemini_init_all,
    "gemini_init_model": handle_gemini_init_model,
    "gemini_get_models": handle_gemini_get_models,
}
