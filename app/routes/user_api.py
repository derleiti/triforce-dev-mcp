# app/routes/user_api.py
import secrets
"""
AILinux User API Routes

Endpoints für:
- WordPress Plugin (Webhooks)
- Client SDK (Settings Sync)
- Device Management

Stand: 2025-12-14
"""

from fastapi import APIRouter, HTTPException, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any, List
from datetime import datetime
import hashlib
import hmac
import logging

from ..services.user_system.user_manager import (
    user_manager, 
    SubscriptionTier,
    UserSettings
)
from ..utils.admin_auth import require_read_access

logger = logging.getLogger("ailinux.user_api")

# Module-level JWT secret — initialize once at import time.
# Prefer environment variable JWT_SECRET; fallback: generate once at process start.
import os
_JWT_SECRET_MODULE = os.environ.get("JWT_SECRET")
if not _JWT_SECRET_MODULE:
    # Generate a stable value at process start if none provided (development fallback).
    # In production, consider failing startup if JWT_SECRET is not explicitly set.
    _JWT_SECRET_MODULE = secrets.token_hex(32)

router = APIRouter()
webhook_router = APIRouter()

# ============================================================================
# Pydantic Models
# ============================================================================

class UserCreateRequest(BaseModel):
    email: EmailStr
    username: str
    tier: str = "free"
    wordpress_user_id: Optional[str] = None


class UserUpgradeRequest(BaseModel):
    user_id: str
    new_tier: str
    expires_at: Optional[str] = None


class DeviceRegisterRequest(BaseModel):
    user_id: str
    device_name: str
    device_type: str = "desktop"


class SettingsSyncRequest(BaseModel):
    settings: Dict[str, Any]
    merge: bool = True


class CredentialSetRequest(BaseModel):
    provider: str
    api_key: str


class WebhookPayload(BaseModel):
    event: str
    user_id: str
    data: Dict[str, Any] = {}
    timestamp: str = ""


# ============================================================================
# WordPress Webhook Secret Verification
# ============================================================================

def _get_webhook_secret() -> str:
    """Lädt Webhook Secret aus Environment"""
    import os
    return (os.environ.get("AILINUX_WEBHOOK_SECRET") or os.environ.get("WEBHOOK_SECRET") or "").strip()

async def verify_webhook_signature(request: Request, x_webhook_signature: str = Header(None)) -> bool:
    """
    Verifiziert WordPress Webhook Signatur mittels HMAC-SHA256.

    WordPress sendet: X-Webhook-Signature: sha256=<hex_digest>
    """
    if not x_webhook_signature:
        logger.warning("Webhook request without signature")
        return False

    webhook_secret = _get_webhook_secret()
    if not webhook_secret:
        logger.error("Webhook authentication unavailable: webhook secret is not configured")
        return False

    # Body für HMAC-Berechnung lesen
    body = await request.body()

    # Erwartete Signatur berechnen
    expected_sig = hmac.new(
        webhook_secret.encode('utf-8'),
        body,
        hashlib.sha256
    ).hexdigest()

    # Signatur-Format: "sha256=<hex>" oder nur "<hex>"
    if x_webhook_signature.startswith("sha256="):
        provided_sig = x_webhook_signature[7:]
    else:
        provided_sig = x_webhook_signature

    # Timing-safe Vergleich
    is_valid = hmac.compare_digest(expected_sig, provided_sig)

    if not is_valid:
        logger.warning("Invalid webhook signature")

    return is_valid


