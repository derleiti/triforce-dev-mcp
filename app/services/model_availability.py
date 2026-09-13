"""
AILinux Model Availability Service v1.0
=======================================
Prüft und trackt Model-Verfügbarkeit:
- Rate Limit Status (429)
- Quota Exhausted
- API Errors
- Provider Health

Unavailable Models werden NICHT an Client geliefert.
"""
import asyncio
import httpx
import os
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("ailinux.model_availability")


class AvailabilityStatus(str, Enum):
    AVAILABLE = "available"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    API_ERROR = "api_error"
    UNKNOWN = "unknown"


@dataclass
class ModelStatus:
    model_id: str
    status: AvailabilityStatus = AvailabilityStatus.UNKNOWN
    last_check: datetime = field(default_factory=datetime.now)
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    error_count: int = 0
    retry_after: Optional[datetime] = None
    
    def is_available(self) -> bool:
        if self.status == AvailabilityStatus.AVAILABLE:
            return True
        if self.retry_after and datetime.now() >= self.retry_after:
            return True
        return False
    
    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "status": self.status.value,
            "available": self.is_available(),
            "last_check": self.last_check.isoformat(),
            "last_error": self.last_error,
            "error_count": self.error_count,
            "retry_after": self.retry_after.isoformat() if self.retry_after else None
        }


# Bekannte Modelle mit Quota-Problemen
KNOWN_QUOTA_EXHAUSTED = {
    "gemini/gemini-2.0-flash",
    "gemini/gemini-1.5-flash",
    "gemini/gemini-pro",
    "openai/gpt-4o",  # OpenAI Quota erschöpft
    "openai/gpt-4",
}

# Definitiv funktionierende Modelle
KNOWN_WORKING = {
    "gemini/gemini-2.5-flash",
    "ollama/qwen2.5:14b",
    "groq/llama-3.3-70b-versatile",
    "cerebras/llama-3.3-70b",
}

# OpenRouter FREE Modelle (kostenlos, ohne Credits nutzbar)
OPENROUTER_FREE_MODELS = [
    "meta-llama/llama-3.1-405b-instruct:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "deepseek/deepseek-r1-0528:free",
    "google/gemini-2.0-flash-exp:free",
    "google/gemma-3-27b-it:free",
    "google/gemma-3-12b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "mistralai/devstral-2512:free",
    "qwen/qwen3-235b-a22b:free",
    "qwen/qwen-2.5-vl-7b-instruct:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "moonshotai/kimi-k2:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    "amazon/nova-2-lite-v1:free",
]

# Default Free-Modell für Fallback
DEFAULT_FREE_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"


