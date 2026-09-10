"""
Distributed Compute Routes
==========================

WebSocket und REST-Endpoints für verteiltes Computing.
Ermöglicht Clients, ihre Rechenleistung dem Netzwerk zur Verfügung zu stellen.
"""

import logging
from typing import Any, List

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..services.distributed_compute import (
    get_distributed_compute,
    TaskPriority,
)
from ..services.community_federation_policy import (
    CommunityPolicyError,
    community_federation_policy,
)
from .client_auth import decode_authorization_header, build_verified_session

router = APIRouter(prefix="/v1/distributed", tags=["Distributed Compute"])
logger = logging.getLogger("ailinux.distributed_compute")


# === Pydantic Models ===

class WorkerRegistration(BaseModel):
    """Worker-Registrierung"""
    capability: str = "WEBGPU"
    gpu_vendor: str = ""
    gpu_name: str = ""
    estimated_tflops: float = 0.0
    supported_models: List[str] = []


class TaskSubmission(BaseModel):
    """Task-Einreichung"""
    task_type: str
    input_data: Any
    model_id: str
    priority: str = "normal"
    timeout_seconds: float = 60.0
    allow_community: bool = False


class BatchTaskSubmission(BaseModel):
    """Batch-Task-Einreichung"""
    task_type: str
    input_items: List[Any]
    model_id: str
    batch_size: int = 10
    priority: str = "normal"
    allow_community: bool = False


# === WebSocket Endpoint ===

