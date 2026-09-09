"""
TriStar Settings Controller
Persistent settings management with model temperatures, priorities, and agent configs.
Writes back to both JSON and .env files.
Includes API Keys management with base64 obfuscation.
"""
from __future__ import annotations

import json
import logging
import os
import asyncio
import base64
import hashlib
from pathlib import Path

from app.settings_store import resolve_config_path, save_updates
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger(__name__)

# Base paths
TRISTAR_BASE = Path(os.getenv("TRISTAR_BASE", "/var/tristar"))
SETTINGS_FILE = TRISTAR_BASE / "settings" / "global_settings.json"
SECRETS_FILE = TRISTAR_BASE / "settings" / ".secrets.json"  # Separate file for API keys

# Simple obfuscation key (not true encryption, but prevents casual reading)
_OBFUSCATION_KEY = "tristar-ailinux-2024"


def _obfuscate(value: str) -> str:
    """Simple obfuscation for API keys (base64 + XOR)"""
    if not value:
        return ""
    key_bytes = (_OBFUSCATION_KEY * ((len(value) // len(_OBFUSCATION_KEY)) + 1)).encode()
    xored = bytes(a ^ b for a, b in zip(value.encode(), key_bytes))
    return base64.b64encode(xored).decode()


def _deobfuscate(value: str) -> str:
    """Reverse obfuscation"""
    if not value:
        return ""
    try:
        xored = base64.b64decode(value.encode())
        key_bytes = (_OBFUSCATION_KEY * ((len(xored) // len(_OBFUSCATION_KEY)) + 1)).encode()
        return bytes(a ^ b for a, b in zip(xored, key_bytes)).decode()
    except Exception:
        return value  # Return as-is if deobfuscation fails


@dataclass
class ModelConfig:
    """Configuration for a single model"""
    model_id: str
    temperature: float = 0.7
    priority: int = 50  # 0-100, higher = preferred
    enabled: bool = True
    max_tokens: int = 4096
    top_p: float = 0.9
    context_window: int = 8192
    tags: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class AgentConfig:
    """Configuration for a CLI agent"""
    agent_id: str
    display_name: str
    default_model: str = ""
    temperature: float = 0.7
    system_prompt_override: str = ""
    enabled: bool = True
    priority: int = 50
    auto_start: bool = False
    timeout_seconds: int = 120


@dataclass
class APIKeyConfig:
    """Configuration for API keys (stored separately with obfuscation)"""
    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    ollama_bearer_token: str = ""
    ollama_bearer_auth_enabled: bool = True

    # Gemini
    gemini_api_key: str = ""
    gemini_project_id: str = ""

    # Anthropic Claude
    anthropic_api_key: str = ""
    anthropic_max_tokens: int = 8192

    # Mistral
    mistral_api_key: str = ""
    mistral_organization_id: str = ""

    # Hugging Face
    huggingface_api_key: str = ""
    huggingface_inference_url: str = "https://router.huggingface.co/hf-inference"

    # GPT-OSS
    gpt_oss_api_key: str = ""
    gpt_oss_base_url: str = ""

    # =========================================================================
    # NEW FREE TIER PROVIDERS (2025)
    # =========================================================================

    # Groq (Fastest Inference - 300+ tokens/sec, 14,400 requests/day free)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_default_model: str = "llama-3.3-70b-versatile"
    groq_timeout_ms: int = 30000

    # Cerebras (1M tokens/day FREE, 20x faster than GPU)
    cerebras_api_key: str = ""
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    cerebras_default_model: str = "llama3.1-70b"
    cerebras_timeout_ms: int = 30000

    # Cohere (Best RAG & Embeddings, 1,000 requests/day free)
    cohere_api_key: str = ""
    cohere_default_model: str = "command-r-plus"
    cohere_embed_model: str = "embed-multilingual-v3.0"
    cohere_timeout_ms: int = 60000

    # OpenRouter (300+ models, one API key, $5 free + free models)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_default_model: str = "nvidia/nemotron-3-ultra-550b-a55b:free"
    openrouter_timeout_ms: int = 120000

    # Together AI ($25 free credits, 3 months unlimited FLUX.1)
    together_api_key: str = ""
    together_base_url: str = "https://api.together.xyz/v1"
    together_default_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    together_timeout_ms: int = 120000

    # Fireworks AI ($1 free credits)
    fireworks_api_key: str = ""
    fireworks_base_url: str = "https://api.fireworks.ai/inference/v1"
    fireworks_default_model: str = "accounts/fireworks/models/llama-v3p3-70b-instruct"
    fireworks_timeout_ms: int = 60000

    # Cloudflare Workers AI (10,000 neurons/day free)
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    cloudflare_default_model: str = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

    # Jina AI (Free Embeddings)
    jina_api_key: str = ""
    jina_embed_model: str = "jina-embeddings-v3"

    # =========================================================================
    # EXISTING SERVICES
    # =========================================================================

    # WordPress
    wordpress_url: str = ""
    wordpress_user: str = ""
    wordpress_password: str = ""

    # Stable Diffusion / ComfyUI
    stable_diffusion_url: str = "http://localhost:7860"
    stable_diffusion_username: str = ""
    stable_diffusion_password: str = ""
    stable_diffusion_api_key: str = ""
    comfyui_url: str = ""

    # TriStar GUI Auth
    tristar_gui_user: str = "admin"
    tristar_gui_password: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"


@dataclass
class FallbackConfig:
    """Fallback and routing configuration"""
    # Provider fallback order (comma-separated)
    # groq/cerebras are fastest, gemini is most reliable
    fallback_order: str = "gemini,groq,cerebras,ollama,mistral,cohere,openrouter,anthropic,together,fireworks,huggingface"

    # Enable automatic fallback on error
    auto_fallback_enabled: bool = True

    # Max retries before fallback
    max_retries_before_fallback: int = 2

    # Retry delay (exponential backoff base in seconds)
    retry_delay_base: float = 1.0

    # Timeout before trying fallback (seconds)
    fallback_timeout: float = 30.0

    # Provider-specific fallback models
    gemini_fallback_model: str = "ollama/qwen3:30b-cloud"
    anthropic_fallback_model: str = "gemini/gemini-2.0-flash"
    ollama_fallback_model: str = "gemini/gemini-2.0-flash"
    mistral_fallback_model: str = "ollama/mistral:7b"

    # Circuit breaker settings
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 5  # failures before opening
    circuit_breaker_timeout: int = 60  # seconds before half-open


@dataclass
class CachingConfig:
    """Caching configuration"""
    # Enable response caching
    cache_enabled: bool = True

    # Cache backend (memory, redis)
    cache_backend: str = "redis"

    # Default TTL in seconds
    cache_ttl_seconds: int = 3600

    # Max cache entries (memory backend)
    cache_max_entries: int = 1000

    # Cache identical prompts
    cache_identical_prompts: bool = True

    # Cache embedding results
    cache_embeddings: bool = True
    cache_embeddings_ttl: int = 86400  # 24 hours

    # Exclude models from caching (comma-separated)
    cache_exclude_models: str = ""

    # Min prompt length to cache
    cache_min_prompt_length: int = 10


@dataclass
class GlobalSettings:
    """All global settings"""
    # Core
    request_timeout: float = 30.0
    ollama_timeout_ms: int = 120000
    max_concurrent_requests: int = 8
    request_queue_timeout: float = 15.0

    # Default temperatures by provider
    default_temperature_ollama: float = 0.7
    default_temperature_gemini: float = 0.8
    default_temperature_mistral: float = 0.7
    default_temperature_anthropic: float = 0.7
    default_temperature_huggingface: float = 0.7
    default_temperature_groq: float = 0.7
    default_temperature_cerebras: float = 0.7
    default_temperature_cohere: float = 0.7
    default_temperature_openrouter: float = 0.7
    default_temperature_together: float = 0.7
    default_temperature_fireworks: float = 0.7
    default_temperature_cloudflare: float = 0.6

    # Rate limits
    rate_limit_requests_per_minute: int = 60
    rate_limit_tokens_per_minute: int = 100000
    rate_limit_per_user: bool = True
    rate_limit_per_ip: bool = True

    # Chain/Orchestration
    chain_max_cycles: int = 20
    chain_default_aggressive: bool = False
    chain_timeout_seconds: int = 300
    chain_auto_summarize: bool = True

    # Memory
    memory_max_entries: int = 10000
    memory_min_confidence: float = 0.3
    memory_auto_prune: bool = True
    memory_shard_count: int = 12

    # Logging
    log_level: str = "INFO"
    log_retention_days: int = 7
    log_max_entries: int = 50000
    log_include_prompts: bool = False  # Privacy: don't log full prompts by default
    log_include_responses: bool = False

    # GUI
    gui_theme: str = "dark"
    gui_auto_refresh_seconds: int = 5
    gui_show_debug: bool = False
    gui_language: str = "de"

    # Crawler
    crawler_enabled: bool = True
    crawler_max_memory_bytes: int = 268435456
    crawler_flush_interval: int = 3600
    crawler_retention_days: int = 30
    crawler_max_pages_per_job: int = 100
    crawler_user_agent: str = "AILinux-Crawler/2.80"

    # WordPress
    wordpress_category_id: int = 1
    wordpress_auto_publish: bool = False
    wordpress_default_status: str = "draft"

    # Model defaults
    default_chat_model: str = "gemini/gemini-2.0-flash"
    default_code_model: str = "anthropic/claude-sonnet-4"
    default_embedding_model: str = "ollama/nomic-embed-text"
    default_vision_model: str = "gemini/gemini-2.0-flash"
    default_summary_model: str = "ollama/qwen3:14b"

    # Security
    security_max_prompt_length: int = 100000
    security_max_response_length: int = 200000
    security_block_code_execution: bool = False
    security_sanitize_outputs: bool = True

    # CORS
    cors_allowed_origins: str = ""

    # Stable Diffusion
    stable_diffusion_backend: str = "comfyui"
    stable_diffusion_default_models: str = "sd_xl_base_1.0.safetensors"
    stable_diffusion_poll_interval: float = 1.0
    stable_diffusion_max_wait: float = 120.0

    # Timestamps
    last_modified: str = ""
    modified_by: str = ""


class SettingsController:
    """Controller for persistent settings management"""

    def __init__(self):
        self._settings: GlobalSettings = GlobalSettings()
        self._api_keys: APIKeyConfig = APIKeyConfig()
        self._fallback: FallbackConfig = FallbackConfig()
        self._caching: CachingConfig = CachingConfig()
        self._model_configs: Dict[str, ModelConfig] = {}
        self._agent_configs: Dict[str, AgentConfig] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Load settings from disk"""
        if self._initialized:
            return

        async with self._lock:
            # Ensure directories exist
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)

            if SETTINGS_FILE.exists():
                try:
                    data = json.loads(SETTINGS_FILE.read_text())
                    self._load_from_dict(data)
                    logger.info(f"Loaded settings from {SETTINGS_FILE}")
                except Exception as e:
                    logger.error(f"Failed to load settings: {e}")
                    self._create_defaults()
            else:
                self._create_defaults()
                await self._save_to_disk()

            # Load API keys from separate secrets file
            if SECRETS_FILE.exists():
                try:
                    secrets_data = json.loads(SECRETS_FILE.read_text())
                    self._load_api_keys(secrets_data)
                    logger.info(f"Loaded API keys from {SECRETS_FILE}")
                except Exception as e:
                    logger.error(f"Failed to load API keys: {e}")
            else:
                # Try to load from .env file
                self._load_api_keys_from_env()

            self._initialized = True

    def _create_defaults(self) -> None:
        """Create default settings"""
        self._settings = GlobalSettings()
        self._api_keys = APIKeyConfig()
        self._fallback = FallbackConfig()
        self._caching = CachingConfig()

        # Default agent configs
        self._agent_configs = {
            "gemini-mcp": AgentConfig(
                agent_id="gemini-mcp",
                display_name="Gemini Lead",
                default_model="gemini/gemini-2.0-flash",
                temperature=0.8,
                priority=90,
                auto_start=True
            ),
            "claude-mcp": AgentConfig(
                agent_id="claude-mcp",
                display_name="Claude Code",
                default_model="anthropic/claude-sonnet-4",
                temperature=0.7,
                priority=85,
                auto_start=True
            ),
            "codex-mcp": AgentConfig(
                agent_id="codex-mcp",
                display_name="Codex",
                default_model="ollama/qwen2.5-coder:14b",
                temperature=0.6,
                priority=70,
                auto_start=False
            ),
            "opencode-mcp": AgentConfig(
                agent_id="opencode-mcp",
                display_name="OpenCode",
                default_model="ollama/deepseek-coder-v2:16b",
                temperature=0.7,
                priority=60,
                auto_start=False
            ),
        }

        logger.info("Created default settings")

    def _load_api_keys(self, data: Dict[str, Any]) -> None:
        """Load API keys from secrets data (deobfuscate)"""
        sensitive_fields = {
            "gemini_api_key", "anthropic_api_key", "mistral_api_key",
            "huggingface_api_key", "gpt_oss_api_key", "ollama_bearer_token",
            "wordpress_password", "stable_diffusion_password",
            "stable_diffusion_api_key", "tristar_gui_password",
            # New providers (2025)
            "groq_api_key", "cerebras_api_key", "cohere_api_key",
            "openrouter_api_key", "together_api_key", "fireworks_api_key",
            "cloudflare_api_token", "jina_api_key"
        }

        for key, value in data.items():
            if hasattr(self._api_keys, key):
                # Deobfuscate sensitive fields
                if key in sensitive_fields and value:
                    value = _deobfuscate(value)
                setattr(self._api_keys, key, value)

    def _load_api_keys_from_env(self) -> None:
        """Load API keys from environment variables / .env file"""
        env_mapping = {
            "OLLAMA_BASE": "ollama_base_url",
            "OLLAMA_BEARER_TOKEN": "ollama_bearer_token",
            "GEMINI_API_KEY": "gemini_api_key",
            "ANTHROPIC_API_KEY": "anthropic_api_key",
            "MISTRAL_API_KEY": "mistral_api_key",
            "HUGGINGFACE_API_KEY": "huggingface_api_key",
            "GPT_OSS_API_KEY": "gpt_oss_api_key",
            "GPT_OSS_BASE_URL": "gpt_oss_base_url",
            # New providers (2025)
            "GROQ_API_KEY": "groq_api_key",
            "GROQ_BASE_URL": "groq_base_url",
            "GROQ_DEFAULT_MODEL": "groq_default_model",
            "CEREBRAS_API_KEY": "cerebras_api_key",
            "CEREBRAS_BASE_URL": "cerebras_base_url",
            "CEREBRAS_DEFAULT_MODEL": "cerebras_default_model",
            "COHERE_API_KEY": "cohere_api_key",
            "COHERE_DEFAULT_MODEL": "cohere_default_model",
            "COHERE_EMBED_MODEL": "cohere_embed_model",
            "OPENROUTER_API_KEY": "openrouter_api_key",
            "OPENROUTER_BASE_URL": "openrouter_base_url",
            "OPENROUTER_DEFAULT_MODEL": "openrouter_default_model",
            "TOGETHER_API_KEY": "together_api_key",
            "TOGETHER_BASE_URL": "together_base_url",
            "TOGETHER_DEFAULT_MODEL": "together_default_model",
            "FIREWORKS_API_KEY": "fireworks_api_key",
            "FIREWORKS_BASE_URL": "fireworks_base_url",
            "FIREWORKS_DEFAULT_MODEL": "fireworks_default_model",
            "CLOUDFLARE_ACCOUNT_ID": "cloudflare_account_id",
            "CLOUDFLARE_API_TOKEN": "cloudflare_api_token",
            "CLOUDFLARE_DEFAULT_MODEL": "cloudflare_default_model",
            "JINA_API_KEY": "jina_api_key",
            "JINA_EMBED_MODEL": "jina_embed_model",
            # Existing services
            "WORDPRESS_URL": "wordpress_url",
            "WORDPRESS_USER": "wordpress_user",
            "WORDPRESS_PASSWORD": "wordpress_password",
            "STABLE_DIFFUSION_URL": "stable_diffusion_url",
            "STABLE_DIFFUSION_USERNAME": "stable_diffusion_username",
            "STABLE_DIFFUSION_PASSWORD": "stable_diffusion_password",
            "COMFYUI_URL": "comfyui_url",
            "TRISTAR_GUI_USER": "tristar_gui_user",
            "TRISTAR_GUI_PASSWORD": "tristar_gui_password",
            "REDIS_URL": "redis_url",
        }

        for env_key, attr_name in env_mapping.items():
            value = os.getenv(env_key, "")
            if value and hasattr(self._api_keys, attr_name):
                setattr(self._api_keys, attr_name, value)

        logger.info("Loaded API keys from environment")

    def _load_from_dict(self, data: Dict[str, Any]) -> None:
        """Load settings from dictionary"""
        # Global settings
        global_data = data.get("global", {})
        for key, value in global_data.items():
            if hasattr(self._settings, key):
                setattr(self._settings, key, value)

        # Fallback config
        fallback_data = data.get("fallback", {})
        for key, value in fallback_data.items():
            if hasattr(self._fallback, key):
                setattr(self._fallback, key, value)

        # Caching config
        caching_data = data.get("caching", {})
        for key, value in caching_data.items():
            if hasattr(self._caching, key):
                setattr(self._caching, key, value)

        # Model configs
        self._model_configs = {}
        for model_id, config in data.get("models", {}).items():
            self._model_configs[model_id] = ModelConfig(
                model_id=model_id,
                **{k: v for k, v in config.items() if k != "model_id"}
            )

        # Agent configs
        self._agent_configs = {}
        for agent_id, config in data.get("agents", {}).items():
            self._agent_configs[agent_id] = AgentConfig(
                agent_id=agent_id,
                **{k: v for k, v in config.items() if k != "agent_id"}
            )

    def _to_dict(self) -> Dict[str, Any]:
        """Convert settings to dictionary"""
        return {
            "global": asdict(self._settings),
            "fallback": asdict(self._fallback),
            "caching": asdict(self._caching),
            "models": {k: asdict(v) for k, v in self._model_configs.items()},
            "agents": {k: asdict(v) for k, v in self._agent_configs.items()},
            "metadata": {
                "version": "2.80",
                "saved_at": datetime.now().isoformat(),
            }
        }

    def _api_keys_to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """Convert API keys to dictionary, optionally masking secrets"""
        sensitive_fields = {
            "gemini_api_key", "anthropic_api_key", "mistral_api_key",
            "huggingface_api_key", "gpt_oss_api_key", "ollama_bearer_token",
            "wordpress_password", "stable_diffusion_password",
            "stable_diffusion_api_key", "tristar_gui_password",
            # New providers (2025)
            "groq_api_key", "cerebras_api_key", "cohere_api_key",
            "openrouter_api_key", "together_api_key", "fireworks_api_key",
            "cloudflare_api_token", "jina_api_key"
        }

        result = asdict(self._api_keys)

        if mask_secrets:
            for key in sensitive_fields:
                if key in result and result[key]:
                    # Show only last 4 characters
                    value = result[key]
                    if len(value) > 4:
                        result[key] = "*" * (len(value) - 4) + value[-4:]
                    else:
                        result[key] = "****"

        return result

    def _save_api_keys_to_disk(self) -> None:
        """Save API keys to secrets file (obfuscated)"""
        sensitive_fields = {
            "gemini_api_key", "anthropic_api_key", "mistral_api_key",
            "huggingface_api_key", "gpt_oss_api_key", "ollama_bearer_token",
            "wordpress_password", "stable_diffusion_password",
            "stable_diffusion_api_key", "tristar_gui_password",
            # New providers (2025)
            "groq_api_key", "cerebras_api_key", "cohere_api_key",
            "openrouter_api_key", "together_api_key", "fireworks_api_key",
            "cloudflare_api_token", "jina_api_key"
        }

        data = asdict(self._api_keys)

        # Obfuscate sensitive fields
        for key in sensitive_fields:
            if key in data and data[key]:
                data[key] = _obfuscate(data[key])

        try:
            SECRETS_FILE.parent.mkdir(parents=True, exist_ok=True)
            SECRETS_FILE.write_text(json.dumps(data, indent=2))
            # Set restrictive permissions
            SECRETS_FILE.chmod(0o600)
            logger.info(f"Saved API keys to {SECRETS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save API keys: {e}")

    async def _save_to_disk(self) -> None:
        """Save settings to JSON file"""
        try:
            data = self._to_dict()
            SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            logger.info(f"Saved settings to {SETTINGS_FILE}")
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")

    async def _update_env_file(self, updates: Dict[str, Any]) -> None:
        """Persist backend env settings through the canonical TriForce store."""
        if not updates:
            return
        try:
            target = resolve_config_path()
            save_updates({key: str(value) for key, value in updates.items()}, path=target)
            logger.info("Updated canonical TriForce config %s with %d values", target, len(updates))
        except Exception as e:
            logger.error("Failed to update canonical TriForce config: %s", e)
            raise

    # =========================================================================
    # Global Settings
    # =========================================================================

    async def get_all_settings(self) -> Dict[str, Any]:
        """Get all settings"""
        await self.initialize()
        return self._to_dict()

    async def get_global_settings(self) -> Dict[str, Any]:
        """Get global settings only"""
        await self.initialize()
        return asdict(self._settings)

    async def update_global_settings(self, updates: Dict[str, Any], modified_by: str = "web-ui") -> Dict[str, Any]:
        """Update global settings"""
        await self.initialize()

        async with self._lock:
            env_updates = {}

            for key, value in updates.items():
                if hasattr(self._settings, key):
                    setattr(self._settings, key, value)

                    # Map to env variable names
                    env_key = key.upper()
                    if key == "request_timeout":
                        env_updates["REQUEST_TIMEOUT"] = str(value)
                    elif key == "ollama_timeout_ms":
                        env_updates["OLLAMA_TIMEOUT_MS"] = str(value)
                    elif key == "max_concurrent_requests":
                        env_updates["MAX_CONCURRENT_REQUESTS"] = str(value)
                    elif key == "crawler_enabled":
                        env_updates["CRAWLER_ENABLED"] = str(value).lower()
                    elif key == "crawler_max_memory_bytes":
                        env_updates["CRAWLER_MAX_MEMORY_BYTES"] = str(value)
                    elif key == "log_level":
                        env_updates["LOG_LEVEL"] = value

            self._settings.last_modified = datetime.now().isoformat()
            self._settings.modified_by = modified_by

            await self._save_to_disk()
            if env_updates:
                await self._update_env_file(env_updates)

            # Update runtime environment
            for key, value in env_updates.items():
                os.environ[key] = value

        return asdict(self._settings)

    # =========================================================================
    # Model Settings
    # =========================================================================

    async def get_model_configs(self) -> Dict[str, Dict[str, Any]]:
        """Get all model configurations"""
        await self.initialize()
        return {k: asdict(v) for k, v in self._model_configs.items()}

    async def get_model_config(self, model_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific model"""
        await self.initialize()
        config = self._model_configs.get(model_id)
        return asdict(config) if config else None

    async def set_model_config(
        self,
        model_id: str,
        temperature: Optional[float] = None,
        priority: Optional[int] = None,
        enabled: Optional[bool] = None,
        max_tokens: Optional[int] = None,
        top_p: Optional[float] = None,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Set or update model configuration"""
        await self.initialize()

        async with self._lock:
            if model_id not in self._model_configs:
                self._model_configs[model_id] = ModelConfig(model_id=model_id)

            config = self._model_configs[model_id]

            if temperature is not None:
                config.temperature = max(0.0, min(2.0, temperature))
            if priority is not None:
                config.priority = max(0, min(100, priority))
            if enabled is not None:
                config.enabled = enabled
            if max_tokens is not None:
                config.max_tokens = max(1, min(128000, max_tokens))
            if top_p is not None:
                config.top_p = max(0.0, min(1.0, top_p))
            if tags is not None:
                config.tags = tags
            if notes is not None:
                config.notes = notes

            await self._save_to_disk()

        return asdict(config)

    async def delete_model_config(self, model_id: str) -> bool:
        """Delete model configuration"""
        await self.initialize()

        async with self._lock:
            if model_id in self._model_configs:
                del self._model_configs[model_id]
                await self._save_to_disk()
                return True
        return False

    async def get_model_temperature(self, model_id: str) -> float:
        """Get temperature for a model (with fallback to provider default)"""
        await self.initialize()

        if model_id in self._model_configs:
            return self._model_configs[model_id].temperature

        # Fallback to provider defaults
        if model_id.startswith("gemini/"):
            return self._settings.default_temperature_gemini
        elif model_id.startswith("anthropic/"):
            return self._settings.default_temperature_anthropic
        elif model_id.startswith("mistral/"):
            return self._settings.default_temperature_mistral
        elif model_id.startswith("ollama/"):
            return self._settings.default_temperature_ollama
        elif model_id.startswith("groq/"):
            return self._settings.default_temperature_groq
        elif model_id.startswith("cerebras/"):
            return self._settings.default_temperature_cerebras
        elif model_id.startswith("cohere/"):
            return self._settings.default_temperature_cohere
        elif model_id.startswith("openrouter/"):
            return self._settings.default_temperature_openrouter
        elif model_id.startswith("together/"):
            return self._settings.default_temperature_together
        elif model_id.startswith("fireworks/"):
            return self._settings.default_temperature_fireworks
        elif model_id.startswith("cloudflare/"):
            return self._settings.default_temperature_cloudflare
        else:
            return 0.7

    async def get_models_by_priority(self, min_priority: int = 0) -> List[Dict[str, Any]]:
        """Get models sorted by priority"""
        await self.initialize()

        models = [
            asdict(config) for config in self._model_configs.values()
            if config.enabled and config.priority >= min_priority
        ]
        return sorted(models, key=lambda x: x["priority"], reverse=True)

    # =========================================================================
    # Agent Settings
    # =========================================================================

    async def get_agent_configs(self) -> Dict[str, Dict[str, Any]]:
        """Get all agent configurations"""
        await self.initialize()
        return {k: asdict(v) for k, v in self._agent_configs.items()}

    async def get_agent_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get configuration for a specific agent"""
        await self.initialize()
        config = self._agent_configs.get(agent_id)
        return asdict(config) if config else None

    async def set_agent_config(
        self,
        agent_id: str,
        display_name: Optional[str] = None,
        default_model: Optional[str] = None,
        temperature: Optional[float] = None,
        system_prompt_override: Optional[str] = None,
        enabled: Optional[bool] = None,
        priority: Optional[int] = None,
        auto_start: Optional[bool] = None,
        timeout_seconds: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Set or update agent configuration"""
        await self.initialize()

        async with self._lock:
            if agent_id not in self._agent_configs:
                self._agent_configs[agent_id] = AgentConfig(
                    agent_id=agent_id,
                    display_name=display_name or agent_id
                )

            config = self._agent_configs[agent_id]

            if display_name is not None:
                config.display_name = display_name
            if default_model is not None:
                config.default_model = default_model
            if temperature is not None:
                config.temperature = max(0.0, min(2.0, temperature))
            if system_prompt_override is not None:
                config.system_prompt_override = system_prompt_override
            if enabled is not None:
                config.enabled = enabled
            if priority is not None:
                config.priority = max(0, min(100, priority))
            if auto_start is not None:
                config.auto_start = auto_start
            if timeout_seconds is not None:
                config.timeout_seconds = max(10, min(600, timeout_seconds))

            await self._save_to_disk()

        return asdict(config)

    async def get_agent_temperature(self, agent_id: str) -> float:
        """Get temperature for an agent"""
        await self.initialize()

        if agent_id in self._agent_configs:
            return self._agent_configs[agent_id].temperature
        return 0.7

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    async def import_settings(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Import settings from a dictionary (e.g., from file upload)"""
        await self.initialize()

        async with self._lock:
            self._load_from_dict(data)
            self._settings.last_modified = datetime.now().isoformat()
            self._settings.modified_by = "import"
            await self._save_to_disk()

        return self._to_dict()

    async def export_settings(self) -> Dict[str, Any]:
        """Export all settings"""
        await self.initialize()
        return self._to_dict()

    async def reset_to_defaults(self) -> Dict[str, Any]:
        """Reset all settings to defaults"""
        async with self._lock:
            self._create_defaults()
            self._settings.last_modified = datetime.now().isoformat()
            self._settings.modified_by = "reset"
            await self._save_to_disk()

        return self._to_dict()

    # =========================================================================
    # API Keys Management
    # =========================================================================

    async def get_api_keys(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """Get API keys configuration (masked by default)"""
        await self.initialize()
        return self._api_keys_to_dict(mask_secrets=mask_secrets)

    async def set_api_key(self, key_name: str, value: str, modified_by: str = "web-ui") -> Dict[str, Any]:
        """Set a single API key"""
        await self.initialize()

        if not hasattr(self._api_keys, key_name):
            raise ValueError(f"Unknown API key: {key_name}")

        async with self._lock:
            setattr(self._api_keys, key_name, value)
            self._save_api_keys_to_disk()

            # Also update .env file
            env_mapping = {
                "ollama_base_url": "OLLAMA_BASE",
                "ollama_bearer_token": "OLLAMA_BEARER_TOKEN",
                "gemini_api_key": "GEMINI_API_KEY",
                "anthropic_api_key": "ANTHROPIC_API_KEY",
                "mistral_api_key": "MISTRAL_API_KEY",
                "huggingface_api_key": "HUGGINGFACE_API_KEY",
                "gpt_oss_api_key": "GPT_OSS_API_KEY",
                "gpt_oss_base_url": "GPT_OSS_BASE_URL",
                # New providers (2025)
                "groq_api_key": "GROQ_API_KEY",
                "groq_base_url": "GROQ_BASE_URL",
                "groq_default_model": "GROQ_DEFAULT_MODEL",
                "cerebras_api_key": "CEREBRAS_API_KEY",
                "cerebras_base_url": "CEREBRAS_BASE_URL",
                "cerebras_default_model": "CEREBRAS_DEFAULT_MODEL",
                "cohere_api_key": "COHERE_API_KEY",
                "cohere_default_model": "COHERE_DEFAULT_MODEL",
                "cohere_embed_model": "COHERE_EMBED_MODEL",
                "openrouter_api_key": "OPENROUTER_API_KEY",
                "openrouter_base_url": "OPENROUTER_BASE_URL",
                "openrouter_default_model": "OPENROUTER_DEFAULT_MODEL",
                "together_api_key": "TOGETHER_API_KEY",
                "together_base_url": "TOGETHER_BASE_URL",
                "together_default_model": "TOGETHER_DEFAULT_MODEL",
                "fireworks_api_key": "FIREWORKS_API_KEY",
                "fireworks_base_url": "FIREWORKS_BASE_URL",
                "fireworks_default_model": "FIREWORKS_DEFAULT_MODEL",
                "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
                "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
                "cloudflare_default_model": "CLOUDFLARE_DEFAULT_MODEL",
                "jina_api_key": "JINA_API_KEY",
                "jina_embed_model": "JINA_EMBED_MODEL",
                # Existing services
                "wordpress_url": "WORDPRESS_URL",
                "wordpress_user": "WORDPRESS_USER",
                "wordpress_password": "WORDPRESS_PASSWORD",
                "stable_diffusion_url": "STABLE_DIFFUSION_URL",
                "stable_diffusion_username": "STABLE_DIFFUSION_USERNAME",
                "stable_diffusion_password": "STABLE_DIFFUSION_PASSWORD",
                "comfyui_url": "COMFYUI_URL",
                "tristar_gui_user": "TRISTAR_GUI_USER",
                "tristar_gui_password": "TRISTAR_GUI_PASSWORD",
                "redis_url": "REDIS_URL",
            }

            if key_name in env_mapping:
                await self._update_env_file({env_mapping[key_name]: value})
                os.environ[env_mapping[key_name]] = value

        return self._api_keys_to_dict(mask_secrets=True)

    async def set_api_keys(self, updates: Dict[str, str], modified_by: str = "web-ui") -> Dict[str, Any]:
        """Set multiple API keys at once"""
        await self.initialize()

        async with self._lock:
            env_updates = {}
            env_mapping = {
                "ollama_base_url": "OLLAMA_BASE",
                "ollama_bearer_token": "OLLAMA_BEARER_TOKEN",
                "gemini_api_key": "GEMINI_API_KEY",
                "anthropic_api_key": "ANTHROPIC_API_KEY",
                "mistral_api_key": "MISTRAL_API_KEY",
                "huggingface_api_key": "HUGGINGFACE_API_KEY",
                "gpt_oss_api_key": "GPT_OSS_API_KEY",
                "gpt_oss_base_url": "GPT_OSS_BASE_URL",
                # New providers (2025)
                "groq_api_key": "GROQ_API_KEY",
                "groq_base_url": "GROQ_BASE_URL",
                "groq_default_model": "GROQ_DEFAULT_MODEL",
                "cerebras_api_key": "CEREBRAS_API_KEY",
                "cerebras_base_url": "CEREBRAS_BASE_URL",
                "cerebras_default_model": "CEREBRAS_DEFAULT_MODEL",
                "cohere_api_key": "COHERE_API_KEY",
                "cohere_default_model": "COHERE_DEFAULT_MODEL",
                "cohere_embed_model": "COHERE_EMBED_MODEL",
                "openrouter_api_key": "OPENROUTER_API_KEY",
                "openrouter_base_url": "OPENROUTER_BASE_URL",
                "openrouter_default_model": "OPENROUTER_DEFAULT_MODEL",
                "together_api_key": "TOGETHER_API_KEY",
                "together_base_url": "TOGETHER_BASE_URL",
                "together_default_model": "TOGETHER_DEFAULT_MODEL",
                "fireworks_api_key": "FIREWORKS_API_KEY",
                "fireworks_base_url": "FIREWORKS_BASE_URL",
                "fireworks_default_model": "FIREWORKS_DEFAULT_MODEL",
                "cloudflare_account_id": "CLOUDFLARE_ACCOUNT_ID",
                "cloudflare_api_token": "CLOUDFLARE_API_TOKEN",
                "cloudflare_default_model": "CLOUDFLARE_DEFAULT_MODEL",
                "jina_api_key": "JINA_API_KEY",
                "jina_embed_model": "JINA_EMBED_MODEL",
                # Existing services
                "wordpress_url": "WORDPRESS_URL",
                "wordpress_user": "WORDPRESS_USER",
                "wordpress_password": "WORDPRESS_PASSWORD",
                "stable_diffusion_url": "STABLE_DIFFUSION_URL",
                "stable_diffusion_username": "STABLE_DIFFUSION_USERNAME",
                "stable_diffusion_password": "STABLE_DIFFUSION_PASSWORD",
                "comfyui_url": "COMFYUI_URL",
                "tristar_gui_user": "TRISTAR_GUI_USER",
                "tristar_gui_password": "TRISTAR_GUI_PASSWORD",
                "redis_url": "REDIS_URL",
            }

            for key_name, value in updates.items():
                if hasattr(self._api_keys, key_name):
                    setattr(self._api_keys, key_name, value)
                    if key_name in env_mapping:
                        env_updates[env_mapping[key_name]] = value

            self._save_api_keys_to_disk()

            if env_updates:
                await self._update_env_file(env_updates)
                for k, v in env_updates.items():
                    os.environ[k] = v

        return self._api_keys_to_dict(mask_secrets=True)

    async def verify_api_key(self, provider: str) -> Dict[str, Any]:
        """Verify if an API key is configured and valid format"""
        await self.initialize()

        key_mapping = {
            "gemini": "gemini_api_key",
            "anthropic": "anthropic_api_key",
            "mistral": "mistral_api_key",
            "huggingface": "huggingface_api_key",
            "gpt_oss": "gpt_oss_api_key",
            "ollama": "ollama_bearer_token",
            # New providers (2025)
            "groq": "groq_api_key",
            "cerebras": "cerebras_api_key",
            "cohere": "cohere_api_key",
            "openrouter": "openrouter_api_key",
            "together": "together_api_key",
            "fireworks": "fireworks_api_key",
            "cloudflare": "cloudflare_api_token",
            "jina": "jina_api_key",
        }

        if provider not in key_mapping:
            return {"provider": provider, "configured": False, "error": "Unknown provider"}

        key_name = key_mapping[provider]
        key_value = getattr(self._api_keys, key_name, "")

        return {
            "provider": provider,
            "configured": bool(key_value),
            "key_length": len(key_value) if key_value else 0,
        }

    # =========================================================================
    # Fallback Configuration
    # =========================================================================

    async def get_fallback_config(self) -> Dict[str, Any]:
        """Get fallback configuration"""
        await self.initialize()
        return asdict(self._fallback)

    async def update_fallback_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update fallback configuration"""
        await self.initialize()

        async with self._lock:
            for key, value in updates.items():
                if hasattr(self._fallback, key):
                    setattr(self._fallback, key, value)
            await self._save_to_disk()

        return asdict(self._fallback)

    # =========================================================================
    # Caching Configuration
    # =========================================================================

    async def get_caching_config(self) -> Dict[str, Any]:
        """Get caching configuration"""
        await self.initialize()
        return asdict(self._caching)

    async def update_caching_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """Update caching configuration"""
        await self.initialize()

        async with self._lock:
            for key, value in updates.items():
                if hasattr(self._caching, key):
                    setattr(self._caching, key, value)
            await self._save_to_disk()

        return asdict(self._caching)


# Singleton instance
settings_controller = SettingsController()
