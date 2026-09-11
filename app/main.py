import os
from contextlib import asynccontextmanager
import logging
# import logging (centralized)
import redis.asyncio as redis
from fastapi import FastAPI, Request, status, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_oauth2_redirect_html
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pathlib import Path
from .utils.rate_limit_compat import FastAPILimiter
from typing import Optional

# Unified logging für alle Komponenten
try:
    from .utils.unified_logger import setup_unified_logging
    setup_unified_logging()
except ImportError:
    pass

# Setup module logger once at top
logger = logging.getLogger(__name__)

# Prometheus metrics integration
try:
    from prometheus_fastapi_instrumentator import Instrumentator  # optional
    _HAS_INSTRUMENTATOR = True
except Exception:
    _HAS_INSTRUMENTATOR = False

try:
    from .utils.metrics import MetricsMiddleware, get_metrics_response
    _HAS_CUSTOM_METRICS = True
except Exception:
    _HAS_CUSTOM_METRICS = False

# TriForce Central Logging
try:
    from .utils.triforce_logging import (
        central_logger,
        TriForceLoggingMiddleware,
        setup_triforce_logging,
    )
    _HAS_TRIFORCE_LOGGING = True
except Exception:
    _HAS_TRIFORCE_LOGGING = False

# Central Logging (all logs to ./triforce/logs/)
try:
    from .utils.central_logging import setup_central_logging, LOG_DIR
    setup_central_logging()
    _HAS_CENTRAL_LOGGING = True
except Exception:
    _HAS_CENTRAL_LOGGING = False

# System Log Collector (kernel, apps, journald -> /triforce/logs/)
try:
    from .utils.system_log_collector import init_system_logging, system_log_collector
    _HAS_SYSTEM_LOG_COLLECTOR = True
except Exception:
    _HAS_SYSTEM_LOG_COLLECTOR = False

from .config import get_settings

# Import the router object from each route module
from .routes.admin import router as admin_router
from .routes.admin_crawler import router as admin_crawler_router
from .routes.agents import router as agents_router
from .routes.chat import router as chat_router
from .routes.crawler import router as crawler_router
from .routes.health import router as health_router
from .routes.mcp import public_router as mcp_public_router
from .routes.mcp import router as mcp_router
from .routes.mcp_node import router as mcp_node_router
from .routes.mcp_remote import router as mcp_remote_router
from .routes.models import router as models_router
from .routes.openai_compat import router as openai_compat_router
from .routes.orchestration import router as orchestration_router
from .routes.posts import router as posts_router
from .routes.settings import router as settings_router
from .routes.vision import router as vision_router_v1
from .routes.text_analysis import router as text_router
from .routes.triforce import router as triforce_router
from .routes.tristar import router as tristar_router
from .routes.tristar_settings import router as tristar_settings_router
from .routes.mesh import router as mesh_router
from .routes.oauth_service import router as oauth_router
from .routes.perf_monitor import router as perf_monitor_router, perf_middleware
from .routes.distributed_compute import router as distributed_compute_router
from .routes.tristar_gui import router as tristar_gui_router
from .routes.client_chat import router as client_chat_router
from .routes.client_auth import router as client_auth_router
from .routes.client_update import router as client_update_router
from .routes.client_logs import router as client_logs_router
from .routes.client_ocr import router as client_ocr_router
from .routes.federation import router as federation_router
from .routes.nova_chat_agent import router as nova_chat_agent_router
from .routes.mistral_agents import router as mistral_agents_router
from .routes.remote_coding_agent import router as remote_coding_agent_router
from .routes.shared_notify import router as shared_notify_router
from .routes.nova_frontend import public_router as nova_frontend_public_router
from .routes.nova_frontend import router as nova_frontend_router
from .routes.nova_playground import router as nova_playground_router
from .routes.nova_wordpress import router as nova_wordpress_router
from .routes.nova_operator import router as nova_operator_router
from .routes.rag import router as rag_router
from .routes.search_curated import router as search_curated_router
from app.routes.admin_users import router as admin_users_router

# Import routers from the top-level app directory
from .routes_sd3 import router as sd3_router
from .routes_vision import router as vision_router