class ModelAvailabilityService:
    @staticmethod
    def _gemini_key() -> str:
        from .google_genai import resolve_api_key
        return resolve_api_key() or ""

    def __init__(self):
        self._status: Dict[str, ModelStatus] = {}
        self._excluded_models: Set[str] = set(KNOWN_QUOTA_EXHAUSTED)
        self._check_interval = timedelta(minutes=15)
        self._rate_limit_cooldown = timedelta(minutes=5)
        self._quota_cooldown = timedelta(hours=24)
        
        self._api_keys = {
            "gemini": self._gemini_key(),
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
            "openai": os.getenv("OPENAI_API_KEY", ""),
            "groq": os.getenv("GROQ_API_KEY", ""),
            "cerebras": os.getenv("CEREBRAS_API_KEY", ""),
        }
        
        for model in KNOWN_QUOTA_EXHAUSTED:
            self._status[model] = ModelStatus(
                model_id=model,
                status=AvailabilityStatus.QUOTA_EXHAUSTED,
                last_error="Known quota exhausted",
                retry_after=datetime.now() + timedelta(days=7)
            )
        
        logger.info(f"ModelAvailability: {len(self._excluded_models)} models excluded")
    
    def is_available(self, model_id: str) -> bool:
        status = self._status.get(model_id)
        if model_id in self._excluded_models:
            # Dynamic exclusions must recover automatically when the provider's
            # retry window expires. Otherwise a temporary quota event becomes a
            # process-lifetime permanent ban until an administrator resets it.
            if status and status.retry_after and datetime.now() >= status.retry_after:
                self._excluded_models.discard(model_id)
                # Expiry means "eligible for a fresh probe", not proven healthy.
                # Removing the stale dynamic status restores the service's normal
                # unknown-model policy (assume available until the next result).
                self._status.pop(model_id, None)
                status = None
            else:
                return False
        if model_id in KNOWN_WORKING:
            return True
        if status:
            return status.is_available()
        return True  # Unknown = assume available
    
    def mark_error(self, model_id: str, error_code: int, error_msg: str = ""):
        if model_id not in self._status:
            self._status[model_id] = ModelStatus(model_id=model_id)
        status = self._status[model_id]
        status.last_check = datetime.now()
        status.last_error = f"{error_code}: {error_msg[:100]}"
        status.error_count += 1
        
        if error_code == 429:
            if "quota" in error_msg.lower() or "exhausted" in error_msg.lower():
                status.status = AvailabilityStatus.QUOTA_EXHAUSTED
                status.retry_after = datetime.now() + self._quota_cooldown
                self._excluded_models.add(model_id)
                logger.warning(f"Model {model_id} QUOTA EXHAUSTED")
            else:
                status.status = AvailabilityStatus.RATE_LIMITED
                status.retry_after = datetime.now() + self._rate_limit_cooldown
        elif error_code == 402:
            # Payment Required - Keine Credits
            status.status = AvailabilityStatus.QUOTA_EXHAUSTED
            status.retry_after = datetime.now() + self._quota_cooldown
            self._excluded_models.add(model_id)
            logger.warning(f"Model {model_id} PAYMENT REQUIRED (402) - excluded")
        elif error_code == 404:
            status.status = AvailabilityStatus.API_ERROR
            status.retry_after = datetime.now() + timedelta(days=30)
            self._excluded_models.add(model_id)
            logger.warning(f"Model {model_id} NOT FOUND")
        elif error_code in (401, 403):
            status.status = AvailabilityStatus.API_ERROR
            status.retry_after = datetime.now() + timedelta(hours=1)
    
    def mark_quota_exhausted(
        self,
        model_id: str,
        error_msg: str = "Quota exhausted",
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        """Mark quota exhaustion using the provider's reset window when known."""
        if model_id not in self._status:
            self._status[model_id] = ModelStatus(model_id=model_id)
        status = self._status[model_id]
        status.status = AvailabilityStatus.QUOTA_EXHAUSTED
        status.last_check = datetime.now()
        status.last_error = f"429: {str(error_msg)[:100]}"
        status.error_count += 1
        seconds = max(0, int(retry_after_seconds or 0))
        cooldown = timedelta(seconds=seconds) if seconds else self._quota_cooldown
        status.retry_after = datetime.now() + cooldown
        self._excluded_models.add(model_id)
        logger.warning(
            "Model %s QUOTA EXHAUSTED; retry in %ss",
            model_id,
            int(cooldown.total_seconds()),
        )

    def mark_success(self, model_id: str):
        if model_id not in self._status:
            self._status[model_id] = ModelStatus(model_id=model_id)
        status = self._status[model_id]
        status.status = AvailabilityStatus.AVAILABLE
        status.last_check = datetime.now()
        status.last_success = datetime.now()
        status.error_count = 0
        status.retry_after = None
        self._excluded_models.discard(model_id)
    
    def filter_available(self, model_ids: List[str]) -> List[str]:
        return [m for m in model_ids if self.is_available(m)]
    
    def get_excluded_models(self) -> Set[str]:
        return self._excluded_models.copy()
    
    def get_all_status(self) -> Dict[str, dict]:
        return {k: v.to_dict() for k, v in self._status.items()}
    
    async def check_provider_health(self, provider: str) -> dict:
        results = {
            "provider": provider,
            "api_key_set": bool(self._api_keys.get(provider)),
            "checked_at": datetime.now().isoformat(),
            "status": "unknown"
        }
        
        if provider == "gemini":
            key = self._api_keys.get("gemini")
            if key:
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(
                            "https://generativelanguage.googleapis.com/v1beta/models",
                            headers={"x-goog-api-key": key},
                        )
                        results["status"] = "healthy" if resp.status_code == 200 else f"error_{resp.status_code}"
                except Exception as e:
                    results["status"] = f"error: {str(e)[:50]}"
            else:
                results["status"] = "no_api_key"
        
        elif provider == "ollama":
            try:
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get("http://localhost:11434/api/tags")
                    results["status"] = "healthy" if resp.status_code == 200 else "offline"
            except:
                results["status"] = "offline"
        
        return results
    
    async def run_health_check(self) -> dict:
        providers = ["gemini", "anthropic", "ollama"]
        results = {}
        for p in providers:
            results[p] = await self.check_provider_health(p)
        return {
            "checked_at": datetime.now().isoformat(),
            "excluded_models": list(self._excluded_models),
            "excluded_count": len(self._excluded_models),
            "providers": results
        }
    
    def reset_model(self, model_id: str):
        if model_id in self._status:
            del self._status[model_id]
        self._excluded_models.discard(model_id)
    
    def add_exclusion(self, model_id: str, reason: str = "manual"):
        self._excluded_models.add(model_id)
        self._status[model_id] = ModelStatus(
            model_id=model_id,
            status=AvailabilityStatus.QUOTA_EXHAUSTED,
            last_error=f"Manual: {reason}",
            retry_after=datetime.now() + timedelta(days=365)
        )
    
    def get_free_models(self) -> List[str]:
        """Gibt OpenRouter Free-Modelle zurück (kostenlos nutzbar)"""
        return OPENROUTER_FREE_MODELS.copy()
    
    def get_default_free_model(self) -> str:
        """Default Free-Modell für Fallback"""
        return DEFAULT_FREE_MODEL


availability_service = ModelAvailabilityService()
