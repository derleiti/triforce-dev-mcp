"""Fail-closed NVIDIA NIM access decision for publicly served inference.

NVIDIA Build's 'Free Endpoint' label is a development/prototyping offer,
not proof that the account may serve arbitrary public users for free.
Set NVIDIA_PUBLIC_FREE_ENABLED=1 and a *specific* comma-delimited list of
NVIDIA_PUBLIC_FREE_MODEL_IDS only after confirming the account's permitted
usage and putting provider quotas/budgets in place. No automatic opt-in.
"""
from __future__ import annotations

import os
import time


def is_public_free_nvidia_model(model_id: str) -> bool:
    if os.environ.get('NVIDIA_PUBLIC_FREE_ENABLED', '').lower() not in {'1', 'true', 'yes'}:
        return False
    explicit_ids = {value.strip() for value in os.environ.get('NVIDIA_PUBLIC_FREE_MODEL_IDS', '').split(',') if value.strip()}
    if not model_id.startswith('nvidia/') or model_id not in explicit_ids:
        return False

    from .model_registry import registry
    from .model_availability import availability_service
    from .model_capabilities import get_store
    matched = next((m for m in (registry._cache or []) if m.id == model_id), None)
    if not matched or not matched.free_endpoint or matched.availability != 'ok':
        return False
    if 'chat' not in matched.capabilities or not availability_service.is_available(model_id):
        return False
    record = get_store().get(model_id)
    if not record or record.availability != 'ok' or record.source != 'probed':
        return False
    # Provider switches, 403s and retired NIMs change over time. Fail closed
    # on stale capability evidence even when cached registry says 'ok'.
    return bool(record.probed_at > 0 and time.time() - record.probed_at < 12 * 3600)