@router.websocket("/worker")
async def worker_websocket(
    websocket: WebSocket,
    capability: str = Query("WEBGPU"),
    gpu_name: str = Query(""),
    tflops: float = Query(0.0),
):
    """
    WebSocket für Compute Workers.

    Clients verbinden sich hier, um Tasks zu empfangen und Ergebnisse zu melden.

    Query Parameters:
    - capability: WEBGPU, WEBGL2, WEBGL, WASM_SIMD, WASM, JS_ONLY
    - gpu_name: Name der GPU (z.B. "NVIDIA RTX 3080")
    - tflops: Geschätzte TFLOPS

    Protocol:
    1. Client verbindet sich mit GPU-Info
    2. Server sendet `worker_registered` mit Session-ID
    3. Server sendet `task_assignment` wenn Task verfügbar
    4. Client sendet `task_result` nach Bearbeitung
    5. Client sendet `heartbeat` alle 30 Sekunden
    """
    await websocket.accept()

    manager = get_distributed_compute()
    session_id = None

    try:
        community_federation_policy.ensure_enabled()

        # Authentication is carried in the first JSON message instead of the URL
        # so bearer tokens do not end up in proxy/access logs.
        init_msg = await websocket.receive_json()
        if init_msg.get("type") != "register":
            await websocket.close(code=4001, reason="Expected register message")
            return

        auth = init_msg.get("authorization") or ""
        payload = decode_authorization_header(auth)
        session = build_verified_session(payload)
        tier = community_federation_policy.validate_tier(session.get("tier", "guest"))
        user_id = str(session.get("user_id") or session.get("client_id") or "")
        if not user_id:
            raise CommunityPolicyError("Authenticated worker has no stable user identity")

        supported_models = init_msg.get("supported_models", [
            "embedding_small",
            "sentiment",
            "clip_embed",
        ])
        registration = community_federation_policy.validate_registration(
            capability=capability,
            gpu_name=gpu_name,
            estimated_tflops=tflops,
            supported_models=supported_models,
        )
        metadata_audit = await community_federation_policy.audit_registration_metadata(
            user_id=user_id,
            tier=tier,
            registration=registration,
        )

        # Session IDs are server-issued. A client-provided ID is never trusted.
        import secrets
        requested_session_id = secrets.token_urlsafe(24)

        client = await manager.register_client(
            websocket=websocket,
            session_id=requested_session_id,
            capability=registration.capability,
            gpu_name=registration.gpu_name,
            estimated_tflops=registration.estimated_tflops,
            supported_models=registration.supported_models,
            user_id=user_id,
            tier=tier,
            trust_level="community",
            metadata_audit=metadata_audit,
        )
        session_id = client.session_id

        # Bestätigung senden
        await websocket.send_json({
            "type": "worker_registered",
            "session_id": session_id,
            "supported_models": registration.supported_models,
            "trust_level": "community",
            "results_verified_by_default": False,
            "metadata_audit": metadata_audit,
            "message": "Successfully registered as authenticated community compute worker",
        })

        logger.info(f"Worker connected: {session_id} ({capability})")

        # Message Loop
        while True:
            try:
                message = await websocket.receive_json()
                msg_type = community_federation_policy.validate_worker_message(message)

                if msg_type == "heartbeat":
                    await manager.client_heartbeat(session_id)
                    await websocket.send_json({"type": "heartbeat_ack"})

                elif msg_type == "task_result":
                    task_id = str(message.get("task_id") or "")
                    if not task_id:
                        raise CommunityPolicyError("task_result requires task_id")
                    compute_time = message.get("compute_time", 0.0)
                    if not isinstance(compute_time, (int, float)) or compute_time < 0 or compute_time > 86400:
                        raise CommunityPolicyError("Invalid compute_time")
                    await manager.report_task_result(
                        session_id=session_id,
                        task_id=task_id,
                        success=bool(message.get("success", False)),
                        result=message.get("result"),
                        error=str(message.get("error") or "")[:1000] or None,
                        compute_time=float(compute_time),
                    )

                elif msg_type == "task_progress":
                    # Progress-Update vom Client - Task Status aktualisieren
                    task_id = message.get("task_id")
                    progress = message.get("progress", 0)  # 0-100
                    if not isinstance(progress, (int, float)) or progress < 0 or progress > 100:
                        raise CommunityPolicyError("Invalid task progress")
                    if task_id and session_id in manager._clients:
                        task = manager._task_queue.get(task_id)
                        if task and task.assigned_to == session_id:
                            # Update task status to PROCESSING when progress is received
                            from ..services.distributed_compute import TaskStatus
                            task.status = TaskStatus.PROCESSING
                            # Log progress for monitoring
                            logger.debug(f"Task {task_id} progress: {progress}%")

                elif msg_type == "capability_update":
                    if session_id in manager._clients:
                        current = manager._clients[session_id]
                        updated = community_federation_policy.validate_registration(
                            capability=current.capability,
                            gpu_name=current.gpu_name,
                            estimated_tflops=current.estimated_tflops,
                            supported_models=message.get("supported_models", []),
                        )
                        current.supported_models = updated.supported_models

                elif msg_type == "disconnect":
                    break

            except CommunityPolicyError as e:
                logger.warning(f"Community worker policy violation: {e}")
                await websocket.close(code=4003, reason="Community worker policy violation")
                break
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.error(f"Worker message error: {e}")
                await websocket.close(code=1011, reason="Worker message handling failed")
                break

    except WebSocketDisconnect:
        logger.info(f"Worker disconnected: {session_id}")
    except (CommunityPolicyError, HTTPException) as e:
        logger.warning(f"Community worker rejected: {e}")
        try:
            await websocket.close(code=4003, reason="Community worker authorization/policy rejected")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Worker error: {e}")
    finally:
        if session_id:
            await manager.unregister_client(session_id)


def _require_community_task_owner(task_obj, authorization: str) -> None:
    if not task_obj or not getattr(task_obj, "allow_community", False):
        return
    payload = decode_authorization_header(authorization)
    session = build_verified_session(payload)
    requester = str(session.get("user_id") or session.get("client_id") or "")
    if not requester or requester != getattr(task_obj, "owner_id", None):
        raise HTTPException(status_code=403, detail="Community task belongs to another account")


# === REST Endpoints ===

