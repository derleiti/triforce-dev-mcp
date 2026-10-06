from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import hashlib
import logging

from fastapi import Request

_STORE = Path(os.environ.get("TRIFORCE_WEB_WORKER_STORE", "/var/lib/triforce/web_worker_jobs.json"))
_LOCK = threading.RLock()
_MAX_TASK = 100_000
_MAX_RESULT = 100_000
_logger = logging.getLogger("mcp.web_worker")


def _short_hash(value: Any) -> str:
    raw = str(value or "").encode("utf-8", errors="ignore")
    return hashlib.sha256(raw).hexdigest()[:12] if raw else ""


def _identity_fingerprint(request: Optional[Request]) -> Dict[str, str]:
    if request is None:
        return {}
    state = getattr(request, "state", None)
    out: Dict[str, str] = {}
    for name in (
        "mcp_session_id", "mcp_auth_user", "mcp_auth_method", "mcp_auth_client_id",
        "mcp_workspace_subject", "mcp_authority_role", "mcp_authority_source",
    ):
        value = getattr(state, name, None) if state is not None else None
        if value not in (None, ""):
            out[name] = _short_hash(value)
    try:
        for name in (
            "mcp-session-id", "x-request-id", "x-openai-request-id", "x-chatgpt-conversation-id",
            "x-conversation-id", "user-agent", "cf-connecting-ip",
        ):
            value = request.headers.get(name)
            if value:
                out[f"hdr:{name}"] = _short_hash(value)
    except Exception:
        pass
    return out


TICKET_ALLOWED_TOOLS = frozenset({
    "web_worker",
    "chat", "models", "specialist",
    "search", "crawl", "current_time",
    "code_read", "code_search", "code_tree", "code_grep",
    "file_read", "file_tree",
    "status", "safe_probe", "log_viewer", "service_status", "container_status",
    "mail_inbox", "mail_read", "mail_send", "mail_mark_seen",
    "flarum_discussions", "flarum_discussion_get", "flarum_post_get",
    "flarum_post_create", "flarum_posts", "flarum_tags",
    "notify_list", "notify_read", "notify_send",
    "memory_search", "memory_history",
})

# Untrusted marketplace text gets an even narrower sandbox than support tickets.
# No account integrations, no local/server inspection, no Git/GitHub, no mail,
# no filesystem writes, and no payment or bidding actions are exposed here.
MARKET_ALLOWED_TOOLS = frozenset({
    "web_worker",
    "chat", "models", "specialist",
    "search", "crawl", "current_time",
})


WEB_WORKER_TOOLS = [{
    "name": "web_worker",
    "description": (
        "Persistent autonomous web-worker job handoff. Internal callers create/cancel jobs; "
        "a fresh authenticated web chat claims a job by ID, receives its task, and reports "
        "completion. Ticket jobs are bound to a fail-closed read/support-only MCP policy for "
        "the claiming session; internal jobs retain the caller's normal MCP permissions."
    ),
    "inputSchema": {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "claim", "status", "complete", "fail", "cancel"],
            },
            "job_id": {"type": "string"},
            "mode": {"type": "string", "enum": ["internal", "ticket", "market"], "default": "internal"},
            "task": {"type": "string"},
            "result": {"type": "string"},
            "error": {"type": "string"},
            "metadata": {"type": "object"},
        },
    },
    "annotations": {
        "title": "Web Worker",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "x_inventory": "agents",
}]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        try:
            raw = json.loads(_STORE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)}
        except FileNotFoundError:
            pass
        except Exception:
            pass
        return {}


