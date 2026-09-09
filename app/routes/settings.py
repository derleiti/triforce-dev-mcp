from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from ..utils.rate_limit_compat import RateLimiter

from ..config import Settings, get_settings
from ..routes.client_auth import require_admin
from ..settings_store import ConfigConflict, ConfigError, load_snapshot, save_updates
from ..schemas.settings import SettingsResponse, SettingsUpdate

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsResponse)
async def get_settings_endpoint():
    """Get current system settings (read-only view)"""
    settings = get_settings()

    return SettingsResponse(
        # Core Settings
        request_timeout=settings.request_timeout,
        ollama_timeout_ms=settings.ollama_timeout_ms,
        max_concurrent_requests=settings.max_concurrent_requests,
        request_queue_timeout=settings.request_queue_timeout,
        # Backends
        ollama_base=str(settings.ollama_base),
        ollama_bearer_auth_enabled=settings.ollama_bearer_auth_enabled,
        gemini_api_key_configured=bool(settings.gemini_api_key),
        mistral_api_key_configured=bool(settings.mistral_api_key),
        gpt_oss_configured=bool(settings.gpt_oss_api_key and settings.gpt_oss_base_url),
        stable_diffusion_url=str(settings.stable_diffusion_url),
        comfyui_url=str(settings.comfyui_url) if settings.comfyui_url else None,
        stable_diffusion_backend=settings.stable_diffusion_backend,
        stable_diffusion_default_models=settings.stable_diffusion_default_models,
        # Crawler Configuration
        crawler_enabled=settings.crawler_enabled,
        crawler_max_memory_bytes=settings.crawler_max_memory_bytes,
        crawler_flush_interval=settings.crawler_flush_interval,
        crawler_retention_days=settings.crawler_retention_days,
        crawler_summary_model=settings.crawler_summary_model,
        # User Crawler Settings
        user_crawler_workers=settings.user_crawler_workers,
        user_crawler_max_concurrent=settings.user_crawler_max_concurrent,
        # Auto Crawler Settings
        auto_crawler_workers=settings.auto_crawler_workers,
        auto_crawler_enabled=settings.auto_crawler_enabled,
        # WordPress Integration
        wordpress_configured=bool(settings.wordpress_url and settings.wordpress_user),
        wordpress_url=str(settings.wordpress_url) if settings.wordpress_url else None,
        wordpress_category_id=settings.wordpress_category_id,
        # OpenAI Compatibility
        openai_model_aliases=settings.openai_model_aliases or {},
        # CORS
        cors_allowed_origins=settings.cors_allowed_origins,
    )


def _update_to_env(updates: SettingsUpdate) -> dict[str, object]:
    """Translate the public update schema to canonical environment aliases."""
    values = updates.model_dump(exclude_none=True, exclude={"expected_digest"})
    result: dict[str, object] = {}
    for field_name, value in values.items():
        field = Settings.model_fields[field_name]
        alias = field.validation_alias
        choices = getattr(alias, "choices", None)
        if choices:
            env_name = next((str(choice) for choice in choices if isinstance(choice, str)), field_name)
        elif alias is not None:
            env_name = str(alias)
        else:
            env_name = field_name
        result[env_name] = value
    return result


@router.put(
    "",
    response_model=SettingsResponse,
    dependencies=[Depends(RateLimiter(times=10, seconds=60))],
)
async def update_settings_endpoint(
    updates: SettingsUpdate,
    _admin: dict = Depends(require_admin),
):
    """Persist supported settings through the canonical configuration store.

    The backend never escalates privileges. On packaged installations
    ``/etc/triforce/triforce.env`` is root-owned, so remote API writes are
    rejected with HTTP 403 and must be performed through the local Control
    Center/PolicyKit helper. Successful writes are intentionally not injected
    into ``os.environ``; server settings become active after a controlled
    restart.
    """
    env_updates = _update_to_env(updates)
    if not env_updates:
        return await get_settings_endpoint()

    try:
        snapshot = load_snapshot()
        expected = updates.expected_digest or snapshot.digest
        save_updates(env_updates, expected_digest=expected, environ={})
    except ConfigConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                "Systemkonfiguration ist geschützt. Verwende das lokale "
                "TriForce Control Center mit administrativer Autorisierung."
            ),
        ) from exc
    except (ConfigError, OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    get_settings.cache_clear()
    return await get_settings_endpoint()
