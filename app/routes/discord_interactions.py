"""Discord HTTP interactions endpoint for the AILinux Discord application.

Discord calls this endpoint using signed POSTs. The public application key is
not a credential; it only verifies the authenticity of incoming interactions.
There are intentionally no Discord bot-token or privileged guild operations
behind this route.
"""
import json
import os
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

router = APIRouter()

# Public key from Discord Developer Portal (Application 1562939604829470741).
# Override via an environment variable if the application key is ever rotated.
DEFAULT_PUBLIC_KEY = "002156aa62ce723dc138484ebfaa7943be8c8b76d7e9b3f96e9121d4fa77a796"
_public_key = Ed25519PublicKey.from_public_bytes(
    bytes.fromhex(os.getenv("AILINUX_DISCORD_APPLICATION_PUBLIC_KEY", DEFAULT_PUBLIC_KEY))
)
MAX_BODY_BYTES = 262144
MAX_CLOCK_SKEW_SECONDS = 300


@router.post("/discord", include_in_schema=False)
async def discord_interactions(request: Request) -> Response:
    """Validate Ed25519 and answer Discord's signed PING with PONG."""
    signature_hex = request.headers.get("X-Signature-Ed25519", "")
    timestamp = request.headers.get("X-Signature-Timestamp", "")

    if not timestamp.isascii() or not timestamp.isdecimal() or len(timestamp) > 16:
        return Response(status_code=401)
    if len(signature_hex) != 128:
        return Response(status_code=401)
    try:
        if abs(int(time.time()) - int(timestamp)) > MAX_CLOCK_SKEW_SECONDS:
            return Response(status_code=401)
        signature = bytes.fromhex(signature_hex)
    except (ValueError, OverflowError):
        return Response(status_code=401)

    length = request.headers.get("content-length", "")
    if length.isdecimal() and int(length) > MAX_BODY_BYTES:
        return Response(status_code=413)

    raw_body = await request.body()
    if len(raw_body) > MAX_BODY_BYTES:
        return Response(status_code=413)

    try:
        _public_key.verify(signature, timestamp.encode("ascii") + raw_body)
    except (InvalidSignature, ValueError):
        return Response(status_code=401)

    try:
        payload = json.loads(raw_body)
        kind = payload["type"]
    except (ValueError, KeyError, TypeError):
        return Response(status_code=400)

    if kind == 1:
        # Discord verifies this PONG when saving Interactions Endpoint URL.
        return JSONResponse({"type": 1})

    # No application commands are wired up yet. Do not execute arbitrary
    # requests or claim successful guild management; acknowledge privately.
    if kind in (2, 3, 5):
        return JSONResponse(
            {
                "type": 4,
                "data": {
                    "content": "AILinux Discord-Integration ist erreichbar. Befehle werden noch eingerichtet.",
                    "flags": 64,
                },
            }
        )
    if kind == 4:
        return JSONResponse({"type": 8, "data": {"choices": []}})
    return Response(status_code=400)
