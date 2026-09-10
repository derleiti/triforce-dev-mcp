from __future__ import annotations

from fastapi import APIRouter, Query

from ..services.model_registry import registry
from ..services.model_availability import availability_service

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(
    force_refresh: bool = Query(False, description="Force-refresh the model registry"),
    include_unavailable: bool = Query(False, description="Include quota-exhausted models")
) -> dict[str, object]:
    """
    Liste alle verfügbaren Modelle.
    Modelle mit Quota-Problemen werden standardmäßig NICHT angezeigt.
    """
    models = await registry.list_models(force_refresh=force_refresh)
    
    if include_unavailable:
        # Admin-Modus: Alle Modelle zeigen
        return {
            "data": [model.to_dict() for model in models],
            "total": len(models),
            "filtered": False
        }
    
    # Standard: Nur verfügbare Modelle
    available_models = [
        model for model in models 
        if availability_service.is_available(model.id)
    ]
    
    excluded_count = len(models) - len(available_models)
    
    return {
        "data": [model.to_dict() for model in available_models],
        "total": len(available_models),
        "excluded": excluded_count,
        "filtered": True
    }


@router.get("/models/all")
async def list_all_models(force_refresh: bool = Query(False)) -> dict[str, object]:
    """Liste ALLE Modelle (inkl. unavailable) - für Admin/Debug"""
    models = await registry.list_models(force_refresh=force_refresh)
    excluded = list(availability_service.get_excluded_models())
    
    return {
        "data": [model.to_dict() for model in models],
        "total": len(models),
        "excluded_models": excluded,
        "excluded_count": len(excluded)
    }


@router.get("/models/capabilities")
async def capability_index(
    usable_only: bool = Query(True, description="Hide models that cannot currently answer"),
) -> dict[str, object]:
    """Inverted index: capability -> model ids, plus store statistics.

    This is the list TriForce tools should consult when they need "a model
    that can do X" instead of hardcoding model names.
    """
    from ..services.model_capabilities import get_store, ALL_CAPABILITIES

    store = get_store()
    index = store.index(usable_only=usable_only)
    return {
        "capabilities": {cap: index.get(cap, []) for cap in ALL_CAPABILITIES},
        "counts": {cap: len(index.get(cap, [])) for cap in ALL_CAPABILITIES},
        "stats": store.stats(),
        "usable_only": usable_only,
    }


@router.get("/models/by-capability/{capability}")
async def models_by_capability(
    capability: str,
    usable_only: bool = Query(True),
    provider: str | None = Query(None, description="Restrict to one provider"),
) -> dict[str, object]:
    """All models offering one capability, newest verification first."""
    from ..services.model_capabilities import get_store

    store = get_store()
    records = [
        r for r in store.all()
        if capability in r.capabilities and (r.usable or not usable_only)
        and (provider is None or r.provider == provider)
    ]
    records.sort(key=lambda r: ({"probed": 0, "declared": 1, "heuristic": 2}.get(r.source, 3),
                                r.model_id))
    return {
        "capability": capability,
        "total": len(records),
        "models": [
            {
                "id": r.model_id,
                "provider": r.provider,
                "capabilities": r.capabilities,
                "source": r.source,
                "availability": r.availability,
                "detail": r.availability_detail,
            }
            for r in records
        ],
    }


@router.get("/models/unavailable")
async def unavailable_models() -> dict[str, object]:
    """Models that exist in the registry but cannot currently serve requests.

    Split by reason so an exhausted credit balance is not confused with a
    model the provider has retired.
    """
    from ..services.model_capabilities import get_store

    buckets: dict[str, list[dict[str, str]]] = {}
    for r in get_store().all():
        if r.usable:
            continue
        buckets.setdefault(r.availability, []).append(
            {"id": r.model_id, "provider": r.provider, "detail": r.availability_detail}
        )
    return {
        "total": sum(len(v) for v in buckets.values()),
        "by_reason": {k: sorted(v, key=lambda x: x["id"]) for k, v in buckets.items()},
    }