@router.post("/submit")
async def submit_task(task: TaskSubmission, authorization: str = Header(None)):
    """
    Reicht einen Task zur verteilten Berechnung ein.

    Der Task wird an einen verfügbaren Worker zugewiesen.
    """
    manager = get_distributed_compute()
    owner_id = None
    if task.allow_community:
        community_federation_policy.ensure_enabled()
        payload = decode_authorization_header(authorization)
        session = build_verified_session(payload)
        community_federation_policy.validate_tier(session.get("tier", "guest"))
        owner_id = str(session.get("user_id") or session.get("client_id") or "")
        community_federation_policy.validate_community_task(task.task_type, task.input_data, task.model_id)

    priority_map = {
        "low": TaskPriority.LOW,
        "normal": TaskPriority.NORMAL,
        "high": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT,
    }

    task_id = await manager.submit_task(
        task_type=task.task_type,
        input_data=task.input_data,
        model_id=task.model_id,
        priority=priority_map.get(task.priority.lower(), TaskPriority.NORMAL),
        timeout_seconds=task.timeout_seconds,
        allow_community=task.allow_community,
        owner_id=owner_id,
    )

    return JSONResponse(content={
        "task_id": task_id,
        "status": "pending",
        "message": "Task submitted for distributed processing",
    })


@router.post("/submit/batch")
async def submit_batch_task(batch: BatchTaskSubmission, authorization: str = Header(None)):
    """
    Reicht mehrere Items als Batch-Tasks ein.

    Die Items werden in Batches aufgeteilt und parallel verarbeitet.
    """
    manager = get_distributed_compute()
    owner_id = None
    if batch.allow_community:
        community_federation_policy.ensure_enabled()
        payload = decode_authorization_header(authorization)
        session = build_verified_session(payload)
        community_federation_policy.validate_tier(session.get("tier", "guest"))
        owner_id = str(session.get("user_id") or session.get("client_id") or "")
        community_federation_policy.validate_community_task(batch.task_type + "_batch", batch.input_items, batch.model_id)

    priority_map = {
        "low": TaskPriority.LOW,
        "normal": TaskPriority.NORMAL,
        "high": TaskPriority.HIGH,
        "urgent": TaskPriority.URGENT,
    }

    task_ids = await manager.submit_batch_task(
        task_type=batch.task_type,
        input_items=batch.input_items,
        model_id=batch.model_id,
        batch_size=batch.batch_size,
        priority=priority_map.get(batch.priority.lower(), TaskPriority.NORMAL),
        allow_community=batch.allow_community,
        owner_id=owner_id,
    )

    return JSONResponse(content={
        "task_ids": task_ids,
        "batch_count": len(task_ids),
        "total_items": len(batch.input_items),
        "message": "Batch submitted for distributed processing",
    })


@router.get("/task/{task_id}")
async def get_task_status(task_id: str, authorization: str = Header(None)):
    """Gibt Status eines Tasks zurück."""
    manager = get_distributed_compute()
    task_obj = manager._task_queue.get(task_id)
    _require_community_task_owner(task_obj, authorization)
    status = await manager.get_task_status(task_id)

    if not status:
        raise HTTPException(status_code=404, detail="Task not found")

    return JSONResponse(content=status)


@router.get("/task/{task_id}/result")
async def get_task_result(
    task_id: str,
    wait: bool = Query(True, description="Warten bis Ergebnis verfügbar"),
    timeout: float = Query(30.0, description="Max. Wartezeit in Sekunden"),
    authorization: str = Header(None),
):
    """
    Holt das Ergebnis eines Tasks.

    Mit `wait=true` blockiert der Request bis das Ergebnis verfügbar ist.
    """
    manager = get_distributed_compute()
    task_obj = manager._task_queue.get(task_id)
    _require_community_task_owner(task_obj, authorization)

    try:
        result = await manager.get_task_result(task_id, wait=wait, timeout=timeout)

        if result is None:
            return JSONResponse(content={
                "task_id": task_id,
                "status": "pending",
                "result": None,
            })

        task_obj = manager._task_queue.get(task_id)
        return JSONResponse(content={
            "task_id": task_id,
            "status": "completed",
            "result": result,
            "result_verified": bool(getattr(task_obj, "result_verified", False)),
            "result_source_trust": getattr(task_obj, "result_source_trust", "unknown"),
        })

    except TimeoutError:
        return JSONResponse(
            content={"error": "Timeout waiting for result"},
            status_code=408,
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e)},
            status_code=500,
        )