def _save(jobs: Dict[str, Dict[str, Any]]) -> None:
    with _LOCK:
        _STORE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE.with_suffix(".tmp")
        tmp.write_text(json.dumps(jobs, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, _STORE)
        os.chmod(_STORE, 0o600)


def _session_id(request: Optional[Request]) -> str:
    """Stable worker identity across transport churn.

    OpenAI MCP may use a fresh Mcp-Session-Id for individual tool calls while
    preserving x-openai-session + x-openai-subject for the logical web context.
    Worker ownership therefore prefers that trusted logical identity and hashes
    it so raw connector identifiers are never persisted. Other clients keep
    their normal MCP transport session id.
    """
    if request is None:
        return ""
    try:
        headers = request.headers
        openai_session = str(headers.get("x-openai-session") or "").strip()
        openai_subject = str(headers.get("x-openai-subject") or "").strip()
        user_agent = str(headers.get("user-agent") or "").lower()
        if openai_session and openai_subject and "openai-mcp" in user_agent:
            import hashlib
            digest = hashlib.sha256((openai_subject + "\\0" + openai_session).encode("utf-8")).hexdigest()
            return "openai-worker-" + digest[:40]
    except Exception:
        pass
    state = getattr(request, "state", None)
    session_id = str(getattr(state, "mcp_session_id", "") or "").strip()
    if session_id:
        return session_id
    try:
        return str(
            request.headers.get("Mcp-Session-Id")
            or request.headers.get("mcp-session-id")
            or ""
        ).strip()
    except Exception:
        return ""


def _is_internal(request: Optional[Request]) -> bool:
    if request is None:
        return False
    try:
        from app.utils.mcp_security import is_internal_full_request
        return bool(is_internal_full_request(request))
    except Exception:
        return False


def _find_active_by_session(session_id: str) -> tuple[str, Optional[Dict[str, Any]]]:
    if not session_id:
        return "", None
    jobs = _load()
    for job_id, job in jobs.items():
        if (
            str(job.get("claimed_session") or "") == session_id
            and str(job.get("state") or "") == "claimed"
        ):
            return job_id, job
    return "", None


def worker_mode_for_request(request: Optional[Request]) -> str:
    _, job = _find_active_by_session(_session_id(request))
    return str((job or {}).get("mode") or "")


def restricted_worker_tool_allowed(mode: str, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
    name = str(tool_name or "")
    if name.startswith("triforce_"):
        name = name[9:]
    if mode == "ticket":
        return name in TICKET_ALLOWED_TOOLS
    if mode == "market":
        return name in MARKET_ALLOWED_TOOLS
    return True


def ticket_worker_tool_allowed(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> bool:
    return restricted_worker_tool_allowed("ticket", tool_name, arguments)


def filter_restricted_worker_tools(tools: list[Dict[str, Any]], request: Optional[Request]) -> list[Dict[str, Any]]:
    mode = worker_mode_for_request(request)
    if mode not in {"ticket", "market"}:
        return tools
    return [tool for tool in tools if restricted_worker_tool_allowed(mode, str(tool.get("name") or ""))]


def filter_ticket_worker_tools(tools: list[Dict[str, Any]], request: Optional[Request]) -> list[Dict[str, Any]]:
    return filter_restricted_worker_tools(tools, request)


def create_trusted_job(*, mode: str, task: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a worker job from trusted in-process automation only.

    This helper is intentionally not an MCP handler. External callers must go
    through handle_web_worker(create), which requires internal TriForce authority.
    """
    mode = str(mode or "").strip().lower()
    if mode not in {"internal", "ticket", "market"}:
        raise ValueError("invalid trusted worker mode")
    task = str(task or "").strip()
    if not task:
        raise ValueError("task is required")
    if len(task) > _MAX_TASK:
        raise ValueError(f"task exceeds {_MAX_TASK} characters")
    jobs = _load()
    job_id = secrets.token_urlsafe(24)
    jobs[job_id] = {
        "mode": mode,
        "state": "queued",
        "task": task,
        "metadata": dict(metadata or {}),
        "created_at": _now(),
        "claimed_at": None,
        "claimed_session": None,
        "completed_at": None,
        "result": None,
        "error": None,
    }
    _save(jobs)
    return {"ok": True, **_public_job(job_id, jobs[job_id])}


def _public_job(job_id: str, job: Dict[str, Any], *, include_task: bool = False) -> Dict[str, Any]:
    out = {
        "job_id": job_id,
        "mode": job.get("mode"),
        "state": job.get("state"),
        "created_at": job.get("created_at"),
        "claimed_at": job.get("claimed_at"),
        "completed_at": job.get("completed_at"),
        "metadata": job.get("metadata") or {},
    }
    if include_task:
        out["task"] = job.get("task") or ""
    if job.get("result"):
        out["result"] = job.get("result")
    if job.get("error"):
        out["error"] = job.get("error")
    return out


async def handle_web_worker(params: Dict[str, Any], request: Optional[Request] = None) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    session_id = _session_id(request)
    _logger.info("WEB_WORKER_ID action=%s fp=%s", action, _identity_fingerprint(request))
    jobs = _load()

    if action == "create":
        if not _is_internal(request):
            return {"ok": False, "error": "create requires internal TriForce authority", "code": "WORKER_CREATE_FORBIDDEN"}
        task = str(params.get("task") or "").strip()
        if not task:
            return {"ok": False, "error": "task is required"}
        if len(task) > _MAX_TASK:
            return {"ok": False, "error": f"task exceeds {_MAX_TASK} characters"}
        mode = str(params.get("mode") or "internal").strip().lower()
        if mode not in {"internal", "ticket", "market"}:
            return {"ok": False, "error": "mode must be internal, ticket, or market"}
        job_id = secrets.token_urlsafe(24)
        jobs[job_id] = {
            "mode": mode,
            "state": "queued",
            "task": task,
            "metadata": dict(params.get("metadata") or {}),
            "created_at": _now(),
            "claimed_at": None,
            "claimed_session": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        _save(jobs)
        return {
            "ok": True,
            **_public_job(job_id, jobs[job_id]),
            "claim_instruction": f"Call web_worker with action=claim and job_id={job_id}",
        }

    job_id = str(params.get("job_id") or "").strip()

    if action == "claim":
        if not session_id:
            return {"ok": False, "error": "claim requires an MCP session", "code": "WORKER_SESSION_REQUIRED"}
        if not job_id or job_id not in jobs:
            return {"ok": False, "error": "job not found", "code": "WORKER_JOB_NOT_FOUND"}
        job = jobs[job_id]
        state = str(job.get("state") or "")
        current = str(job.get("claimed_session") or "")
        if state == "claimed" and current != session_id:
            return {"ok": False, "error": "job already claimed", "code": "WORKER_ALREADY_CLAIMED"}
        if state not in {"queued", "claimed"}:
            return {"ok": False, "error": f"job is {state}", "code": "WORKER_JOB_NOT_CLAIMABLE"}
        # One MCP session may own only one active job. This prevents a ticket
        # session from claiming an internal job to escape its restricted profile.
        active_id, active = _find_active_by_session(session_id)
        if active and active_id != job_id:
            return {"ok": False, "error": "session already owns an active worker job", "code": "WORKER_SESSION_BUSY"}
        job["state"] = "claimed"
        job["claimed_session"] = session_id
        job["claimed_at"] = job.get("claimed_at") or _now()
        jobs[job_id] = job
        _save(jobs)
        try:
            from app.routes.mcp import _queue_tools_list_changed
            await _queue_tools_list_changed(session_id)
        except Exception:
            pass
        return {
            "ok": True,
            **_public_job(job_id, job, include_task=True),
            "policy": (
                "ticket_read_support_only"
                if job.get("mode") == "ticket"
                else "market_untrusted_read_only"
                if job.get("mode") == "market"
                else "normal_internal_permissions"
            ),
        }

    if action == "status":
        if job_id:
            job = jobs.get(job_id)
            if not job:
                return {"ok": False, "error": "job not found"}
            owner = str(job.get("claimed_session") or "")
            if not _is_internal(request) and owner != session_id:
                return {"ok": False, "error": "status forbidden for this job"}
            return {"ok": True, **_public_job(job_id, job, include_task=(owner == session_id))}
        if not _is_internal(request):
            active_id, active = _find_active_by_session(session_id)
            return {"ok": True, "jobs": ([_public_job(active_id, active, include_task=True)] if active else [])}
        rows = [_public_job(jid, job) for jid, job in jobs.items()]
        rows.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return {"ok": True, "jobs": rows[:100], "count": len(rows)}

    if action in {"complete", "fail"}:
        if not job_id or job_id not in jobs:
            return {"ok": False, "error": "job not found"}
        job = jobs[job_id]
        if str(job.get("claimed_session") or "") != session_id or str(job.get("state") or "") != "claimed":
            return {"ok": False, "error": "job is not claimed by this MCP session", "code": "WORKER_NOT_OWNER"}
        if action == "complete":
            result = str(params.get("result") or "")[:_MAX_RESULT]
            job["state"] = "completed"
            job["result"] = result
            job["error"] = None
        else:
            error = str(params.get("error") or "")[:_MAX_RESULT]
            job["state"] = "failed"
            job["error"] = error
        job["completed_at"] = _now()
        jobs[job_id] = job
        _save(jobs)
        try:
            from app.routes.mcp import _queue_tools_list_changed
            await _queue_tools_list_changed(session_id)
        except Exception:
            pass
        return {"ok": True, **_public_job(job_id, job)}

    if action == "cancel":
        if not _is_internal(request):
            return {"ok": False, "error": "cancel requires internal TriForce authority"}
        if not job_id or job_id not in jobs:
            return {"ok": False, "error": "job not found"}
        job = jobs[job_id]
        old_session = str(job.get("claimed_session") or "")
        job["state"] = "cancelled"
        job["completed_at"] = _now()
        jobs[job_id] = job
        _save(jobs)
        if old_session:
            try:
                from app.routes.mcp import _queue_tools_list_changed
                await _queue_tools_list_changed(old_session)
            except Exception:
                pass
        return {"ok": True, **_public_job(job_id, job)}

    return {"ok": False, "error": "unsupported action"}


WEB_WORKER_HANDLERS = {"web_worker": handle_web_worker}
