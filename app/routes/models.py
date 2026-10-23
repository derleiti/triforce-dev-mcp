from __future__ import annotations

from fastapi import APIRouter, Query, Depends, Header
from .client_auth import require_admin

from ..services.model_registry import registry
from ..services.model_availability import availability_service

router = APIRouter(tags=["models"])


@router.get("/models")
async def list_models(
    force_refresh: bool = Query(False, description="Force-refresh the model registry"),
    include_unavailable: bool = Query(False, description="Include unverified and unavailable models (internal/admin only)"),
    authorization: str | None = Header(None),
) -> dict[str, object]:
    """
    Liste alle verfügbaren Modelle.
    Modelle mit Quota-Problemen werden standardmäßig NICHT angezeigt.
    """
    models = await registry.list_models(force_refresh=force_refresh)
    
    if include_unavailable:
        await require_admin(authorization)
        # Admin-Modus: Alle Modelle zeigen
        return {
            "data": [model.to_dict() for model in models],
            "total": len(models),
            "filtered": False
        }
    
    # Standard: Nur verfügbare Modelle
    available_models = [
        model for model in models 
        if availability_service.is_available(model.id) and model.availability == "ok"
    ]
    
    excluded_count = len(models) - len(available_models)
    
    return {
        "data": [model.to_dict() for model in available_models],
        "total": len(available_models),
        "excluded": excluded_count,
        "filtered": True
    }


@router.get("/models/catalog")
async def shared_model_catalog(
    force_refresh: bool = Query(False),
    free_only: bool = Query(False),
    capability: str | None = Query(None),
) -> dict[str, object]:
    """Single public catalog for Nova, AICoder, Playground and MCP clients.

    Never publish models without a successful availability verification.
    """
    discovered = await registry.list_models(force_refresh=force_refresh)
    available = [m for m in discovered if m.availability == "ok" and availability_service.is_available(m.id)]
    if free_only:
        available = [m for m in available if m.free_status == "catalog_free"]
    if capability:
        available = [m for m in available if capability in m.capabilities]
    result = registry.model_catalog(available)
    result["filtered"] = True
    result["free_only"] = free_only
    result["capability_filter"] = capability
    return result


@router.get("/models/nvidia/status")
async def nvidia_model_status(_admin: dict = Depends(require_admin)) -> dict[str, object]:
    """Compare NVIDIA catalog discovery with verified usable models.

    Catalog membership is not proof of account access or zero-cost inference.
    """
    discovered = [m for m in await registry.list_models() if m.provider == "nvidia"]
    verified = [m for m in discovered if m.availability == "ok" and availability_service.is_available(m.id)]
    from ..services.nvidia_free_endpoints import NVIDIA_BUILD_FREE_ENDPOINT_IDS
    free_endpoint_matches = [m for m in discovered if m.free_endpoint]
    return {
        "provider": "nvidia",
        "build_free_endpoint_listed": len(NVIDIA_BUILD_FREE_ENDPOINT_IDS),
        "build_free_endpoint_api_matches": len(free_endpoint_matches),
        "build_free_endpoint_unmatched_ids": sorted(
            NVIDIA_BUILD_FREE_ENDPOINT_IDS - {m.id.removeprefix("nvidia/") for m in free_endpoint_matches}
        ),
        "discovered": len(discovered),
        "verified_usable": len(verified),
        "unverified_or_unavailable": len(discovered) - len(verified),
        "models": [{"id": m.id, "availability": m.availability,
                    "capabilities": m.capabilities, "free_status": m.free_status,
                    "verified_usable": m.availability == "ok" and availability_service.is_available(m.id)}
                   for m in discovered],
        "note": "NVIDIA model listing does not establish free pricing or execution permission",
    }


@router.get("/models/all")
async def list_all_models(force_refresh: bool = Query(False), _admin: dict = Depends(require_admin)) -> dict[str, object]:
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
async def unavailable_models(_admin: dict = Depends(require_admin)) -> dict[str, object]:
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