@router.delete("/task/{task_id}")
async def cancel_task(task_id: str, authorization: str = Header(None)):
    """Bricht einen Task ab."""
    manager = get_distributed_compute()
    task_obj = manager._task_queue.get(task_id)
    _require_community_task_owner(task_obj, authorization)
    success = await manager.cancel_task(task_id)

    if not success:
        raise HTTPException(status_code=404, detail="Task not found or already completed")

    return JSONResponse(content={
        "task_id": task_id,
        "status": "cancelled",
    })


@router.get("/stats")
async def get_stats():
    """
    Gibt Statistiken über das Distributed Compute Network zurück.

    Inkludiert:
    - Queue-Status
    - Verbundene Workers
    - Gesamt-Statistiken
    - Top Contributors
    """
    manager = get_distributed_compute()
    return JSONResponse(content=manager.get_stats())


@router.get("/workers")
async def get_workers():
    """Liste aller verbundenen Workers."""
    manager = get_distributed_compute()

    workers = []
    for client in manager._clients.values():
        workers.append({
            "session_id": client.session_id[:8] + "...",
            "capability": client.capability,
            "gpu_name": client.gpu_name,
            "estimated_tflops": client.estimated_tflops,
            "is_available": client.is_available,
            "tier": client.tier,
            "trust_level": client.trust_level,
            "audit_risk": client.metadata_audit.get("risk", "NOT_RUN"),
            "tasks_completed": client.tasks_completed,
            "credits_earned": round(client.credits_earned, 2),
        })

    return JSONResponse(content={
        "workers": workers,
        "total": len(workers),
        "available": sum(1 for w in workers if w["is_available"]),
    })


@router.get("/credits/{session_id}")
async def get_client_credits(session_id: str):
    """
    Gibt Credits eines Workers zurück.

    Credits werden für erfolgreich abgeschlossene Tasks vergeben.
    """
    manager = get_distributed_compute()
    credits = manager.get_client_credits(session_id)

    if not credits:
        raise HTTPException(status_code=404, detail="Worker not found")

    return JSONResponse(content=credits)


@router.get("/community/policy")
async def community_policy_status():
    return JSONResponse(content=community_federation_policy.settings_snapshot())


# === Convenience Endpoints ===

@router.post("/embed")
async def distributed_embed(
    texts: List[str],
    model_id: str = "embedding_small",
    priority: str = "normal",
):
    """
    Convenience-Endpoint für verteilte Embedding-Berechnung.

    Verteilt die Texte auf verfügbare Workers.
    """
    manager = get_distributed_compute()

    if len(texts) <= 10:
        # Kleine Batches als einzelnen Task
        task_id = await manager.submit_task(
            task_type="embedding",
            input_data=texts,
            model_id=model_id,
            priority=TaskPriority.NORMAL,
        )
        return JSONResponse(content={"task_id": task_id, "batch_count": 1})
    else:
        # Größere Mengen als Batch
        task_ids = await manager.submit_batch_task(
            task_type="embedding",
            input_items=texts,
            model_id=model_id,
            batch_size=10,
        )
        return JSONResponse(content={"task_ids": task_ids, "batch_count": len(task_ids)})


@router.post("/sentiment")
async def distributed_sentiment(
    texts: List[str],
    model_id: str = "sentiment",
    priority: str = "normal",
):
    """Convenience-Endpoint für verteilte Sentiment-Analyse."""
    manager = get_distributed_compute()

    task_ids = await manager.submit_batch_task(
        task_type="sentiment",
        input_items=texts,
        model_id=model_id,
        batch_size=20,
    )

    return JSONResponse(content={"task_ids": task_ids, "batch_count": len(task_ids)})
