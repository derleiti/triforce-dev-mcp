from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from app.config import get_settings

logger = logging.getLogger("ailinux.community_federation_policy")


class CommunityPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class CommunityRegistration:
    capability: str
    gpu_name: str
    estimated_tflops: float
    supported_models: List[str]


class CommunityFederationPolicy:
    """Deterministic security boundary for untrusted community compute workers.

    Mistral may add an advisory anomaly review, but LLM output never grants access
    and never overrides these deterministic checks.
    """

    ALLOWED_CAPABILITIES = {
        "WEBGPU",
        "WEBGL2",
        "WASM_SIMD",
        "WASM",
        "JS_ONLY",
        "NATIVE_CPU",
        "NATIVE_GPU",
    }
    ALLOWED_TASK_TYPES = {
        "embedding",
        "embedding_batch",
        "sentiment",
        "sentiment_batch",
        "classification",
        "classification_batch",
        "summarization",
        "summarization_batch",
        "whisper_tiny",
        "whisper_small",
        "clip_embed",
        "clip_embed_batch",
        "image_classification",
        "image_classification_batch",
    }
    ALLOWED_WORKER_MESSAGES = {
        "heartbeat",
        "task_result",
        "task_progress",
        "capability_update",
        "disconnect",
    }
    _MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/+@-]{1,160}$")

    def settings_snapshot(self) -> Dict[str, Any]:
        settings = get_settings()
        return {
            "community_enabled": settings.federation_community_enabled,
            "auth_required": True,
            "allowed_tiers": self.allowed_tiers(),
            "max_message_bytes": settings.federation_community_max_message_bytes,
            "max_task_bytes": settings.federation_community_max_task_bytes,
            "max_models": settings.federation_community_max_models,
            "mistral_metadata_audit_enabled": settings.federation_community_mistral_audit_enabled,
            "mistral_raw_content_shared": False,
            "results_trusted_by_default": False,
            "allowed_task_types": sorted(self.ALLOWED_TASK_TYPES),
        }

    def allowed_tiers(self) -> List[str]:
        raw = get_settings().federation_community_allowed_tiers
        return [part.strip().lower() for part in raw.split(",") if part.strip()]

    def ensure_enabled(self) -> None:
        if not get_settings().federation_community_enabled:
            raise CommunityPolicyError("Community federation is disabled")

    def validate_tier(self, tier: str) -> str:
        normalized = (tier or "guest").strip().lower()
        if normalized not in self.allowed_tiers():
            raise CommunityPolicyError(f"Account tier not allowed for community compute: {normalized}")
        return normalized

    def validate_registration(
        self,
        *,
        capability: str,
        gpu_name: str,
        estimated_tflops: float,
        supported_models: List[str],
    ) -> CommunityRegistration:
        settings = get_settings()
        capability = (capability or "").strip().upper()
        if capability not in self.ALLOWED_CAPABILITIES:
            raise CommunityPolicyError(f"Unsupported worker capability: {capability}")

        gpu_name = (gpu_name or "").strip()[:120]
        try:
            estimated_tflops = float(estimated_tflops)
        except (TypeError, ValueError) as exc:
            raise CommunityPolicyError("Invalid TFLOPS value") from exc
        if estimated_tflops < 0 or estimated_tflops > 5000:
            raise CommunityPolicyError("TFLOPS value outside accepted range")

        if not isinstance(supported_models, list):
            raise CommunityPolicyError("supported_models must be a list")
        if len(supported_models) > settings.federation_community_max_models:
            raise CommunityPolicyError("Too many advertised models")

        normalized_models: List[str] = []
        seen = set()
        for model in supported_models:
            model_id = str(model).strip()
            if not self._MODEL_RE.fullmatch(model_id):
                raise CommunityPolicyError(f"Invalid model identifier: {model_id[:40]}")
            if model_id not in seen:
                seen.add(model_id)
                normalized_models.append(model_id)

        return CommunityRegistration(
            capability=capability,
            gpu_name=gpu_name,
            estimated_tflops=estimated_tflops,
            supported_models=normalized_models,
        )

    def validate_worker_message(self, message: Dict[str, Any]) -> str:
        if not isinstance(message, dict):
            raise CommunityPolicyError("Worker message must be an object")
        size = len(json.dumps(message, ensure_ascii=False, default=str).encode("utf-8"))
        if size > get_settings().federation_community_max_message_bytes:
            raise CommunityPolicyError("Worker message too large")
        msg_type = str(message.get("type") or "")
        if msg_type not in self.ALLOWED_WORKER_MESSAGES:
            raise CommunityPolicyError(f"Worker message type not allowed: {msg_type or '<empty>'}")
        return msg_type

    def validate_community_task(self, task_type: str, input_data: Any, model_id: str) -> None:
        task_type = (task_type or "").strip()
        if task_type not in self.ALLOWED_TASK_TYPES:
            raise CommunityPolicyError(f"Task type not allowed on community workers: {task_type}")
        model_id = (model_id or "").strip()
        if not self._MODEL_RE.fullmatch(model_id):
            raise CommunityPolicyError("Invalid model identifier")
        size = len(json.dumps(input_data, ensure_ascii=False, default=str).encode("utf-8"))
        if size > get_settings().federation_community_max_task_bytes:
            raise CommunityPolicyError("Community task payload too large")

    @staticmethod
    def _pseudonymous_user_id(user_id: str) -> str:
        return hashlib.sha256((user_id or "anonymous").encode("utf-8")).hexdigest()[:16]

    async def audit_registration_metadata(
        self,
        *,
        user_id: str,
        tier: str,
        registration: CommunityRegistration,
    ) -> Dict[str, Any]:
        """Optional Mistral anomaly review of sanitized registration metadata.

        Advisory only: failure/high risk never replaces deterministic authorization.
        No bearer token, IP, prompt, task input, task result, file content or email is sent.
        """
        settings = get_settings()
        if not settings.federation_community_mistral_audit_enabled:
            return {"enabled": False, "risk": "NOT_RUN"}

        metadata = {
            "worker": self._pseudonymous_user_id(user_id),
            "tier": tier,
            "capability": registration.capability,
            "gpu_name": registration.gpu_name,
            "estimated_tflops": registration.estimated_tflops,
            "supported_models": registration.supported_models,
        }
        try:
            from app.services.mistral_agent import mistral_agent_service

            result = await mistral_agent_service.start(
                "Review this sanitized community compute worker registration for implausible or anomalous metadata. "
                "Do not authorize access. Return one final line exactly as RISK: LOW, RISK: MEDIUM, or RISK: HIGH.\n\n"
                + json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                store=False,
                description="TriForce community federation metadata anomaly audit",
                instructions=(
                    "You are an advisory anomaly reviewer. You never grant permissions. "
                    "Use only the supplied sanitized metadata and do not infer identity."
                ),
            )
            text = str(result.get("response") or "")
            upper = text.upper()
            risk = "HIGH" if "RISK: HIGH" in upper else "MEDIUM" if "RISK: MEDIUM" in upper else "LOW" if "RISK: LOW" in upper else "UNKNOWN"
            return {
                "enabled": True,
                "risk": risk,
                "conversation_id": result.get("conversation_id"),
            }
        except Exception as exc:
            logger.warning("Mistral community metadata audit unavailable: %s", exc)
            return {"enabled": True, "risk": "UNAVAILABLE"}


community_federation_policy = CommunityFederationPolicy()
