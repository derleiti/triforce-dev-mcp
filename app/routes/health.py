import hmac
import logging
import os
import socket
import time
from typing import Any, Dict

import psutil

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


def _local_node_id() -> str:
    hostname = socket.gethostname().lower()
    if "backup" in hostname:
        return "backup"
    if "zombie" in hostname:
        return "zombie-pc"
    return "hetzner"


def _federation_metrics_allowed(request: Request) -> bool:
    secret = os.getenv("FEDERATION_SECRET", "").strip()
    provided = request.headers.get("X-Federation-Key", "").strip()
    return bool(secret and provided and hmac.compare_digest(secret, provided))


def _compute_health_metrics() -> Dict[str, Any]:
    """Cheap host-capacity snapshot for federation scheduling.

    These values are transported on /health only for authenticated federation
    probes. Missing platform metrics degrade to safe
    defaults instead of failing the health probe.
    """
    try:
        cpu_percent = float(psutil.cpu_percent(interval=None))
    except Exception:
        cpu_percent = 0.0
    try:
        memory = psutil.virtual_memory()
        memory_percent = float(memory.percent)
        memory_available_mb = int(memory.available / (1024 * 1024))
    except Exception:
        memory_percent = 0.0
        memory_available_mb = 0
    try:
        swap = psutil.swap_memory()
        swap_free_mb = int(swap.free / (1024 * 1024))
    except Exception:
        swap_free_mb = 0
    try:
        disk = psutil.disk_usage("/")
        disk_free_gb = round(disk.free / (1024 ** 3), 2)
    except Exception:
        disk_free_gb = 0.0
    try:
        load1, _load5, _load15 = os.getloadavg()
    except (AttributeError, OSError):
        load1 = 0.0
    cpu_count = max(int(psutil.cpu_count() or 1), 1)

    return {
        "node_id": _local_node_id(),
        "cpu_percent": round(cpu_percent, 2),
        "cpu_count": cpu_count,
        "load1": round(float(load1), 3),
        "load_ratio": round(float(load1) / cpu_count, 4),
        "memory_percent": round(memory_percent, 2),
        "memory_available_mb": memory_available_mb,
        "swap_free_mb": swap_free_mb,
        "disk_free_gb": disk_free_gb,
        "timestamp": int(time.time()),
    }

ANDROID_APP_PACKAGE = os.getenv("AILINUX_ANDROID_APP_PACKAGE", "me.ailinux.workspace")
ANDROID_APP_CERT_SHA256 = os.getenv(
    "AILINUX_ANDROID_SHA256_CERT_FINGERPRINT",
    "3D:4B:5C:38:78:F1:95:08:53:AF:BF:20:A8:DF:F4:90:7A:BC:9C:8D:F1:D2:68:B3:F6:1E:16:DC:F4:9D:7E:21",
)


@router.get(
    "/.well-known/assetlinks.json",
    tags=["Android"],
    summary="Android App Links association",
    include_in_schema=False,
)
async def android_assetlinks():
    """Publish the Digital Asset Links contract for the signed AILinux Helper."""
    return JSONResponse(
        content=[{
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": ANDROID_APP_PACKAGE,
                "sha256_cert_fingerprints": [ANDROID_APP_CERT_SHA256],
            },
        }],
        headers={"Cache-Control": "public, max-age=3600"},
    )


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
async def health_check(request: Request):
    # Here you could add checks for database connection, external services, etc.
    # For now, a simple success response is sufficient.
    logger.info("Health probe received")
    from ..services.memory_trigger import get_memory_engine
    episodic = await get_memory_engine().health()
    payload = {**HEALTH_RESPONSE, "episodic_memory": episodic}
    if _federation_metrics_allowed(request):
        payload["node_metrics"] = _compute_health_metrics()
    return JSONResponse(content=payload, status_code=status.HTTP_200_OK)


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
