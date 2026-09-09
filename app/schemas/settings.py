from __future__ import annotations

from typing import Dict, Optional
from pydantic import BaseModel, Field, AnyHttpUrl


class SettingsResponse(BaseModel):
    """Response model for settings"""
    # Core Settings
    request_timeout: float = Field(..., description="Default request timeout in seconds")
    ollama_timeout_ms: int = Field(..., description="Ollama backend timeout in milliseconds")
    max_concurrent_requests: int = Field(..., description="Maximum concurrent requests")
    request_queue_timeout: float = Field(..., description="Request queue timeout in seconds")

    # Backends
    ollama_base: str = Field(..., description="Ollama API base URL")
    ollama_bearer_auth_enabled: bool = Field(..., description="Whether Ollama bearer auth is enabled")
    gemini_api_key_configured: bool = Field(..., description="Whether Gemini API key is configured")
    mistral_api_key_configured: bool = Field(..., description="Whether Mistral API key is configured")
    gpt_oss_configured: bool = Field(..., description="Whether GPT-OSS is configured")
    stable_diffusion_url: str = Field(..., description="Stable Diffusion API URL")
    comfyui_url: Optional[str] = Field(None, description="ComfyUI API URL")
    stable_diffusion_backend: str = Field(..., description="Stable Diffusion backend type")
    stable_diffusion_default_models: str = Field(..., description="Default models for Stable Diffusion")

    # Crawler Configuration
    crawler_enabled: bool = Field(..., description="Whether crawler is enabled")
    crawler_max_memory_bytes: int = Field(..., description="Crawler max memory in bytes")
    crawler_flush_interval: int = Field(..., description="Crawler flush interval in seconds")
    crawler_retention_days: int = Field(..., description="Crawler retention period in days")
    crawler_summary_model: Optional[str] = Field(None, description="Model for summarization")

    # User Crawler Settings
    user_crawler_workers: int = Field(..., description="User crawler workers")
    user_crawler_max_concurrent: int = Field(..., description="User crawler max concurrent pages")

    # Auto Crawler Settings
    auto_crawler_workers: int = Field(..., description="Auto crawler workers")
    auto_crawler_enabled: bool = Field(..., description="Whether auto crawler is enabled")

    # WordPress Integration
    wordpress_configured: bool = Field(..., description="Whether WordPress is configured")
    wordpress_url: Optional[str] = Field(None, description="WordPress site URL")
    wordpress_category_id: int = Field(..., description="Default WordPress category ID")

    # OpenAI Compatibility
    openai_model_aliases: Dict[str, str] = Field(default_factory=dict, description="Model aliases")

    # CORS
    cors_allowed_origins: str = Field(..., description="Allowed CORS origins")


class SettingsUpdate(BaseModel):
    """Update model for settings (only updatable fields)."""
    expected_digest: Optional[str] = Field(None, min_length=64, max_length=64, description="Configuration digest used for optimistic concurrency")
    # Core Settings
    request_timeout: Optional[float] = Field(None, ge=1.0, le=300.0)
    ollama_timeout_ms: Optional[int] = Field(None, ge=1000, le=300000)
    max_concurrent_requests: Optional[int] = Field(None, ge=1, le=100)
    request_queue_timeout: Optional[float] = Field(None, ge=1.0, le=120.0)

    # Backend URLs
    ollama_base: Optional[AnyHttpUrl] = None
    stable_diffusion_url: Optional[AnyHttpUrl] = None
    comfyui_url: Optional[AnyHttpUrl] = None
    wordpress_url: Optional[AnyHttpUrl] = None

    # Backend Settings
    ollama_bearer_auth_enabled: Optional[bool] = None
    stable_diffusion_backend: Optional[str] = Field(None, pattern="^(automatic1111|comfyui)$")
    stable_diffusion_default_models: Optional[str] = None

    # Crawler Configuration
    crawler_enabled: Optional[bool] = None
    crawler_max_memory_bytes: Optional[int] = Field(None, ge=1024*1024, le=2*1024*1024*1024)
    crawler_flush_interval: Optional[int] = Field(None, ge=60, le=86400)
    crawler_retention_days: Optional[int] = Field(None, ge=1, le=365)
    crawler_summary_model: Optional[str] = None

    # User Crawler Settings
    user_crawler_workers: Optional[int] = Field(None, ge=1, le=20)
    user_crawler_max_concurrent: Optional[int] = Field(None, ge=1, le=50)

    # Auto Crawler Settings
    auto_crawler_workers: Optional[int] = Field(None, ge=1, le=10)
    auto_crawler_enabled: Optional[bool] = None

    # WordPress Settings
    wordpress_category_id: Optional[int] = Field(None, ge=0)

    # OpenAI Compatibility
    openai_model_aliases: Optional[Dict[str, str]] = None

    # CORS
    cors_allowed_origins: Optional[str] = None
