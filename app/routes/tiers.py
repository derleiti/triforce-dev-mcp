"""
AILinux Tier API Routes
Pricing, Tier-Info, User-Upgrade
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta

from ..services.user_tiers import (
    tier_service, UserTier, TIER_CONFIGS,
    FREE_MODELS, ALL_OPENROUTER_MODELS
)

from .client_auth import require_admin

router = APIRouter(prefix="/tiers", tags=["Tiers & Pricing"])


class TierResponse(BaseModel):
    tier: str
    name: str
    price_monthly: float
    price_yearly: float
    features: List[str]
    model_count: int
    priority_queue: bool
    support_level: str


class UserTierRequest(BaseModel):
    user_id: str
    tier: str
    duration_months: Optional[int] = 1


class ModelListResponse(BaseModel):
    tier: str
    model_count: int
    models: List[str]


@router.get("/pricing", response_model=List[TierResponse])
async def get_pricing():
    """
    Hole alle Preismodelle
    
    Returns:
        Liste aller Tiers mit Preisen und Features
    """
    return tier_service.get_all_tiers()


@router.get("/user/{user_id}", response_model=TierResponse)
async def get_user_tier(user_id: str, _admin: dict = Depends(require_admin)):
    """
    Hole Tier eines Users
    
    Args:
        user_id: User-ID oder Email
    
    Returns:
        Aktuelles Tier des Users
    """
    tier = tier_service.get_user_tier(user_id)
    return tier_service.get_tier_info(tier)


@router.get("/user/{user_id}/models", response_model=ModelListResponse)
async def get_user_models(user_id: str, _admin: dict = Depends(require_admin)):
    """
    Hole verfügbare Modelle für einen User
    
    Args:
        user_id: User-ID oder Email
    
    Returns:
        Liste der erlaubten Modelle basierend auf Tier
    """
    tier = tier_service.get_user_tier(user_id)
    models = tier_service.get_allowed_models(user_id)
    
    return {
        "tier": tier.value,
        "model_count": len(models),
        "models": models
    }


@router.post("/user/upgrade")
async def upgrade_user_tier(request: UserTierRequest, _admin: dict = Depends(require_admin)):
    """
    Upgrade User-Tier (nach Payment-Bestätigung)
    
    Args:
        request: User-ID, neues Tier, Dauer in Monaten
    
    Returns:
        Bestätigung mit neuem Tier und Ablaufdatum
    """
    try:
        new_tier = UserTier(request.tier)
    except ValueError:
        raise HTTPException(400, f"Ungültiges Tier: {request.tier}")
    
    # Berechne Ablaufdatum
    expires = datetime.now() + timedelta(days=30 * request.duration_months)
    
    success = tier_service.set_user_tier(
        request.user_id, 
        new_tier, 
        expires
    )
    
    if not success:
        raise HTTPException(500, "Fehler beim Upgrade")
    
    config = TIER_CONFIGS[new_tier]
    
    return {
        "success": True,
        "user_id": request.user_id,
        "tier": new_tier.value,
        "tier_name": config.name,
        "expires": expires.isoformat(),
        "model_count": len(ALL_OPENROUTER_MODELS) if config.models == "all" else len(FREE_MODELS),
        "message": f"Erfolgreich auf {config.name} upgraded!"
    }


@router.get("/models/free", response_model=ModelListResponse)
async def get_free_models():
    """
    Hole alle kostenlosen Modelle
    
    Returns:
        Liste aller :free Modelle
    """
    return {
        "tier": "free",
        "model_count": len(FREE_MODELS),
        "models": FREE_MODELS
    }


@router.get("/models/all", response_model=ModelListResponse)
async def get_all_models():
    """
    Hole alle verfügbaren Modelle (für Pro/Enterprise)
    
    Returns:
        Liste aller OpenRouter Modelle
    """
    return {
        "tier": "pro",
        "model_count": len(ALL_OPENROUTER_MODELS),
        "models": ALL_OPENROUTER_MODELS
    }


@router.post("/check-model")
async def check_model_access(user_id: str, model: str, _admin: dict = Depends(require_admin)):
    """
    Prüfe ob User Zugriff auf ein Modell hat
    
    Args:
        user_id: User-ID
        model: Model-ID
    
    Returns:
        Erlaubt/Nicht erlaubt mit Upgrade-Hinweis
    """
    allowed = tier_service.is_model_allowed(user_id, model)
    tier = tier_service.get_user_tier(user_id)
    
    result = {
        "user_id": user_id,
        "model": model,
        "allowed": allowed,
        "user_tier": tier.value,
    }
    
    if not allowed:
        result["upgrade_required"] = True
        result["message"] = f"Model '{model}' erfordert Pro oder Enterprise. Aktuell: {tier.value}"
        result["upgrade_url"] = "/tiers/pricing"
    
    return result