async def _delayed_bootstrap():
    """Verzögerter Bootstrap der CLI Agents nach Server-Start"""
    import asyncio
    # import logging (centralized)
    # Warte bis Server vollständig gestartet ist
    await asyncio.sleep(5)
    try:
        from .services.agent_bootstrap import bootstrap_service
        result = await bootstrap_service.bootstrap_all(sequential_lead=True)
        logger.info(
            "Agent Bootstrap complete: %s agents ready for on-demand calls",
            result.get("success_count", 0),
        )
    except Exception as e:
        logger.error(f"Agent Bootstrap failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # import logging (centralized)

    # === Hardware Acceleration Auto-Detection ===
    try:
        from .services.hardware_accel import init_hardware_acceleration, get_hardware_config
        hw_config = init_hardware_acceleration()
        logger.info(f"Hardware Acceleration: {hw_config.get('primary_accelerator', 'CPU')}")
        logger.info(f"  GPUs: {len(hw_config.get('gpus', []))}, Threads: {hw_config.get('recommended_threads', 4)}")
    except Exception as e:
        logger.warning(f"Hardware detection failed (using defaults): {e}")

    settings = get_settings()
    # Connect to Redis and initialize the rate limiter
    redis_connection = redis.from_url(settings.redis_url, encoding="utf-8", decode_responses=True)
    await FastAPILimiter.init(redis_connection)

    # Start periodic model registry refresh (every hour)
    from .services.model_registry import registry
    registry.start_periodic_refresh(interval_seconds=3600.0)

    # Initialize TriStar services
    try:
        from .services.tristar.memory_controller import memory_controller
        from .services.tristar.model_init import model_init_service
        from .services.tristar.agent_controller import agent_controller
        from .services.tristar.settings_controller import settings_controller
        await memory_controller.initialize()
        await model_init_service.initialize()
        await agent_controller.initialize()
        await settings_controller.initialize()
    except Exception as e:
        # import logging (centralized)
        logger.warning(f"Failed to initialize TriStar services: {e}")

    # Start TriForce Central Logger
    if _HAS_TRIFORCE_LOGGING:
        try:
            await central_logger.start()
            setup_triforce_logging()
            # import logging (centralized)
            logger.info("TriForce Central Logging initialized")
        except Exception as e:
            # import logging (centralized)
            logger.warning(f"Failed to initialize TriForce logging: {e}")

    # Start Mesh Coordinator
    try:
        from .services.mesh_coordinator import mesh_coordinator
        await mesh_coordinator.start()
        # import logging (centralized)
        logger.info("Mesh Coordinator started")
    except Exception as e:
        # import logging (centralized)
        logger.warning(f"Failed to start Mesh Coordinator: {e}")

    # Start Federation Manager only when explicitly configured with a secret.
    # Fresh/local installations remain standalone and do not attempt peer calls.
    if os.environ.get("FEDERATION_SECRET", "").strip():
        try:
            from .services.server_federation import federation_manager
            from .services.federation_websocket import federation_lb
            import socket
            node_id_env = os.environ.get("NODE_ID")
            _hostname = socket.gethostname().lower()
            node_id = node_id_env or ("backup" if "backup" in _hostname else "zombie-pc" if "zombie" in _hostname else "hetzner")
            await federation_manager.initialize(node_id=node_id)
            await federation_lb.start()
            logger.info("Federation Manager started")
        except Exception as e:
            logger.warning(f"Failed to start Federation Manager: {e}")
    else:
        logger.info("Federation Manager disabled (FEDERATION_SECRET not configured)")

    # Start Distributed Compute Manager
    try:
        from .services.distributed_compute import get_distributed_compute
        distributed_compute = get_distributed_compute()
        await distributed_compute.start()
        # import logging (centralized)
        logger.info("Distributed Compute Manager started")
    except Exception as e:
        # import logging (centralized)
        logger.warning(f"Failed to start Distributed Compute Manager: {e}")

    # Start MCP Server Brain (Mitdenk-Funktion)
    try:
        from .services.init_service import mcp_brain
        await mcp_brain.start()
        # import logging (centralized)
        logger.info("MCP Server Brain started")
    except Exception as e:
        # import logging (centralized)
        logger.warning(f"Failed to start MCP Brain: {e}")

    # Start optional MCP WebSocket mesh. Existing installs retain the historical
    # default; fresh package config explicitly sets MCP_WS_ENABLED=false.
    if settings.mcp_ws_enabled:
        try:
            from .services.mcp_ws_server import mcp_ws_server
            await mcp_ws_server.start()
            logger.info(f"MCP WebSocket Server started on port {settings.mcp_ws_port}")
        except Exception as e:
            logger.warning(f"Failed to start MCP WebSocket Server: {e}")
    else:
        logger.info("MCP WebSocket Server disabled")

    # Auto-Bootstrap CLI Agents (wenn konfiguriert)
    try:
        from .services.agent_bootstrap import bootstrap_service
        auto_bootstrap = os.environ.get("AUTO_BOOTSTRAP_AGENTS", "false").lower() == "true"
        if auto_bootstrap:
            # import logging (centralized)
            logger.info("Auto-bootstrapping CLI Agents...")
            import asyncio
            # Verzögert starten um Server hochfahren zu lassen
            asyncio.create_task(_delayed_bootstrap())
        else:
            # import logging (centralized)
            logger.info("Agent Bootstrap available (AUTO_BOOTSTRAP_AGENTS=true to enable)")
    except Exception as e:
        # import logging (centralized)
        logger.warning(f"Failed to setup Agent Bootstrap: {e}")
    try:
        from .mcp.notification_manager import start_pollers
        start_pollers()
        logger.info("Notification Manager pollers requested")
        from .services.tristar.idle_worker import start_idle_worker
        start_idle_worker()
        logger.info("TriStar idle worker startup evaluated")
    except Exception:
        logger.warning("Notification Manager start skipped")

    # Initialize System Log Collector (collects kernel, apps, journald logs)
    if _HAS_SYSTEM_LOG_COLLECTOR:
        try:
            # import logging (centralized)
            logger.info("Initializing System Log Collector...")
            result = await init_system_logging()
            sources = result.get("sources_collected", [])
            logger.info(f"System Log Collector initialized: {len(sources)} sources collected")
        except Exception as e:
            # import logging (centralized)
            logger.warning(f"Failed to initialize System Log Collector: {e}")

    yield

    # Clean up resources on shutdown
    await registry.stop_periodic_refresh()

    # Stop TriForce Central Logger
    if _HAS_TRIFORCE_LOGGING:
        try:
            await central_logger.stop()
        except Exception:
            pass

    try:
        from .services.tristar.agent_controller import agent_controller
        await agent_controller.shutdown()
    except Exception:
        pass

    # Stop Mesh Coordinator
    try:
        from .services.mesh_coordinator import mesh_coordinator
        await mesh_coordinator.stop()
    except Exception:
        pass

    # Stop MCP Server Brain

    # Stop MCP WebSocket Server
    try:
        from .services.mcp_ws_server import mcp_ws_server
        await mcp_ws_server.stop()
    except Exception:
        pass
    try:
        from .services.init_service import mcp_brain
        await mcp_brain.stop()
    except Exception:
        pass

    # Stop Distributed Compute Manager
    try:
        from .services.distributed_compute import get_distributed_compute
        distributed_compute = get_distributed_compute()
        await distributed_compute.stop()
    except Exception:
        pass

    # Stop System Log Collector
    if _HAS_SYSTEM_LOG_COLLECTOR:
        try:
            await system_log_collector.stop()
        except Exception:
            pass

    await FastAPILimiter.close()

def create_app() -> FastAPI:
    # import logging (centralized)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    app = FastAPI(
        title="AILinux AI Server",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False  # Verhindert 307 Redirect bei trailing slash
    )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        import json
        logger.error("Request validation error: %s", json.dumps(exc.errors()))
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": exc.errors(), "body": exc.body},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if isinstance(exc.detail, dict) and "error" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        import traceback
        logger.error(f"Global Unhandled Exception on {request.url.path}: {exc}")
        logger.error(traceback.format_exc())
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "Internal Server Error",
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred."
                }
            }
        )

    settings = get_settings()
    allowed_origins = [origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()]

    # Only add CORS middleware if origins are configured
    # If empty, assume nginx/reverse proxy handles CORS
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # =========================================================================
    # Primary Routes (/v1 prefix)
    # Note: MCP health check is now handled by mcp_router with transport detection
    # =========================================================================
    app.include_router(admin_router, prefix="/v1", tags=["Admin"])
    app.include_router(admin_crawler_router, prefix="/v1", tags=["Admin Crawler"])
    app.include_router(agents_router, prefix="/v1", tags=["Agents"])
    app.include_router(chat_router, prefix="/v1", tags=["Chat"])
    app.include_router(crawler_router, prefix="/v1", tags=["Crawler"])
    app.include_router(health_router, tags=["Monitoring"])
    app.include_router(rag_router, prefix="/v1", tags=["RAG"])
    app.include_router(search_curated_router, prefix="/v1", tags=["Search Curated"])
    app.include_router(mcp_public_router, prefix="/v1", tags=["MCP"])
    app.include_router(mcp_router, prefix="/v1", tags=["MCP"])
    app.include_router(mcp_node_router, prefix="/v1", tags=["MCP Node"])
    app.include_router(mcp_remote_router, tags=["MCP Remote Server"])
    app.include_router(oauth_router, tags=["OAuth 2.0"])
    app.include_router(models_router, prefix="/v1", tags=["Models"])
    app.include_router(openai_compat_router, prefix="/v1/openai", tags=["OpenAI Compatibility"])
    app.include_router(orchestration_router, prefix="/v1", tags=["Orchestration"])
    app.include_router(posts_router, prefix="/v1", tags=["Posts"])
    app.include_router(settings_router, prefix="/v1", tags=["Settings"])
    app.include_router(vision_router_v1, prefix="/v1", tags=["Vision V1"])
    app.include_router(text_router, prefix="/v1", tags=["Text Analysis"])
    app.include_router(triforce_router, prefix="/v1", tags=["TriForce"])
    app.include_router(tristar_router, prefix="/v1", tags=["TriStar"])
    app.include_router(tristar_settings_router, prefix="/v1", tags=["TriStar Settings"])
    app.include_router(mesh_router, prefix="/v1", tags=["Mesh AI"])
    app.include_router(distributed_compute_router, tags=["Distributed Compute"])
    app.include_router(tristar_gui_router)

    # =========================================================================
    # Translation Layer - Alternative Path Aliases
    # =========================================================================
    # Note: tristar_router and triforce_router have internal prefixes
    # (/tristar and /triforce), so we register them without additional prefix
    # for the root aliases, and with empty prefix for cross-links

    # Legacy /mcp aliases removed - use /v1/mcp instead
    # Standard MCP endpoint: /v1/mcp (registered above in line 256)

    # TriStar Aliases: Register at root (router has prefix="/tristar")
    # This gives us: /tristar/..., /v1/tristar/... (primary)
    # REDUNDANT: app.include_router(tristar_router, prefix="", tags=["TriStar Root"])  # Disabled 2025-12-26

    # TriForce Aliases: Register at root (router has prefix="/triforce")
    # This gives us: /triforce/..., /v1/triforce/... (primary)
    # REDUNDANT: app.include_router(triforce_router, prefix="", tags=["TriForce Root"])  # Disabled 2025-12-26

    # MCP under TriStar/TriForce namespaces
    # REDUNDANT: app.include_router(mcp_router, prefix="/tristar/mcp", tags=["TriStar MCP"])  # Disabled 2025-12-26
    # REDUNDANT: app.include_router(mcp_router, prefix="/triforce/mcp", tags=["TriForce MCP"])  # Disabled 2025-12-26

    # Mesh AI Routes (additional aliases)
    # REDUNDANT: app.include_router(mesh_router, prefix="", tags=["Mesh Root"])  # Disabled 2025-12-26  # /mesh/...
    # REDUNDANT: app.include_router(mesh_router, prefix="/triforce", tags=["TriForce Mesh"])  # Disabled 2025-12-26  # /triforce/mesh/...
    app.include_router(perf_monitor_router, tags=["Performance Monitor"])

    # Legacy /mcp namespace aliases removed - use /v1/tristar and /v1/triforce instead

    # Include routers from the top-level app directory (prefixes are in the files)
    app.include_router(sd3_router, prefix="/v1", tags=["Stable Diffusion 3"])
    app.include_router(vision_router, tags=["Vision"])
    app.include_router(client_chat_router, prefix="/v1", tags=["Client Chat"])
    app.include_router(client_auth_router, prefix="/v1", tags=["Client Auth"])
    app.include_router(client_update_router, prefix="/v1", tags=["Client Update"])
    app.include_router(client_logs_router, tags=["Client Logs"])
    app.include_router(client_ocr_router, prefix="/v1", tags=["Client OCR"])
    app.include_router(federation_router, prefix="/v1", tags=["Federation"])
    app.include_router(nova_chat_agent_router, prefix="/v1", tags=["Nova Chat Agent"])
    app.include_router(mistral_agents_router, prefix="/v1", tags=["Mistral Agents"])
    app.include_router(remote_coding_agent_router, prefix="/v1", tags=["Remote Coding Preview"])
    app.include_router(shared_notify_router, prefix="/v1", tags=["Shared Notify"])
    app.include_router(nova_frontend_public_router, prefix="/v1", tags=["Nova Frontend Public"])
    app.include_router(nova_frontend_router, prefix="/v1", tags=["Nova Frontend"])
    app.include_router(nova_playground_router, prefix="/v1", tags=["Nova Playground"])
    app.include_router(nova_wordpress_router, tags=["Nova WordPress"])
    app.include_router(nova_operator_router, prefix="/v1", tags=["Nova Operator"])

    @app.middleware("http")
    async def public_discovery_options_middleware(request: Request, call_next):
        public_discovery_paths = {
            "/v1/.well-known/mcp",
            "/v1/.well-known/oauth-authorization-server",
        }
        if request.method == "OPTIONS":
            if request.url.path in public_discovery_paths:
                return Response(status_code=204)
        return await call_next(request)

    # Prometheus Metrics Setup
    if _HAS_INSTRUMENTATOR:
        Instrumentator().instrument(app).expose(app, include_in_schema=False)

    # Custom AILinux Metrics Middleware (LLM calls, circuit breaker, memory, etc.)
    if _HAS_CUSTOM_METRICS:
        @app.middleware("http")
        async def metrics_middleware(request: Request, call_next):
            middleware = MetricsMiddleware()
            return await middleware(request, call_next)

    # Performance Monitor Middleware (Endpoint-Latenz-Tracking)
    @app.middleware("http")
    async def perf_middleware_handler(request: Request, call_next):
        return await perf_middleware(request, call_next)

    # TriForce Central Logging Middleware - logs ALL API traffic
    if _HAS_TRIFORCE_LOGGING:
        app.add_middleware(TriForceLoggingMiddleware, central_logger=central_logger)

    static_dir = Path(__file__).parent / "static"
    docs_static_dir = static_dir / "docs"

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui():
        swagger_css = (docs_static_dir / "swagger-ui.css").read_text(encoding="utf-8")
        swagger_js = (docs_static_dir / "swagger-ui-bundle.js").read_text(encoding="utf-8")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{app.title} - Swagger UI</title><style>{swagger_css}</style>"
            "</head><body><div id='swagger-ui'></div>"
            f"<script>{swagger_js}</script>"
            "<script>window.ui=SwaggerUIBundle({"
            "url:'/openapi.json',dom_id:'#swagger-ui',layout:'BaseLayout',deepLinking:true,"
            "showExtensions:true,showCommonExtensions:true,"
            "oauth2RedirectUrl:window.location.origin+'/docs/oauth2-redirect',"
            "presets:[SwaggerUIBundle.presets.apis,SwaggerUIBundle.SwaggerUIStandalonePreset]"
            "});</script></body></html>"
        )
        return HTMLResponse(html)

    @app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
    async def swagger_ui_redirect():
        return get_swagger_ui_oauth2_redirect_html()

    @app.get("/redoc", include_in_schema=False)
    async def custom_redoc():
        redoc_js = (docs_static_dir / "redoc.standalone.js").read_text(encoding="utf-8")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{app.title} - ReDoc</title>"
            "<style>body{margin:0;padding:0}</style></head><body><div id='redoc'></div>"
            f"<script>{redoc_js}</script>"
            "<script>Redoc.init('/openapi.json',{},document.getElementById('redoc'));</script>"
            "</body></html>"
        )
        return HTMLResponse(html)

    # Existing GUI/static assets remain available on the normal path.
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # =========================================================================
    # Session-based Authentication for TriStar GUI
    # Moved to app/routes/tristar_gui.py
    # =========================================================================

    return app

# Uvicorn Entry
app = create_app()

app.include_router(admin_users_router, prefix="/v1", tags=["Admin Users"])
