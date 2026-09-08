import logging
from typing import Any, Dict

from fastapi import APIRouter, status, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

router = APIRouter()

logger = logging.getLogger("ailinux.health")


# === Hybrid Compute Models ===
class ClientGPUInfoRequest(BaseModel):
    """Client GPU Registration Request"""
    capability: str = "JS_ONLY"
    gpu_vendor: str = ""
    gpu_name: str = ""
    max_buffer_size: int = 0
    max_texture_size: int = 0
    supports_f16: bool = False
    supports_storage_buffers: bool = False
    estimated_tflops: float = 0.0

HEALTH_RESPONSE = {"ok": True, "status": "ok"}


@router.get(
    "/health",
    tags=["Monitoring"],
    summary="Health check endpoint",
    include_in_schema=False,
)
@router.get(
    "/healthz",
    tags=["Monitoring"],
    summary="Kubernetes style health check endpoint",
    include_in_schema=False,
)
async def health_check():
    # Here you could add checks for database connection, external services, etc.
    # For now, a simple success response is sufficient.
    logger.info("Health probe received")
    from ..services.memory_trigger import get_memory_engine
    episodic = await get_memory_engine().health()
    return JSONResponse(content={**HEALTH_RESPONSE, "episodic_memory": episodic}, status_code=status.HTTP_200_OK)


@router.get(
    "/hardware",
    tags=["Monitoring"],
    summary="Hardware acceleration status",
)
async def hardware_status():
    """
    Hardware Acceleration Status.

    Zeigt erkannte Hardware und aktive Beschleunigung:
    - GPU (CUDA/ROCm/oneAPI)
    - CPU Features (AVX2, AVX-512)
    - Optimale Konfiguration (Threads, Batch Size)
    """
    try:
        from ..services.hardware_accel import get_hardware_detector
        detector = get_hardware_detector()
        return JSONResponse(content=detector.to_dict(), status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.warning(f"Hardware detection failed: {e}")
        return JSONResponse(
            content={"error": str(e), "status": "detection_failed"},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.post(
    "/v1/compute/register",
    tags=["Hybrid Compute"],
    summary="Register client GPU for hybrid compute",
)
async def register_compute_client(request: Request, gpu_info: ClientGPUInfoRequest):
    """
    Registriert Client-GPU für Hybrid Compute.

    Der Client meldet seine WebGPU/WebGL-Fähigkeiten,
    und erhält eine Liste der lokal ausführbaren Modelle.
    """
    try:
        from ..services.hybrid_compute import get_hybrid_router, ClientGPUInfo

        # Session ID generieren
        import secrets
        session_id = secrets.token_urlsafe(16)

        # Client GPU Info erstellen
        client_info = ClientGPUInfo.from_dict(gpu_info.model_dump())

        # Bei Router registrieren
        router_instance = get_hybrid_router()
        result = router_instance.register_client(session_id, client_info)

        logger.info(f"Compute client registered: {session_id} ({gpu_info.capability})")
        return JSONResponse(content=result, status_code=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Compute registration failed: {e}")
        return JSONResponse(
            content={"error": str(e), "session_id": None},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get(
    "/v1/compute/status",
    tags=["Hybrid Compute"],
    summary="Get hybrid compute status",
)
async def compute_status():
    """
    Gibt Status des Hybrid Compute Systems zurück.

    Zeigt Server-Last, verbundene Clients und deren GPU-Fähigkeiten.
    """
    try:
        from ..services.hybrid_compute import get_hybrid_router
        from ..services.compute_backend import compute

        router_instance = get_hybrid_router()

        return JSONResponse(content={
            "server": compute.get_status(),
            "hybrid": router_instance.get_stats(),
        }, status_code=status.HTTP_200_OK)

    except Exception as e:
        logger.error(f"Compute status failed: {e}")
        return JSONResponse(
            content={"error": str(e)},
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@router.get(
    "/metrics",
    tags=["Monitoring"],
    summary="Prometheus metrics endpoint",
    include_in_schema=False,
)
async def prometheus_metrics():
    """
    Prometheus metrics endpoint for scraping.

    Provides metrics for:
    - Request count and latency
    - LLM calls and latency
    - Circuit breaker states
    - Memory entries
    - Active connections
    """
    # Import at runtime to avoid circular imports and ensure proper initialization
    try:
        from ..utils.metrics import get_metrics_response
        return get_metrics_response()
    except Exception as e:
        logger.warning(f"Failed to get metrics: {e}")
        return Response(
            content=f"# Metrics not available: {e}\n",
            media_type="text/plain"
        )
