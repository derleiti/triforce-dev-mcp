from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp

from app.config import get_settings


class MercataiError(RuntimeError):
    """Safe Mercatai API error that never embeds credentials."""

    def __init__(self, message: str, *, status: int = 0, payload: Any = None):
        super().__init__(message)
        self.status = int(status or 0)
        self.payload = payload


_AUTH_LOCK = asyncio.Lock()
_AUTH_CACHE: Dict[str, Any] = {
    "access_token": "",
    "refresh_token": "",
    "expires_at": 0.0,
}


def _cfg():
    return get_settings()


def _base_url() -> str:
    return str(getattr(_cfg(), "mercatai_base_url", "https://mercatai.eu") or "https://mercatai.eu").rstrip("/")


def _timeout() -> aiohttp.ClientTimeout:
    seconds = int(getattr(_cfg(), "mercatai_timeout_seconds", 30) or 30)
    return aiohttp.ClientTimeout(total=max(5, min(seconds, 120)))


def _agent_id() -> str:
    return str(getattr(_cfg(), "mercatai_agent_id", "") or "").strip()


def _agent_uuid(override: Optional[str] = None) -> str:
    return str(override or getattr(_cfg(), "mercatai_agent_uuid", "") or "").strip()


def _agent_api_key() -> str:
    return str(getattr(_cfg(), "mercatai_api_key", "") or "").strip()


def _developer_api_key() -> str:
    return str(getattr(_cfg(), "mercatai_developer_api_key", "") or "").strip()


def _public_config() -> Dict[str, Any]:
    key = _agent_api_key()
    developer_key = _developer_api_key()
    return {
        "base_url": _base_url(),
        "agent_id": _agent_id() or None,
        "agent_uuid": _agent_uuid() or None,
        "api_key_configured": bool(key),
        "developer_api_key_configured": bool(developer_key),
        "developer_key_looks_valid": bool(developer_key.startswith("mct_")) if developer_key else False,
    }


async def _http(
    method: str,
    path: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
) -> tuple[int, Any]:
    url = f"{_base_url()}{path}"
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "TriForce-Mercatai/1.0",
    }
    if headers:
        request_headers.update(headers)

    try:
        async with aiohttp.ClientSession(timeout=_timeout()) as session:
            async with session.request(
                method.upper(),
                url,
                headers=request_headers,
                params=params,
                json=json_body,
            ) as response:
                text = await response.text()
                try:
                    payload: Any = await response.json(content_type=None)
                except Exception:
                    payload = {"text": text[:10000]} if text else {}
                return response.status, payload
    except asyncio.TimeoutError as exc:
        raise MercataiError("Mercatai request timed out") from exc
    except aiohttp.ClientError as exc:
        raise MercataiError(f"Mercatai network error: {type(exc).__name__}") from exc


def _error_from_response(status: int, payload: Any, action: str) -> MercataiError:
    message = ""
    if isinstance(payload, dict):
        message = str(payload.get("message") or payload.get("error") or payload.get("detail") or "").strip()
        code = str(payload.get("code") or "").strip()
        if code:
            message = f"{code}: {message}" if message else code
    if not message:
        message = f"Mercatai {action} failed with HTTP {status}"
    return MercataiError(message[:1000], status=status, payload=payload)


async def _login() -> str:
    agent_id = _agent_id()
    api_key = _agent_api_key()
    if not agent_id:
        raise MercataiError("MERCATAI_AGENT_ID is not configured")
    if not api_key:
        raise MercataiError("MERCATAI_API_KEY is not configured")

    status, payload = await _http(
        "POST",
        "/api/v1/auth/login",
        json_body={"agent_id": agent_id, "api_key": api_key},
    )
    if status != 200 or not isinstance(payload, dict):
        raise _error_from_response(status, payload, "login")

    access_token = str(payload.get("access_token") or "")
    refresh_token = str(payload.get("refresh_token") or "")
    expires_in = int(payload.get("expires_in") or 900)
    if not access_token:
        raise MercataiError("Mercatai login returned no access_token", status=status)

    _AUTH_CACHE.update(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=time.monotonic() + max(30, expires_in - 30),
    )
    return access_token


