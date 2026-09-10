# TriForce Backend Version
VERSION = "2.85 Beta 1"

from functools import lru_cache
from typing import Dict, List, Optional, Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AliasChoices, AnyHttpUrl, Field

from .settings_store import effective_environment

DEFAULT_ALLOWED_ORIGINS = [
    "http://localhost",
    "http://127.0.0.1",
    "http://localhost:5173",
    "http://localhost:3000",
    "https://ailinux.me",
    "https://www.ailinux.me",
    "https://api.ailinux.me",
    "https://login.ailinux.me",
    "https://update.ailinux.me",
    "https://api.ailinux.me:9100",
    "https://api.ailinux.me:9000",
    "https://www.ailinux.me:9100",
    "https://search.ailinux.me",
    "https://search.ailinux.me:9000",
]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="allow",
        env_file=None,
        populate_by_name=True,
    )

    # Canonical server bind settings. These are consumed by the service launcher
    # and shown by the Control Center; changing them requires a controlled restart.
    server_host: str = Field("127.0.0.1", validation_alias="TRIFORCE_BIND_HOST")
    server_port: int = Field(9100, ge=1, le=65535, validation_alias="TRIFORCE_API_PORT")
    server_keepalive: int = Field(75, ge=5, le=600, validation_alias="TRIFORCE_KEEPALIVE")
    deployment_mode: Literal["server", "node"] = Field("server", validation_alias="TRIFORCE_DEPLOYMENT_MODE")

    # Episodic history is optional and separate from curated TriForce memory.
    episodic_memory_enabled: bool = Field(False, validation_alias="TRIFORCE_EPISODIC_MEMORY_ENABLED")
    episodic_memory_provider: str = Field("claude-mem", validation_alias="TRIFORCE_EPISODIC_MEMORY_PROVIDER")
    episodic_memory_data_dir: Optional[str] = Field(None, validation_alias="TRIFORCE_EPISODIC_MEMORY_DATA_DIR")
    memory_auto_recall: bool = Field(True, validation_alias="TRIFORCE_MEMORY_AUTO_RECALL")
    memory_max_results: int = Field(4, ge=1, le=5, validation_alias="TRIFORCE_MEMORY_MAX_RESULTS")
    memory_token_budget: int = Field(1200, ge=128, le=8192, validation_alias="TRIFORCE_MEMORY_TOKEN_BUDGET")
    memory_timeout: float = Field(0.8, gt=0, le=5, validation_alias="TRIFORCE_MEMORY_TIMEOUT")
    memory_trigger_file: bool = Field(True, validation_alias="TRIFORCE_MEMORY_TRIGGER_FILE")
    memory_trigger_failure: bool = Field(True, validation_alias="TRIFORCE_MEMORY_TRIGGER_FAILURE")
    memory_trigger_retry: bool = Field(True, validation_alias="TRIFORCE_MEMORY_TRIGGER_RETRY")
    memory_record_enabled: bool = Field(False, validation_alias="TRIFORCE_MEMORY_RECORD_ENABLED")
    memory_promotion_enabled: bool = Field(False, validation_alias="TRIFORCE_MEMORY_PROMOTION_ENABLED")
    memory_project_id: Optional[str] = Field(None, validation_alias="TRIFORCE_MEMORY_PROJECT_ID")
    memory_stale_days: int = Field(90, ge=1, validation_alias="TRIFORCE_MEMORY_STALE_DAYS")

    # --- Core timeouts ---
    request_timeout: float = Field(default=30.0, validation_alias="REQUEST_TIMEOUT")
    ollama_timeout_ms: int = Field(default=120000, validation_alias="OLLAMA_TIMEOUT_MS")
    max_concurrent_requests: int = Field(default=8, validation_alias="MAX_CONCURRENT_REQUESTS")
    request_queue_timeout: float = Field(default=15.0, validation_alias="REQUEST_QUEUE_TIMEOUT")

    # --- CORS ---
    cors_allowed_origins: str = Field(default=",".join(DEFAULT_ALLOWED_ORIGINS), validation_alias="CORS_ALLOWED_ORIGINS")

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", validation_alias="REDIS_URL")

    # --- Docker Blueprint ---
    # These settings describe the shipped lightweight Compose blueprint only.
    # They never grant Docker socket access and never select an arbitrary compose file.
    docker_project_name: str = Field(default="triforce", validation_alias="DOCKER_PROJECT_NAME")
    docker_restart_policy: str = Field(default="unless-stopped", validation_alias="DOCKER_RESTART_POLICY")
    docker_log_tail: int = Field(default=100, ge=10, le=5000, validation_alias="DOCKER_LOG_TAIL")
    docker_network_name: str = Field(default="triforce-net", validation_alias="DOCKER_NETWORK_NAME")

    docker_redis_image: str = Field(default="redis:7-alpine", validation_alias="DOCKER_REDIS_IMAGE")
    docker_redis_container: str = Field(default="triforce-redis", validation_alias="DOCKER_REDIS_CONTAINER")
    docker_redis_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_REDIS_BIND")
    docker_redis_port: int = Field(default=6379, ge=1, le=65535, validation_alias="DOCKER_REDIS_PORT")

    docker_wordpress_image: str = Field(default="wordpress:latest", validation_alias="DOCKER_WORDPRESS_IMAGE")
    docker_wordpress_container: str = Field(default="triforce-wordpress", validation_alias="DOCKER_WORDPRESS_CONTAINER")
    docker_wordpress_db_image: str = Field(default="mariadb:11", validation_alias="DOCKER_WORDPRESS_DB_IMAGE")
    docker_wordpress_db_container: str = Field(default="triforce-wordpress-db", validation_alias="DOCKER_WORDPRESS_DB_CONTAINER")
    docker_wordpress_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_WORDPRESS_BIND")
    docker_wordpress_port: int = Field(default=8080, ge=1, le=65535, validation_alias="DOCKER_WORDPRESS_PORT")
    docker_wordpress_db_name: str = Field(default="wordpress", validation_alias="DOCKER_WORDPRESS_DB_NAME")
    docker_wordpress_db_user: str = Field(default="wordpress", validation_alias="DOCKER_WORDPRESS_DB_USER")
    docker_wordpress_db_password: str = Field(default="", validation_alias="DOCKER_WORDPRESS_DB_PASSWORD")
    docker_wordpress_db_root_password: str = Field(default="", validation_alias="DOCKER_WORDPRESS_DB_ROOT_PASSWORD")

    docker_flarum_image: str = Field(default="mondedie/flarum:latest", validation_alias="DOCKER_FLARUM_IMAGE")
    docker_flarum_container: str = Field(default="triforce-flarum", validation_alias="DOCKER_FLARUM_CONTAINER")
    docker_flarum_db_image: str = Field(default="mariadb:11", validation_alias="DOCKER_FLARUM_DB_IMAGE")
    docker_flarum_db_container: str = Field(default="triforce-flarum-db", validation_alias="DOCKER_FLARUM_DB_CONTAINER")
    docker_flarum_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_FLARUM_BIND")
    docker_flarum_port: int = Field(default=9080, ge=1, le=65535, validation_alias="DOCKER_FLARUM_PORT")
    docker_flarum_db_name: str = Field(default="flarum", validation_alias="DOCKER_FLARUM_DB_NAME")
    docker_flarum_db_user: str = Field(default="flarum", validation_alias="DOCKER_FLARUM_DB_USER")
    docker_flarum_db_password: str = Field(default="", validation_alias="DOCKER_FLARUM_DB_PASSWORD")
    docker_flarum_db_root_password: str = Field(default="", validation_alias="DOCKER_FLARUM_DB_ROOT_PASSWORD")
    docker_flarum_assets_path: str = Field(default="./flarum/assets", validation_alias="DOCKER_FLARUM_ASSETS_PATH")
    docker_flarum_extensions_path: str = Field(default="./flarum/extensions", validation_alias="DOCKER_FLARUM_EXTENSIONS_PATH")
    docker_flarum_storage_path: str = Field(default="./flarum/storage", validation_alias="DOCKER_FLARUM_STORAGE_PATH")

    docker_searxng_image: str = Field(default="searxng/searxng:latest", validation_alias="DOCKER_SEARXNG_IMAGE")
    docker_searxng_container: str = Field(default="triforce-searxng", validation_alias="DOCKER_SEARXNG_CONTAINER")
    docker_searxng_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_SEARXNG_BIND")
    docker_searxng_port: int = Field(default=8888, ge=1, le=65535, validation_alias="DOCKER_SEARXNG_PORT")
    docker_searxng_base_url: str = Field(default="http://localhost:8888/", validation_alias="DOCKER_SEARXNG_BASE_URL")
    docker_searxng_secret: str = Field(default="", validation_alias="DOCKER_SEARXNG_SECRET")
    docker_searxng_config_path: str = Field(default="./searxng", validation_alias="DOCKER_SEARXNG_CONFIG_PATH")

    docker_n8n_image: str = Field(default="n8nio/n8n:latest", validation_alias="DOCKER_N8N_IMAGE")
    docker_n8n_container: str = Field(default="triforce-n8n", validation_alias="DOCKER_N8N_CONTAINER")
    docker_n8n_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_N8N_BIND")
    docker_n8n_port: int = Field(default=5678, ge=1, le=65535, validation_alias="DOCKER_N8N_PORT")
    docker_n8n_host: str = Field(default="localhost", validation_alias="DOCKER_N8N_HOST")
    docker_n8n_protocol: str = Field(default="http", validation_alias="DOCKER_N8N_PROTOCOL")

    docker_repository_image: str = Field(default="nginx:alpine", validation_alias="DOCKER_REPOSITORY_IMAGE")
    docker_repository_container: str = Field(default="triforce-repository", validation_alias="DOCKER_REPOSITORY_CONTAINER")
    docker_repository_bind: str = Field(default="127.0.0.1", validation_alias="DOCKER_REPOSITORY_BIND")
    docker_repository_port: int = Field(default=8081, ge=1, le=65535, validation_alias="DOCKER_REPOSITORY_PORT")
    docker_repository_data_path: str = Field(default="./repository/repo", validation_alias="DOCKER_REPOSITORY_DATA_PATH")

    docker_mailserver_image: str = Field(default="mailserver/docker-mailserver:latest", validation_alias="DOCKER_MAILSERVER_IMAGE")
    docker_mailserver_container: str = Field(default="triforce-mailserver", validation_alias="DOCKER_MAILSERVER_CONTAINER")
    docker_mailserver_hostname: str = Field(default="mail", validation_alias="DOCKER_MAILSERVER_HOSTNAME")
    docker_mail_smtp_bind: str = Field(default="0.0.0.0", validation_alias="DOCKER_MAIL_SMTP_BIND")
    docker_mail_smtp_port: int = Field(default=25, ge=1, le=65535, validation_alias="DOCKER_MAIL_SMTP_PORT")
    docker_mail_submission_bind: str = Field(default="0.0.0.0", validation_alias="DOCKER_MAIL_SUBMISSION_BIND")
    docker_mail_submission_port: int = Field(default=587, ge=1, le=65535, validation_alias="DOCKER_MAIL_SUBMISSION_PORT")
    docker_mail_imaps_bind: str = Field(default="0.0.0.0", validation_alias="DOCKER_MAIL_IMAPS_BIND")
    docker_mail_imaps_port: int = Field(default=993, ge=1, le=65535, validation_alias="DOCKER_MAIL_IMAPS_PORT")
    docker_mailserver_config_path: str = Field(default="./mailserver", validation_alias="DOCKER_MAILSERVER_CONFIG_PATH")

    # --- Providers / Backends ---
    ollama_base: AnyHttpUrl = Field(default="http://localhost:11434", validation_alias="OLLAMA_BASE")
    ollama_bearer_token: Optional[str] = Field(default=None, validation_alias="OLLAMA_BEARER_TOKEN")
    ollama_bearer_auth_enabled: bool = Field(default=True, validation_alias="OLLAMA_BEARER_AUTH_ENABLED")
    ollama_fallback_model: str = Field(default="gpt-oss:20b-cloud", validation_alias="OLLAMA_FALLBACK_MODEL")
    stable_diffusion_url: AnyHttpUrl = Field(default="http://localhost:7860", validation_alias="STABLE_DIFFUSION_URL")
    comfyui_url: Optional[AnyHttpUrl] = Field(default=None, validation_alias="COMFYUI_URL")
    stable_diffusion_backend: str = Field(default="automatic1111", validation_alias="STABLE_DIFFUSION_BACKEND")
    stable_diffusion_poll_interval: float = Field(default=1.0, validation_alias="STABLE_DIFFUSION_POLL_INTERVAL")
    stable_diffusion_max_wait: float = Field(default=120.0, validation_alias="STABLE_DIFFUSION_MAX_WAIT")
    stable_diffusion_default_models: str = Field(default="sd_xl_base_1.0.safetensors,v1-5-pruned-emaonly.safetensors", validation_alias="STABLE_DIFFUSION_DEFAULT_MODELS")
    stable_diffusion_username: Optional[str] = Field(default=None, validation_alias="STABLE_DIFFUSION_USERNAME")
    stable_diffusion_password: Optional[str] = Field(default=None, validation_alias="STABLE_DIFFUSION_PASSWORD")
    stable_diffusion_api_key: Optional[str] = Field(default=None, validation_alias="STABLE_DIFFUSION_API_KEY")

    # TriStar GUI Authentication
    tristar_gui_user: str = Field(default="zombie", validation_alias="TRISTAR_GUI_USER")
    tristar_gui_password: str = Field(default="", validation_alias="TRISTAR_GUI_PASSWORD")

    # MCP Authentication (User/Password only - no API keys)
    mcp_oauth_user: Optional[str] = Field(default=None, validation_alias="MCP_OAUTH_USER")
    mcp_oauth_pass: Optional[str] = Field(default=None, validation_alias="MCP_OAUTH_PASS")
    mcp_allow_unauthenticated_local: bool = Field(default=False, validation_alias="MCP_ALLOW_UNAUTHENTICATED_LOCAL")
    mcp_dev_allowed_roots: Optional[str] = Field(
        default=None,
        validation_alias="MCP_DEV_ALLOWED_ROOTS",
    )

    # MCP Mesh WebSocket
    mcp_ws_enabled: bool = Field(default=True, validation_alias="MCP_WS_ENABLED")
    mcp_ws_host: str = Field(default="0.0.0.0", validation_alias="MCP_WS_HOST")
    mcp_ws_port: int = Field(default=44433, validation_alias="MCP_WS_PORT")
    mcp_ws_enable_ipv6: bool = Field(default=False, validation_alias="MCP_WS_ENABLE_IPV6")

    # Community federation / distributed compute. Community workers are always
    # treated as untrusted and require an authenticated AILinux account.
    federation_community_enabled: bool = Field(default=False, validation_alias="FEDERATION_COMMUNITY_ENABLED")
    federation_community_allowed_tiers: str = Field(default="registered,pro,enterprise", validation_alias="FEDERATION_COMMUNITY_ALLOWED_TIERS")
    federation_community_max_message_bytes: int = Field(default=262144, ge=4096, le=4194304, validation_alias="FEDERATION_COMMUNITY_MAX_MESSAGE_BYTES")
    federation_community_max_task_bytes: int = Field(default=524288, ge=4096, le=8388608, validation_alias="FEDERATION_COMMUNITY_MAX_TASK_BYTES")
    federation_community_max_models: int = Field(default=32, ge=1, le=256, validation_alias="FEDERATION_COMMUNITY_MAX_MODELS")
    federation_community_mistral_audit_enabled: bool = Field(default=False, validation_alias="FEDERATION_COMMUNITY_MISTRAL_AUDIT_ENABLED")

    # GPT-OSS
    gpt_oss_api_key: str | None = Field(default=None, validation_alias="GPT_OSS_API_KEY")
    gpt_oss_base_url: AnyHttpUrl | None = Field(default=None, validation_alias="GPT_OSS_BASE_URL")

    # Gemini
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "GOOGLE_AI_STUDIO_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_GEMINI_KEY",
        ),
    )
    google_ai_studio_key: str | None = Field(default=None, validation_alias="GOOGLE_AI_STUDIO_KEY")
    google_url: Optional[str] = Field(default=None, validation_alias="GOOGLE_URL")
    google_user: Optional[str] = Field(default=None, validation_alias="GOOGLE_USER")
    google_pass: Optional[str] = Field(default=None, validation_alias="GOOGLE_PASS")
    gemini_agent_id: Optional[str] = Field(default=None, validation_alias="GEMINI_AGENT_ID")
    claude_url: Optional[str] = Field(default=None, validation_alias="CLAUDE_URL")
    claude_user: Optional[str] = Field(default=None, validation_alias="CLAUDE_USER")
    claude_pass: Optional[str] = Field(default=None, validation_alias="CLAUDE_PASS")


    # OpenAI
    openai_api_key: str | None = Field(default=None, validation_alias="OPENAI_API_KEY")

    # Mistral
    mistral_api_key: str | None = Field(default=None, validation_alias="MISTRAL_API_KEY")
    mistral_organisation_id: str | None = Field(default=None, validation_alias="MISTRAL_ORG_ID")
    codestral_api_key: str | None = Field(default=None, validation_alias="CODESTRAL_API_KEY")
    # Native Mistral Agents / Conversations API
    mistral_agent_id: Optional[str] = Field(
        default="ag:75b2b27f:20251006:untitled-agent:8f02c404",
        validation_alias=AliasChoices("MISTRAL_AGENT_ID", "NOVA_MISTRAL_AGENT_ID"),
    )
    mistral_agent_version: Optional[int] = Field(default=5, validation_alias="MISTRAL_AGENT_VERSION")
    mistral_agent_base_url: str = Field(default="https://api.mistral.ai", validation_alias="MISTRAL_AGENT_BASE_URL")
    mistral_agent_timeout_seconds: int = Field(default=180, validation_alias="MISTRAL_AGENT_TIMEOUT_SECONDS")
    mistral_agent_store: bool = Field(default=True, validation_alias="MISTRAL_AGENT_STORE")

    # Anthropic Claude
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_timeout_ms: int = Field(default=120000, validation_alias="ANTHROPIC_TIMEOUT_MS")
    anthropic_max_tokens: int = Field(default=8192, validation_alias="ANTHROPIC_MAX_TOKENS")

    # Hugging Face Inference API (v2.80)
    huggingface_api_key: str | None = Field(
        default=None,
        validation_alias="HUGGINGFACE_API_KEY"
    )
    huggingface_inference_url: str = Field(
        default="https://router.huggingface.co/hf-inference",
        validation_alias="HUGGINGFACE_INFERENCE_URL"
    )
    huggingface_timeout: int = Field(
        default=120,
        validation_alias="HUGGINGFACE_TIMEOUT"
    )

    # =========================================================================
    # NEW FREE TIER PROVIDERS (2025)
    # =========================================================================

    # Groq (Fastest Inference - 300+ tokens/sec)
    groq_api_key: str | None = Field(default=None, validation_alias="GROQ_API_KEY")
    groq_base_url: str = Field(default="https://api.groq.com/openai/v1", validation_alias="GROQ_BASE_URL")
    groq_default_model: str = Field(default="llama-3.3-70b-versatile", validation_alias="GROQ_DEFAULT_MODEL")
    groq_timeout_ms: int = Field(default=30000, validation_alias="GROQ_TIMEOUT_MS")

    # Cerebras (1M tokens/day FREE - 20x faster than GPU)
    cerebras_api_key: str | None = Field(default=None, validation_alias="CEREBRAS_API_KEY")
    cerebras_base_url: str = Field(default="https://api.cerebras.ai/v1", validation_alias="CEREBRAS_BASE_URL")
    cerebras_default_model: str = Field(default="llama3.1-70b", validation_alias="CEREBRAS_DEFAULT_MODEL")
    cerebras_timeout_ms: int = Field(default=30000, validation_alias="CEREBRAS_TIMEOUT_MS")

    # NVIDIA NIM hosted API (OpenAI-compatible; models discovered from /v1/models)
    nvidia_api_key: str | None = Field(default=None, validation_alias="NVIDIA_API_KEY")
    nvidia_base_url: str = Field(default="https://integrate.api.nvidia.com/v1", validation_alias="NVIDIA_BASE_URL")
    nvidia_timeout_ms: int = Field(default=120000, validation_alias="NVIDIA_TIMEOUT_MS")

    # Cohere (Best RAG & Embeddings)
    cohere_api_key: str | None = Field(default=None, validation_alias="COHERE_API_KEY")
    cohere_default_model: str = Field(default="command-r-plus", validation_alias="COHERE_DEFAULT_MODEL")
    cohere_embed_model: str = Field(default="embed-multilingual-v3.0", validation_alias="COHERE_EMBED_MODEL")
    cohere_timeout_ms: int = Field(default=60000, validation_alias="COHERE_TIMEOUT_MS")

    # OpenRouter (300+ models, one API key)
    openrouter_api_key: str | None = Field(default=None, validation_alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL")
    openrouter_default_model: str = Field(default="nvidia/nemotron-3-ultra-550b-a55b:free", validation_alias="OPENROUTER_DEFAULT_MODEL")
    openrouter_timeout_ms: int = Field(default=120000, validation_alias="OPENROUTER_TIMEOUT_MS")

    # Together AI ($25 free credits)
    together_api_key: str | None = Field(default=None, validation_alias="TOGETHER_API_KEY")
    together_base_url: str = Field(default="https://api.together.xyz/v1", validation_alias="TOGETHER_BASE_URL")
    together_default_model: str = Field(default="meta-llama/Llama-3.3-70B-Instruct-Turbo", validation_alias="TOGETHER_DEFAULT_MODEL")
    together_timeout_ms: int = Field(default=120000, validation_alias="TOGETHER_TIMEOUT_MS")

    # Fireworks AI ($1 free credits)
    fireworks_api_key: str | None = Field(default=None, validation_alias="FIREWORKS_API_KEY")
    fireworks_base_url: str = Field(default="https://api.fireworks.ai/inference/v1", validation_alias="FIREWORKS_BASE_URL")
    fireworks_default_model: str = Field(default="accounts/fireworks/models/llama-v3p3-70b-instruct", validation_alias="FIREWORKS_DEFAULT_MODEL")
    fireworks_timeout_ms: int = Field(default=60000, validation_alias="FIREWORKS_TIMEOUT_MS")

    # Cloudflare Workers AI (10,000 neurons/day free)
    cloudflare_account_id: str | None = Field(default=None, validation_alias="CLOUDFLARE_ACCOUNT_ID")
    cloudflare_api_token: str | None = Field(default=None, validation_alias="CLOUDFLARE_API_TOKEN")
    cloudflare_zone_id: str | None = Field(default=None, validation_alias="CLOUDFLARE_ZONE_ID")
    cloudflare_zone_api_token: str | None = Field(default=None, validation_alias="CLOUDFLARE_ZONE_API_TOKEN")
    cloudflare_default_model: str = Field(default="@cf/meta/llama-3.3-70b-instruct-fp8-fast", validation_alias="CLOUDFLARE_DEFAULT_MODEL")

    # GitHub Models (Free with PAT - GPT-4o, Llama, DeepSeek, etc.)
    github_token: str | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    github_models_base_url: str = Field(default="https://models.github.ai/inference", validation_alias="GITHUB_MODELS_BASE_URL")
    github_models_timeout_ms: int = Field(default=60000, validation_alias="GITHUB_MODELS_TIMEOUT_MS")

    # Jina AI (Free Embeddings)
    jina_api_key: str | None = Field(default=None, validation_alias="JINA_API_KEY")
    jina_embed_model: str = Field(default="jina-embeddings-v3", validation_alias="JINA_EMBED_MODEL")

    # OpenAI compatibility
    openai_model_aliases: Dict[str, str] = Field(default_factory=dict, validation_alias="OPENAI_MODEL_ALIASES")
    chatgpt_url: Optional[str] = Field(default=None, validation_alias="CHATGPT_URL")
    chatgpt_user: Optional[str] = Field(default=None, validation_alias="CHATGPT_USER")
    chatgpt_pass: Optional[str] = Field(default=None, validation_alias="CHATGPT_PASS")
    nova_chatgpt_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CHATGPT_URL", "CHATGPT_URL"))
    nova_chatgpt_user: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CHATGPT_USER", "CHATGPT_USER"))
    nova_chatgpt_pass: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CHATGPT_PASS", "CHATGPT_PASS"))
    nova_chatgpt_agent_id: Optional[str] = Field(default=None, validation_alias="NOVA_CHATGPT_AGENT_ID")
    nova_google_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_GOOGLE_URL", "GOOGLE_URL"))
    nova_google_user: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_GOOGLE_USER", "GOOGLE_USER"))
    nova_google_pass: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_GOOGLE_PASS", "GOOGLE_PASS"))
    nova_gemini_agent_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_GEMINI_AGENT_ID", "GEMINI_AGENT_ID"))
    nova_claude_url: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CLAUDE_URL", "CLAUDE_URL"))
    nova_claude_user: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CLAUDE_USER", "CLAUDE_USER"))
    nova_claude_pass: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CLAUDE_PASS", "CLAUDE_PASS"))
    nova_claude_agent_id: Optional[str] = Field(default=None, validation_alias=AliasChoices("NOVA_CLAUDE_AGENT_ID", "CLAUDE_AGENT_ID"))
    claude_agent_id: Optional[str] = Field(default=None, validation_alias="CLAUDE_AGENT_ID")
    nova_mistral_url: Optional[str] = Field(default=None, validation_alias="NOVA_MISTRAL_URL")
    nova_mistral_user: Optional[str] = Field(default=None, validation_alias="NOVA_MISTRAL_USER")
    nova_mistral_pass: Optional[str] = Field(default=None, validation_alias="NOVA_MISTRAL_PASS")
    nova_mistral_agent_id: Optional[str] = Field(default=None, validation_alias="NOVA_MISTRAL_AGENT_ID")

    # WordPress / bbPress
    wordpress_url: AnyHttpUrl | None = Field(default=None, validation_alias="WORDPRESS_URL")
    wordpress_user: str | None = Field(default=None, validation_alias="WORDPRESS_USER")
    wordpress_password: str | None = Field(default=None, validation_alias="WORDPRESS_PASSWORD")

    # Crawler - User Instance (fast, for /crawl prompts)
    crawler_enabled: bool = Field(default=True, validation_alias="CRAWLER_ENABLED")
    crawler_max_memory_bytes: int = Field(default=256*1024*1024, validation_alias="CRAWLER_MAX_MEMORY_BYTES")
    crawler_spool_dir: str = Field(default="data/crawler_spool", validation_alias="CRAWLER_SPOOL_DIR")
    crawler_train_dir: str = Field(default="data/crawler_spool/train", validation_alias="CRAWLER_TRAIN_DIR")
    crawler_flush_interval: int = Field(default=3600, validation_alias="CRAWLER_FLUSH_INTERVAL")
    crawler_retention_days: int = Field(default=30, validation_alias="CRAWLER_RETENTION_DAYS")
    crawler_summary_model: str | None = Field(default=None, validation_alias="CRAWLER_SUMMARY_MODEL")
    crawler_ollama_model: str | None = Field(default=None, validation_alias="CRAWLER_OLLAMA_MODEL")

    # User Crawler Settings (fast, dedicated for user prompts)
    user_crawler_workers: int = Field(default=4, validation_alias="USER_CRAWLER_WORKERS")
    user_crawler_max_concurrent: int = Field(default=8, validation_alias="USER_CRAWLER_MAX_CONCURRENT")

    # Auto Crawler Settings (background, slower)
    auto_crawler_workers: int = Field(default=2, validation_alias="AUTO_CRAWLER_WORKERS")
    auto_crawler_enabled: bool = Field(default=True, validation_alias="AUTO_CRAWLER_ENABLED")

    # WordPress Publishing
    wordpress_category_id: int = Field(default=1, validation_alias="WORDPRESS_CATEGORY_ID")

    # Mail / Notification (optional)
    mail_from_name: Optional[str] = Field(default=None, validation_alias="MAIL_FROM_NAME")
    mail_from_addr: Optional[str] = Field(default=None, validation_alias="MAIL_FROM_ADDR")
    mail_smtp_host: Optional[str] = Field(default=None, validation_alias="MAIL_SMTP_HOST")
    mail_smtp_port: Optional[int] = Field(default=None, validation_alias="MAIL_SMTP_PORT")
    mail_smtp_user: Optional[str] = Field(default=None, validation_alias="MAIL_SMTP_USER")
    mail_smtp_pass: Optional[str] = Field(default=None, validation_alias="MAIL_SMTP_PASS")
    mail_smtp_starttls: Optional[bool] = Field(default=None, validation_alias="MAIL_SMTP_STARTTLS")
    mail_imap_host: Optional[str] = Field(default=None, validation_alias="MAIL_IMAP_HOST")
    mail_imap_port: Optional[int] = Field(default=None, validation_alias="MAIL_IMAP_PORT")
    mail_imap_user: Optional[str] = Field(default=None, validation_alias="MAIL_IMAP_USER")
    mail_imap_pass: Optional[str] = Field(default=None, validation_alias="MAIL_IMAP_PASS")
    mail_imap_ssl: Optional[bool] = Field(default=None, validation_alias="MAIL_IMAP_SSL")
    mail_recipient_allowlist: Optional[str] = Field(default=None, validation_alias="MAIL_RECIPIENT_ALLOWLIST")
    mail_rate_per_min: Optional[int] = Field(default=None, validation_alias="MAIL_RATE_PER_MIN")

@lru_cache
def get_settings() -> Settings:
    # One deterministic loading path: process environment > canonical config file
    # > schema defaults. The file parser treats dotenv content as data, never shell.
    values, _origins = effective_environment()
    return Settings.model_validate(values, by_alias=True, by_name=True)
