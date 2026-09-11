"""Authenticated AILinux Shared Notify / Presence API."""
from __future__ import annotations

from typing import Any, Optional
import uuid

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from .client_auth import build_verified_session, decode_authorization_header
from ..services.shared_notify import get_shared_notify_store

router = APIRouter(prefix="/notify-network", tags=["Shared Notify"])


def _session(authorization: Optional[str]) -> dict[str, Any]:
    payload = decode_authorization_header(authorization)
    session = build_verified_session(payload)
    owner = str(session.get("user_id") or session.get("email") or "").strip().lower()
    if not owner:
        raise HTTPException(401, "Authenticated AILinux user identity required")
    return {**session, "owner_id": owner}


def _fail(exc: Exception) -> None:
    if isinstance(exc, PermissionError):
        raise HTTPException(403, str(exc)) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(400, str(exc)) from exc
    raise exc


class EndpointRegister(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    handle: str = Field(min_length=1, max_length=64)
    endpoint_id: str = Field(default="", max_length=96)
    kind: str = Field(default="client", max_length=32)
    label: str = Field(default="", max_length=120)
    capabilities: list[str] = Field(default_factory=list, max_length=32)
    visibility: str = Field(default="account", max_length=16)
    transport: str = Field(default="mailbox", max_length=32)
    target: str = Field(default="", max_length=256)
    ttl_seconds: int = Field(default=120, ge=30, le=3600)


class PresenceUpdate(BaseModel):
    endpoint_id: str
    availability: Optional[str] = None
    activity: Optional[str] = None
    status_text: Optional[str] = Field(default=None, max_length=240)
    accept_human_chat: Optional[bool] = None
    accept_ai_chat: Optional[bool] = None
    accept_tasks: Optional[bool] = None
    current_task_id: Optional[str] = Field(default=None, max_length=128)
    task_started_at: Optional[int] = None
    eta_seconds: Optional[int] = Field(default=None, ge=0, le=86400)
    ttl_seconds: Optional[int] = Field(default=None, ge=30, le=3600)


class HandleRename(BaseModel):
    endpoint_id: str
    handle: str = Field(min_length=1, max_length=64)


class SharedMessage(BaseModel):
    target: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="human_chat", max_length=32)
    title: str = Field(default="", max_length=300)
    body: str = Field(default="", max_length=10000)
    sender_endpoint_id: str = Field(default="", max_length=96)
    thread_id: str = Field(default="", max_length=96)
    correlation_id: str = Field(default="", max_length=128)
    message_id: str = Field(default="", max_length=96)
    hop_count: int = Field(default=0, ge=0, le=32)
    ttl_seconds: int = Field(default=86400, ge=30, le=604800)
    metadata: dict[str, Any] = Field(default_factory=dict)
    dedup_key: str = Field(default="", max_length=256)


class AckRequest(BaseModel):
    endpoint_id: str
    message_id: str


class MemoryRecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