async def _refresh() -> str:
    refresh_token = str(_AUTH_CACHE.get("refresh_token") or "")
    if not refresh_token:
        return await _login()

    status, payload = await _http(
        "POST",
        "/api/v1/auth/refresh",
        json_body={"refresh_token": refresh_token},
    )
    if status != 200 or not isinstance(payload, dict):
        _AUTH_CACHE.update(access_token="", refresh_token="", expires_at=0.0)
        return await _login()

    access_token = str(payload.get("access_token") or "")
    new_refresh = str(payload.get("refresh_token") or refresh_token)
    expires_in = int(payload.get("expires_in") or 900)
    if not access_token:
        return await _login()

    _AUTH_CACHE.update(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_at=time.monotonic() + max(30, expires_in - 30),
    )
    return access_token


async def _agent_token(*, force: bool = False) -> str:
    async with _AUTH_LOCK:
        token = str(_AUTH_CACHE.get("access_token") or "")
        if not force and token and time.monotonic() < float(_AUTH_CACHE.get("expires_at") or 0):
            return token
        if not force and _AUTH_CACHE.get("refresh_token"):
            return await _refresh()
        return await _login()


async def _agent_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
    retry_auth: bool = True,
) -> Any:
    token = await _agent_token()
    status, payload = await _http(
        method,
        path,
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        json_body=json_body,
    )
    if status == 401 and retry_auth:
        token = await _agent_token(force=True)
        status, payload = await _http(
            method,
            path,
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            json_body=json_body,
        )
    if not 200 <= status < 300:
        raise _error_from_response(status, payload, path)
    return payload


async def _public_request(
    method: str,
    path: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Any = None,
) -> Any:
    status, payload = await _http(method, path, params=params, json_body=json_body)
    if not 200 <= status < 300:
        raise _error_from_response(status, payload, path)
    return payload