async def require_webhook_signature(
    request: Request,
    x_webhook_signature: str = Header(None),
) -> None:
    """Fail closed unless the WordPress webhook carries a valid HMAC."""
    if not await verify_webhook_signature(request, x_webhook_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")


# ============================================================================
# WordPress Integration Endpoints (Webhooks)
# ============================================================================

@webhook_router.post("/webhook/user-created")
async def webhook_user_created(payload: WebhookPayload, _: None = Depends(require_webhook_signature)):
    """
    WordPress ruft diesen Endpoint auf wenn ein neuer User registriert wird.
    
    Payload:
        {
            "event": "user_created",
            "user_id": "wp_123",
            "data": {
                "email": "user@example.com",
                "username": "testuser",
                "tier": "free"
            }
        }
    """
    try:
        data = payload.data
        result = await user_manager.create_user(
            email=data.get("email", ""),
            username=data.get("username", ""),
            tier=SubscriptionTier(data.get("tier", "free")),
            wordpress_user_id=payload.user_id
        )
        
        logger.info(f"WordPress user created: {payload.user_id}")
        return {"success": True, "user": result}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@webhook_router.post("/webhook/payment-success")
async def webhook_payment_success(payload: WebhookPayload, _: None = Depends(require_webhook_signature)):
    """
    WordPress ruft diesen Endpoint auf bei erfolgreicher Zahlung.
    
    Payload:
        {
            "event": "payment_success",
            "user_id": "wp_123",
            "data": {
                "tier": "pro",
                "amount": 9.99,
                "currency": "EUR",
                "subscription_id": "sub_xxx",
                "expires_at": "2025-12-14T00:00:00"
            }
        }
    """
    try:
        data = payload.data
        new_tier = SubscriptionTier(data.get("tier", "pro"))
        expires = None
        if data.get("expires_at"):
            expires = datetime.fromisoformat(data["expires_at"])
        
        success = await user_manager.upgrade_tier(
            user_id=payload.user_id,
            new_tier=new_tier,
            expires=expires
        )
        
        if success:
            logger.info(f"User upgraded via payment: {payload.user_id} -> {new_tier}")
            return {"success": True, "tier": new_tier.value}
        else:
            raise HTTPException(status_code=404, detail="User not found")
            
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@webhook_router.post("/webhook/subscription-cancelled")
async def webhook_subscription_cancelled(payload: WebhookPayload, _: None = Depends(require_webhook_signature)):
    """Downgrade auf Free bei Abo-Kündigung"""
    try:
        success = await user_manager.upgrade_tier(
            user_id=payload.user_id,
            new_tier=SubscriptionTier.FREE
        )
        
        if success:
            logger.info(f"Subscription cancelled: {payload.user_id}")
            return {"success": True}
        else:
            raise HTTPException(status_code=404, detail="User not found")
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# User Management Endpoints
# ============================================================================

@router.post("/users/create")
async def create_user(request: UserCreateRequest):
    """Erstellt neuen User (Admin oder WordPress)"""
    try:
        result = await user_manager.create_user(
            email=request.email,
            username=request.username,
            tier=SubscriptionTier(request.tier),
            wordpress_user_id=request.wordpress_user_id
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/{user_id}")
async def get_user(user_id: str, _: None = Depends(require_read_access)):
    """Holt User-Informationen"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Sensible Daten entfernen
    data = user.to_dict()
    for device in data.get("devices", []):
        device.pop("client_secret_hash", None)
    
    return data


@router.get("/users/{user_id}/quota")
async def get_user_quota(user_id: str, _: None = Depends(require_read_access)):
    """Holt Quota-Status"""
    quota = await user_manager.check_quota(user_id)
    if "error" in quota:
        raise HTTPException(status_code=404, detail=quota["error"])
    return quota


@router.post("/users/{user_id}/upgrade")
async def upgrade_user(user_id: str, request: UserUpgradeRequest):
    """Upgraded User Tier"""
    try:
        expires = None
        if request.expires_at:
            expires = datetime.fromisoformat(request.expires_at)
        
        success = await user_manager.upgrade_tier(
            user_id=user_id,
            new_tier=SubscriptionTier(request.new_tier),
            expires=expires
        )
        
        if success:
            return {"success": True, "tier": request.new_tier}
        raise HTTPException(status_code=404, detail="User not found")
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Device Management Endpoints
# ============================================================================

@router.post("/users/{user_id}/devices")
async def register_device(user_id: str, request: DeviceRegisterRequest):
    """Registriert neues Gerät"""
    try:
        result = await user_manager.register_device(
            user_id=user_id,
            device_name=request.device_name,
            device_type=request.device_type
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/users/{user_id}/devices")
async def list_devices(user_id: str, _: None = Depends(require_read_access)):
    """Listet alle Geräte eines Users"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    devices = []
    for d in user.devices:
        devices.append({
            "device_id": d.device_id,
            "device_name": d.device_name,
            "device_type": d.device_type,
            "client_id": d.client_id,
            "created_at": d.created_at,
            "last_seen": d.last_seen,
            "is_active": d.is_active,
        })
    
    return {"devices": devices, "count": len(devices)}


@router.delete("/users/{user_id}/devices/{device_id}")
async def revoke_device(user_id: str, device_id: str):
    """Deaktiviert ein Gerät"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    for device in user.devices:
        if device.device_id == device_id:
            device.is_active = False
            user_manager._save_user(user)
            return {"success": True, "device_id": device_id}
    
    raise HTTPException(status_code=404, detail="Device not found")


# ============================================================================
# Settings Sync Endpoints
# ============================================================================

@router.get("/users/{user_id}/settings")
async def get_settings(user_id: str, _: None = Depends(require_read_access)):
    """Holt aktuelle User-Settings"""
    settings = await user_manager.get_settings(user_id)
    if not settings:
        raise HTTPException(status_code=404, detail="User not found")
    return settings.to_dict()


@router.post("/users/{user_id}/settings")
async def sync_settings(user_id: str, request: SettingsSyncRequest):
    """Synchronisiert Settings (Client → Server)"""
    try:
        settings = await user_manager.sync_settings(
            user_id=user_id,
            settings=request.settings,
            merge=request.merge
        )
        return settings.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============================================================================
# Credentials Endpoints (User-eigene API Keys)
# ============================================================================

@router.get("/users/{user_id}/credentials")
async def list_credentials(user_id: str, _: None = Depends(require_read_access)):
    """Listet Provider mit gespeicherten Keys (Keys selbst werden nicht zurückgegeben!)"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    cred_store = user_manager.get_credential_store(user_id)
    providers = cred_store.list_providers()
    
    return {"providers": providers, "count": len(providers)}


@router.post("/users/{user_id}/credentials")
async def set_credential(user_id: str, request: CredentialSetRequest):
    """Speichert API Key für Provider"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    cred_store = user_manager.get_credential_store(user_id)
    cred_store.set(request.provider, request.api_key)
    
    logger.info(f"Credential set: {user_id} / {request.provider}")
    return {"success": True, "provider": request.provider}


@router.delete("/users/{user_id}/credentials/{provider}")
async def remove_credential(user_id: str, provider: str):
    """Entfernt API Key"""
    user = await user_manager.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    cred_store = user_manager.get_credential_store(user_id)
    if cred_store.remove(provider):
        return {"success": True, "provider": provider}
    
    raise HTTPException(status_code=404, detail=f"Provider not found: {provider}")


# ============================================================================
# Authentication Endpoint (für Client SDK)
# ============================================================================

@router.post("/auth/token")
async def get_auth_token(
    client_id: str = Header(None),
    client_secret: str = Header(None)
):
    """
    Authentifiziert Client und gibt Token zurück.
    
    Headers:
        X-Client-ID: desktop_u_xxx_abc123
        X-Client-Secret: geheim...
    
    Returns:
        {
            "token": "jwt...",
            "user_id": "u_xxx",
            "tier": "pro",
            "expires_in": 3600
        }
    """
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=401,
            detail="Missing client credentials"
        )
    
    user = await user_manager.authenticate(client_id, client_secret)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )
    
    # Quota prüfen
    quota = await user_manager.check_quota(user.user_id)

    # JWT Token generieren
    import os
    import time
    import base64
    import json

    jwt_secret = os.environ.get("JWT_SECRET", secrets.token_hex(32))
    expires_in = 3600  # 1 Stunde

    # JWT Header
    header = {"alg": "HS256", "typ": "JWT"}

    # JWT Payload
    now = int(time.time())
    # tier kann Enum oder String sein (nach JSON Deserialisierung)
    tier_value = user.quota.tier.value if hasattr(user.quota.tier, 'value') else user.quota.tier
    payload = {
        "sub": user.user_id,
        "tier": tier_value,
        "iat": now,
        "exp": now + expires_in,
        "iss": "ailinux-api"
    }

    # Base64url encode
    def b64url_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=') .decode('ascii')

    header_b64 = b64url_encode(json.dumps(header).encode())
    payload_b64 = b64url_encode(json.dumps(payload).encode())

    # Signature
    message = f"{header_b64}.{payload_b64}"
    signature = hmac.new(
        jwt_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).digest()
    signature_b64 = b64url_encode(signature)

    token = f"{header_b64}.{payload_b64}.{signature_b64}"

    return {
        "token": token,
        "user_id": user.user_id,
        "tier": tier_value,
        "quota": quota,
        "expires_in": expires_in
    }


# ============================================================================
# Admin Endpoints
# ============================================================================
