from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from app.routes.client_auth import USER_REGISTRY, normalize_entitlements


router = APIRouter()


USERS_FILE = Path(os.getenv("TRIFORCE_USERS_FILE", "config/users.json"))


def _allowed_secrets() -> set[str]:
    keys = [
        # INTERNAL_API_KEY ist der kanonische Schluessel zwischen WordPress und TriForce
        # (WP: NOVA_AI_INTERNAL_KEY == TF: INTERNAL_API_KEY). Er fehlte hier, weshalb der
        # Entitlement-Sync aus WordPress dauerhaft mit 401 abgewiesen wurde.
        "INTERNAL_API_KEY",
        "NOVA_AI_INTERNAL_KEY",
        "WEBHOOK_SECRET",
        "TRIFORCE_ADMIN_SECRET",
        "MCP_ADMIN_TOKEN",
        "MCP_AUTH_TOKEN",
    ]
    return {os.getenv(k, "").strip() for k in keys if os.getenv(k, "").strip()}


def _check_secret(
    x_internal_key: Optional[str],
    x_nova_webhook_secret: Optional[str],
    authorization: Optional[str],
) -> None:
    candidates = _allowed_secrets()
    given = x_internal_key or x_nova_webhook_secret or ""

    if not given and authorization:
        prefix = "Bearer "
        if authorization.startswith(prefix):
            given = authorization[len(prefix):].strip()

    if not given or given not in candidates:
        raise HTTPException(status_code=401, detail="unauthorized")


def _load_users() -> Dict[str, Any]:
    if not USERS_FILE.exists():
        return {}
    with USERS_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise HTTPException(status_code=500, detail="users.json is not an object")
    return data


def _atomic_write_users(data: Dict[str, Any]) -> None:
    USERS_FILE.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=USERS_FILE.name + ".",
        suffix=".tmp",
        dir=str(USERS_FILE.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_name, USERS_FILE)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass


class UserUpsertPayload(BaseModel):
    email: EmailStr
    name: Optional[str] = None
    tier: Optional[str] = "free"
    billing: Optional[bool] = None
    source: Optional[str] = None
    customer_id: Optional[str] = None
    client_id: Optional[str] = None
    # Accept legacy list-shaped WordPress metadata as well as canonical maps.
    nova_entitlements: Any = Field(default_factory=dict)
    entitlements: Any = Field(default_factory=dict)
    # WordPress (EntitlementsService::sync_to_backend) schickt gekaufte Produkte als
    # Liste roher LemonSqueezy-Produkt-IDs, z.B. ["970007"]. Ohne dieses Feld hat
    # pydantic sie stillschweigend verworfen -> Kauf kam nie im Account an.
    extra: Any = None


def _merge_user(payload: UserUpsertPayload) -> Dict[str, Any]:
    users = _load_users()
    email = payload.email.lower()

    current = users.get(email)
    if not isinstance(current, dict):
        current = {}

    name = payload.name or current.get("name") or email.split("@", 1)[0]
    tier = payload.tier or current.get("tier") or "free"

    entitlements = {}
    if isinstance(current.get("nova_entitlements"), dict):
        entitlements.update(current["nova_entitlements"])

    entitlements.update(normalize_entitlements(payload.entitlements))
    entitlements.update(normalize_entitlements(payload.nova_entitlements))

    # Rohe Produkt-IDs/Slugs aus 'extra' auf kanonische Schluessel abbilden.
    # Fuer WordPress ist 'extra' die autoritative Kauf-/Refund-Liste: auch eine
    # explizit leere Liste muss bestehende Entitlements entfernen.
    if payload.source == "wordpress" and payload.extra is not None:
        entitlements = normalize_entitlements(payload.extra)
    elif payload.extra:
        entitlements.update(normalize_entitlements(payload.extra))

    billing = payload.billing
    if billing is None:
        billing = current.get("billing", False)

    merged = {
        **current,
        "tier": tier,
        "name": name,
        "billing": bool(billing),
        "nova_entitlements": entitlements,
        "entitlements": entitlements,
    }

    if payload.source:
        merged["source"] = payload.source

    if payload.customer_id:
        merged.setdefault("billing_meta", {})
        merged["billing_meta"]["customer_id"] = payload.customer_id

    if payload.client_id:
        merged["client_id"] = payload.client_id

    users[email] = merged
    _atomic_write_users(users)
    USER_REGISTRY[email] = merged

    return merged


@router.post("/admin/users/upsert")
@router.post("/admin/users/entitlements")
@router.post("/users/entitlements")
@router.post("/user/entitlements")
async def upsert_user_entitlements(
    payload: UserUpsertPayload,
    request: Request,
    x_internal_key: Optional[str] = Header(default=None),
    x_nova_webhook_secret: Optional[str] = Header(default=None),
    authorization: Optional[str] = Header(default=None),
):
    _check_secret(x_internal_key, x_nova_webhook_secret, authorization)

    merged = _merge_user(payload)

    return {
        "ok": True,
        "email": payload.email.lower(),
        "user": merged,
    }