@router.post("/endpoints")
async def register_endpoint(body: EndpointRegister, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        endpoint = get_shared_notify_store().register_endpoint(
            owner_id=session["owner_id"], device_id=body.device_id,
            requested_handle=body.handle, endpoint_id=body.endpoint_id,
            kind=body.kind, label=body.label, capabilities=body.capabilities,
            visibility=body.visibility, transport=body.transport, target=body.target,
            ttl_seconds=body.ttl_seconds,
        )
        return {"ok": True, "endpoint": endpoint}
    except Exception as exc:
        _fail(exc)


@router.post("/presence")
async def set_presence(body: PresenceUpdate, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        fields = body.model_dump(exclude_none=True)
        endpoint_id = fields.pop("endpoint_id")
        endpoint = get_shared_notify_store().set_presence(
            owner_id=session["owner_id"], endpoint_id=endpoint_id, **fields
        )
        return {"ok": True, "endpoint": endpoint}
    except Exception as exc:
        _fail(exc)


@router.post("/heartbeat")
async def heartbeat(body: PresenceUpdate, authorization: str = Header(None)):
    return await set_presence(body, authorization)


@router.post("/handles/rename")
async def rename_handle(body: HandleRename, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        endpoint = get_shared_notify_store().rename_handle(
            owner_id=session["owner_id"], endpoint_id=body.endpoint_id, requested_handle=body.handle
        )
        return {"ok": True, "endpoint": endpoint}
    except Exception as exc:
        _fail(exc)


@router.get("/directory")
async def directory(include_offline: bool = True, authorization: str = Header(None)):
    session = _session(authorization)
    return {"ok": True, "endpoints": get_shared_notify_store().directory(
        owner_id=session["owner_id"], include_offline=include_offline
    )}


async def _record_shared_ai_message(*, session: dict[str, Any], body: SharedMessage, message: dict[str, Any]) -> None:
    # Human chat is deliberately not auto-recorded. Episodic AI collaboration is
    # historical context only and remains disabled unless the operator enables
    # the existing Memory Fabric recording policy.
    if body.kind not in {"ai_optimization", "coordination", "handoff", "review", "task"}:
        return
    try:
        from ..services.memory_trigger import MemoryEvent, get_memory_engine
        engine = get_memory_engine()
        if not engine.settings.episodic_memory_enabled or not engine.settings.memory_record_enabled:
            return
        project = engine.settings.memory_project_id or "shared-notify"
        compact = (str(body.title or "") + "\n" + str(body.body or ""))[:3500]
        event = MemoryEvent(
            event_type="shared_ai_message",
            project_id=f"{project}:account:{session['owner_id']}",
            run_id=str(message.get("message_id") or ""),
            session_id=str(message.get("thread_id") or ""),
            agent=str(body.sender_endpoint_id or "shared-notify"),
            task=compact,
            component="shared-notify",
            source="shared-notify",
            verification_state="observation",
            confidence=0.0,
        )
        await engine.record_event(event)
    except Exception:
        # Memory is optional/fail-open: messaging must never fail because history is unavailable.
        return


@router.post("/send")
async def send_message(body: SharedMessage, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        message, target = get_shared_notify_store().send(
            sender_owner_id=session["owner_id"], target_handle=body.target, kind=body.kind,
            title=body.title, body=body.body, sender_endpoint_id=body.sender_endpoint_id,
            thread_id=body.thread_id, correlation_id=body.correlation_id,
            message_id=body.message_id, hop_count=body.hop_count,
            ttl_seconds=body.ttl_seconds, metadata=body.metadata, dedup_key=body.dedup_key,
        )
        delivery: dict[str, Any] = {"mode": "mailbox", "target": target.handle}
        # Local model/agent endpoints can be invoked immediately through the existing
        # side-effect-free notify RPC; the durable mailbox remains the audit trail.
        if target.online and target.transport == "local-ai" and target.target:
            from ..mcp.notification_manager import _invoke_notify_target
            result = await _invoke_notify_target(
                target.target, title=body.title or f"Shared Notify {body.kind}", body=body.body,
                timeout=min(max(int(body.metadata.get("timeout", 120) or 120), 10), 300),
            )
            delivery = {"mode": "local-ai", "target": target.handle, "result": result}
            if result.get("invoked"):
                message = get_shared_notify_store().mark_local_delivery(
                    message_id=message["message_id"],
                    acknowledged=str((result.get("result") or {}).get("status") or "") == "success",
                )
        await _record_shared_ai_message(session=session, body=body, message=message)
        return {"ok": True, "message": message, "delivery": delivery}
    except Exception as exc:
        _fail(exc)


@router.get("/inbox/{endpoint_id}")
async def inbox(endpoint_id: str, limit: int = 50, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        return {"ok": True, "messages": get_shared_notify_store().inbox(
            owner_id=session["owner_id"], endpoint_id=endpoint_id, limit=limit, mark_delivered=True
        )}
    except Exception as exc:
        _fail(exc)


@router.post("/ack")
async def ack(body: AckRequest, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        return {"ok": True, "message": get_shared_notify_store().ack(
            owner_id=session["owner_id"], endpoint_id=body.endpoint_id, message_id=body.message_id
        )}
    except Exception as exc:
        _fail(exc)


@router.delete("/endpoints/{endpoint_id}")
async def disable_endpoint(endpoint_id: str, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        if not get_shared_notify_store().disable_endpoint(owner_id=session["owner_id"], endpoint_id=endpoint_id):
            raise LookupError("endpoint not found")
        return {"ok": True, "endpoint_id": endpoint_id, "disabled": True}
    except Exception as exc:
        _fail(exc)


@router.post("/memory/recall")
async def memory_recall(body: MemoryRecallRequest, authorization: str = Header(None)):
    session = _session(authorization)
    try:
        from ..services.memory_trigger import MemoryEvent, get_memory_engine
        engine = get_memory_engine()
        if not engine.settings.episodic_memory_enabled:
            return {"ok": True, "status": "disabled", "context": "", "ids": []}
        base_project = engine.settings.memory_project_id or "triforce"
        project = f"{base_project}:account:{session['owner_id']}"
        result = await engine.recall(
            MemoryEvent(
                event_type="task_started", project_id=project,
                run_id="shared-recall-" + uuid.uuid4().hex,
                task=body.query, source="aicoder-shared-notify",
                verification_state="observation", confidence=0.0,
            ),
            manual=True,
        )
        return {
            "ok": True, "status": result.get("status", "ok"),
            "context": str(result.get("context") or ""),
            "ids": list(result.get("ids") or []),
            "latency_ms": result.get("latency_ms"),
        }
    except Exception:
        # Memory is optional and recall must never block normal client operation.
        return {"ok": True, "status": "degraded", "context": "", "ids": []}


@router.get("/status")
async def status(authorization: str = Header(None)):
    session = _session(authorization)
    return {"ok": True, **get_shared_notify_store().stats(owner_id=session["owner_id"])}