async def _developer_request(path: str) -> Any:
    api_key = _developer_api_key()
    if not api_key:
        raise MercataiError("MERCATAI_DEVELOPER_API_KEY is not configured")
    if not api_key.startswith("mct_"):
        raise MercataiError("MERCATAI_DEVELOPER_API_KEY does not have the documented mct_ prefix")
    status, payload = await _http(
        "GET",
        path,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    if not 200 <= status < 300:
        raise _error_from_response(status, payload, path)
    return payload


def _result(coro_result: Any) -> Dict[str, Any]:
    return {"ok": True, "data": coro_result}


def _failure(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, MercataiError):
        out: Dict[str, Any] = {"ok": False, "error": str(exc)}
        if exc.status:
            out["status"] = exc.status
        if isinstance(exc.payload, dict):
            safe = {
                key: value
                for key, value in exc.payload.items()
                if key not in {"access_token", "refresh_token", "api_key", "client_secret"}
            }
            if safe:
                out["detail"] = safe
        return out
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def delivery_gate(task: Dict[str, Any]) -> Dict[str, Any]:
    """Fail-closed implementation of Mercatai's execution_authorized contract."""
    reasons = []
    if task.get("is_demo") is not False:
        reasons.append("task is demo or is_demo is not explicitly false")
    if task.get("status") != "in_progress":
        reasons.append("status is not in_progress")
    if task.get("funding_status") != "funded":
        reasons.append("funding_status is not funded")
    if task.get("execution_authorized") is not True:
        reasons.append("execution_authorized is not true")
    if task.get("next_action") != "perform_and_deliver":
        reasons.append("next_action is not perform_and_deliver")
    return {"authorized": not reasons, "reasons": reasons}


async def status(*, verify: bool = True) -> Dict[str, Any]:
    result: Dict[str, Any] = {"ok": True, "config": _public_config(), "authenticated": False}
    if not verify:
        return result
    if not _agent_id() or not _agent_api_key():
        result["ok"] = False
        result["error"] = "Mercatai agent credentials are incomplete"
        return result
    try:
        await _agent_token(force=True)
        result["authenticated"] = True
    except Exception as exc:
        return {**result, **_failure(exc)}
    return result


async def market(action: str) -> Dict[str, Any]:
    try:
        if action == "activity":
            return _result(await _public_request("GET", "/api/v1/activity"))
        if action == "discovery":
            return _result(await _public_request("GET", "/.well-known/agent.json"))
        if action == "safety":
            return _result(await _public_request("GET", "/.well-known/mercatai-safety.json"))
        if action == "onboarding_countries":
            return _result(await _public_request("GET", "/api/v1/onboarding-countries"))
        return {"ok": False, "error": f"unsupported market action: {action}"}
    except Exception as exc:
        return _failure(exc)


async def tasks(
    *,
    action: str = "list",
    task_id: Optional[str] = None,
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    try:
        if action == "list":
            params: Dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
            if status_filter:
                params["status"] = status_filter
            if category:
                params["category"] = category
            if _agent_id() and _agent_api_key():
                return _result(await _agent_request("GET", "/api/v1/tasks", params=params))
            return _result(await _public_request("GET", "/api/v1/tasks", params=params))

        if not task_id:
            return {"ok": False, "error": "task_id is required"}

        if action == "get":
            if _agent_id() and _agent_api_key():
                data = await _agent_request("GET", f"/api/v1/tasks/{task_id}")
            else:
                data = await _public_request("GET", f"/api/v1/tasks/{task_id}")
            out = _result(data)
            if isinstance(data, dict):
                out["execution_gate"] = delivery_gate(data)
            return out

        if action == "bids":
            if _agent_id() and _agent_api_key():
                return _result(await _agent_request("GET", f"/api/v1/tasks/{task_id}/bids"))
            return _result(await _public_request("GET", f"/api/v1/tasks/{task_id}/bids"))

        return {"ok": False, "error": f"unsupported tasks action: {action}"}
    except Exception as exc:
        return _failure(exc)


async def submit_bid(
    *,
    task_id: str,
    price_eur: float,
    delivery_hours: int,
    approach_summary: Optional[str] = None,
    sample_preview: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        task = await _agent_request("GET", f"/api/v1/tasks/{task_id}")
        if not isinstance(task, dict):
            return {"ok": False, "error": "task detail response is not an object"}
        if task.get("is_demo") is True:
            return {"ok": False, "error": "refusing to bid on a demo task", "task": task}
        if task.get("status") not in {"open", "bidding"}:
            return {"ok": False, "error": f"task is not biddable (status={task.get('status')})", "task": task}
        next_action = task.get("next_action")
        if next_action not in {None, "submit_bid"}:
            return {"ok": False, "error": f"Mercatai next_action is {next_action}, not submit_bid", "task": task}

        payload: Dict[str, Any] = {
            "task_id": task_id,
            "price_eur": float(price_eur),
            "delivery_hours": int(delivery_hours),
        }
        if approach_summary:
            payload["approach_summary"] = str(approach_summary)
        if sample_preview:
            payload["sample_preview"] = str(sample_preview)[:1000]
        return _result(await _agent_request("POST", "/api/v1/bids", json_body=payload))
    except Exception as exc:
        return _failure(exc)


async def deliver(*, task_id: str, delivery_note: str) -> Dict[str, Any]:
    try:
        task = await _agent_request("GET", f"/api/v1/tasks/{task_id}")
        if not isinstance(task, dict):
            return {"ok": False, "error": "task detail response is not an object"}
        gate = delivery_gate(task)
        if not gate["authorized"]:
            return {
                "ok": False,
                "error": "delivery blocked by Mercatai execution gate",
                "execution_gate": gate,
                "task": task,
            }
        note = str(delivery_note or "")
        if not note.strip():
            return {"ok": False, "error": "delivery_note must not be empty"}
        if len(note) > 50000:
            return {"ok": False, "error": "delivery_note exceeds Mercatai's 50,000 character limit"}
        data = await _agent_request(
            "POST",
            f"/api/v1/tasks/{task_id}/deliver",
            json_body={"delivery_note": note},
        )
        return {"ok": True, "data": data, "execution_gate": gate}
    except Exception as exc:
        return _failure(exc)


async def agent(
    *,
    action: str,
    agent_ref: Optional[str] = None,
    agent_uuid: Optional[str] = None,
    profile_visibility: Optional[str] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    try:
        if action == "status":
            return await status(verify=verify)
        if action == "reputation":
            ref = str(agent_ref or _agent_id() or "").strip()
            if not ref:
                return {"ok": False, "error": "agent_ref or MERCATAI_AGENT_ID is required"}
            return _result(await _public_request("GET", f"/api/v1/agents/{ref}/reputation"))
        if action == "visibility":
            uuid = _agent_uuid(agent_uuid)
            if not uuid:
                return {"ok": False, "error": "agent_uuid or MERCATAI_AGENT_UUID is required"}
            if profile_visibility not in {"public", "private"}:
                return {"ok": False, "error": "profile_visibility must be public or private"}
            return _result(
                await _agent_request(
                    "PATCH",
                    f"/api/v1/agents/{uuid}/visibility",
                    json_body={"profile_visibility": profile_visibility},
                )
            )
        return {"ok": False, "error": f"unsupported agent action: {action}"}
    except Exception as exc:
        return _failure(exc)


async def stripe(
    *,
    action: str,
    agent_uuid: Optional[str] = None,
    country: Optional[str] = None,
    business_type: Optional[str] = None,
) -> Dict[str, Any]:
    try:
        if action == "countries":
            return _result(await _public_request("GET", "/api/v1/onboarding-countries"))

        uuid = _agent_uuid(agent_uuid)
        if not uuid:
            return {"ok": False, "error": "agent_uuid or MERCATAI_AGENT_UUID is required"}

        if action == "status":
            return _result(await _agent_request("GET", f"/api/v1/agents/{uuid}/stripe-onboard"))
        if action == "refresh":
            return _result(await _agent_request("POST", f"/api/v1/agents/{uuid}/stripe-onboard/refresh"))
        if action == "onboard":
            if not country:
                return {"ok": False, "error": "country is required for Stripe onboarding"}
            payload: Dict[str, Any] = {"country": str(country).upper()}
            if business_type:
                payload["business_type"] = business_type
            return _result(
                await _agent_request(
                    "POST",
                    f"/api/v1/agents/{uuid}/stripe-onboard",
                    json_body=payload,
                )
            )
        return {"ok": False, "error": f"unsupported stripe action: {action}"}
    except Exception as exc:
        return _failure(exc)


async def report_task(*, task_id: str, reason_code: str, details: Optional[str] = None) -> Dict[str, Any]:
    try:
        payload: Dict[str, Any] = {"reason_code": str(reason_code)}
        if details:
            payload["details"] = str(details)[:1000]
        return _result(
            await _agent_request(
                "POST",
                f"/api/v1/tasks/{task_id}/report",
                json_body=payload,
            )
        )
    except Exception as exc:
        return _failure(exc)


async def developer(action: str) -> Dict[str, Any]:
    try:
        if action == "usage":
            return _result(await _developer_request("/api/v1/developer/usage"))
        if action == "earnings":
            return _result(await _developer_request("/api/v1/developer/earnings"))
        return {"ok": False, "error": f"unsupported developer action: {action}"}
    except Exception as exc:
        return _failure(exc)
