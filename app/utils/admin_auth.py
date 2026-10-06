"""
app/utils/admin_auth.py
=======================
Zentrale Admin- und Lese-Auth-Utilities für TriForce.

Verwendung:
    from ..utils.admin_auth import require_admin, validate_allowed_paths, require_read_access

Env-Keys:
    ADMIN_USER_IDS   — optionaler Legacy-Fallback für ältere Client-IDs
    INTERNAL_API_KEY — interner Service-Key (WordPress-Plugin, Backend-Services)
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import List

from fastapi import Depends, Header, HTTPException

logger = logging.getLogger("ailinux.admin_auth")

# ---------------------------------------------------------------------------
# Konfig
# ---------------------------------------------------------------------------

def _load_admin_ids() -> frozenset[str]:
    raw = os.environ.get("ADMIN_USER_IDS", "")
    ids = frozenset(uid.strip() for uid in raw.split(",") if uid.strip())
    if not ids:
        logger.debug("ADMIN_USER_IDS not set; signed authority_role is canonical")
    return ids


# Einmalig beim Modulimport laden; kein Hot-Reload nötig (restart required)
ADMIN_USER_IDS: frozenset[str] = _load_admin_ids()

# Whitelist-Präfixe für grant-file-access — alles andere wird geblockt
ALLOWED_PATH_PREFIXES: tuple[str, ...] = (
    "/home/",
    "/tmp/ailinux/",
    "/var/ailinux/",
)

MAX_PATHS_PER_GRANT = 20


# ---------------------------------------------------------------------------
# Admin-Check
# ---------------------------------------------------------------------------

def require_admin(ctx: dict) -> None:
    """
    Wirft HTTPException(403) wenn ctx['user_id'] kein Admin ist.

    Muss nach get_client_context aufgerufen werden:

        ctx = Depends(get_client_context)
        require_admin(ctx)
    """
    authority_role = str(ctx.get("authority_role") or "").strip().lower()
    if authority_role in {"human_owner", "human_admin"}:
        return

    # Compatibility for legacy client tokens/configurations that predate the
    # shared authority model. New sessions should rely on signed authority_role.
    user_id = str(ctx.get("user_id") or "").strip()
    if user_id and user_id in ADMIN_USER_IDS:
        return

    logger.warning(
        "Admin access denied for user_id=%r authority_role=%r",
        user_id,
        authority_role,
    )
    raise HTTPException(403, "Admin-Zugriff erforderlich")


# ---------------------------------------------------------------------------
# Path-Validierung
# ---------------------------------------------------------------------------

def validate_allowed_paths(paths: List[str]) -> List[str]:
    """
    Validiert und normalisiert eine Pfad-Liste für grant-file-access.

    - Blockiert Path-Traversal (../)
    - Blockiert Pfade außerhalb ALLOWED_PATH_PREFIXES
    - Limitiert Anzahl auf MAX_PATHS_PER_GRANT

    Returns normalized paths (absolut, ohne trailing slash).
    Raises HTTPException(400) bei ungültigem Pfad.
    """
    if not paths:
        raise HTTPException(400, "Mindestens ein Pfad erforderlich")
    if len(paths) > MAX_PATHS_PER_GRANT:
        raise HTTPException(
            400,
            f"Maximal {MAX_PATHS_PER_GRANT} Pfade pro Grant erlaubt, erhalten: {len(paths)}",
        )

    normalized = []
    for raw in paths:
        # 1. Nur absolute Pfade erlaubt — relative Pfade koennen Traversal verstecken
        if not raw.startswith("/"):
            raise HTTPException(400, f"Nur absolute Pfade erlaubt: {raw!r}")

        # 2. '..' Segmente explizit blocken — CWD-unabhaengig, vor resolve()
        from pathlib import PurePosixPath
        if ".." in PurePosixPath(raw).parts:
            raise HTTPException(400, f"Pfad darf kein '..' enthalten: {raw!r}")

        try:
            p = Path(raw).resolve()
        except Exception:
            raise HTTPException(400, f"Ungueltiger Pfad: {raw!r}")

        normed = str(p)

        # 3. Nach Normalisierung gegen Whitelist pruefen
        if not any(normed.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
            raise HTTPException(
                400,
                f"Pfad nicht erlaubt (ausserhalb Whitelist): {normed!r}",
            )

        normalized.append(normed)

    return normalized


# ---------------------------------------------------------------------------
# Lese-Auth für user_api GET-Endpunkte
# ---------------------------------------------------------------------------

def require_read_access(x_internal_key: str = Header(default="")) -> None:
    """
    FastAPI Dependency für GET /users/* Endpunkte.

    Erlaubt Zugriff nur mit gültigem INTERNAL_API_KEY.
    Ohne Key → 401.

    Verwendung:
        @router.get("/users/{user_id}")
        async def get_user(user_id: str, _: None = Depends(require_read_access)):
            ...
    """
    expected = os.environ.get("INTERNAL_API_KEY", "")
    if not expected or x_internal_key != expected:
        raise HTTPException(
            status_code=401,
            detail="Authentifizierung erforderlich (X-Internal-Key)",
        )
