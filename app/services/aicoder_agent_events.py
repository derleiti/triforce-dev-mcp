"""AICoder agent run -> TriForce notification policy.

Keeps agent-runtime notification semantics separate from the generic notification
manager dispatcher so a single failed headless run never triggers suggestion mail.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .aicoder_runner import AICoderRunResult

STATE_FILE = Path('/var/tristar/agents/runtime-notify-state.json')
FAILURE_MAIL_THRESHOLD = 3
DEFAULT_COOLDOWN_S = 300
LONG_RUN_MS = 10 * 60 * 1000
DAILY_FAILURE_LIMIT = 200
DIGEST_CHECK_INTERVAL_S = 15 * 60
DEFAULT_DIGEST_TIME = '08:00'
DEFAULT_DIGEST_TZ = 'Europe/Berlin'

logger = logging.getLogger('ailinux.aicoder_agent_events')
_state_lock = asyncio.Lock()


def _elapsed_ms(result: AICoderRunResult) -> int:
    for event in reversed(result.events):
        if event.get('type') == 'run_terminal':
            try:
                return max(0, int(event.get('elapsed_ms') or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def _is_headless_denial(result: AICoderRunResult) -> bool:
    evidence = [result.error, result.response]
    for event in result.events:
        if event.get('type') == 'tool_result' and event.get('is_error'):
            evidence.append(str(event.get('result') or ''))
    text = ' '.join(str(item or '') for item in evidence).lower()
    explicit = any(k in text for k in ('headless', 'denied', 'abgelehnt', 'not approved', 'rejected'))
    headless_abort = result.status == 'paused' and 'aborted by user' in text
    return explicit or headless_abort


def _load_state(path: Path | None = None) -> dict[str, Any]:
    path = path or STATE_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict[str, Any], path: Path | None = None) -> None:
    path = path or STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    tmp.chmod(0o600)
    os.replace(tmp, path)


def _event_key(profile_id: str, kind: str) -> str:
    return f'{profile_id}:{kind}'


def _digest_clock() -> tuple[ZoneInfo, int, int]:
    tz_name = str(os.environ.get("AICODER_AGENT_DIGEST_TZ") or DEFAULT_DIGEST_TZ).strip()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        logger.warning("Invalid digest timezone %r; using %s", tz_name, DEFAULT_DIGEST_TZ)
        tz = ZoneInfo(DEFAULT_DIGEST_TZ)
    raw = str(os.environ.get("AICODER_AGENT_DIGEST_TIME") or DEFAULT_DIGEST_TIME).strip()
    try:
        hs, ms = raw.split(":", 1); hour, minute = int(hs), int(ms)
        if not (0 <= hour <= 23 and 0 <= minute <= 59): raise ValueError
    except Exception:
        hour, minute = 8, 0
    return tz, hour, minute

def _queue_failure_for_digest(state: dict[str, Any], result: AICoderRunResult, streak: int) -> None:
    pending = state.setdefault("daily_failures", [])
    if not isinstance(pending, list): pending = []; state["daily_failures"] = pending
    did = result.run_id or result.plan_id or f"{result.profile_id}:{time.time_ns()}"
    if any(isinstance(i, dict) and str(i.get("id") or "") == did for i in pending): return
    pending.append({"id": did, "profile_id": result.profile_id, "status": result.status, "streak": int(streak), "model": result.model or "", "run_id": result.run_id or "", "plan_id": result.plan_id or "", "error": (result.error or "AICoder run failed without error text.")[:2000], "elapsed_ms": _elapsed_ms(result), "created_at": datetime.now(timezone.utc).isoformat()})
    state["daily_failures"] = pending[-DAILY_FAILURE_LIMIT:]

def _digest_due(state: dict[str, Any], now: datetime | None = None) -> tuple[bool, str, str]:
    tz, hour, minute = _digest_clock(); current = now or datetime.now(timezone.utc)
    if current.tzinfo is None: current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(tz); day = local.date().isoformat()
    if state.get("last_digest_date") == day: return False, day, "already_sent"
    if (local.hour, local.minute) < (hour, minute): return False, day, "not_due"
    return True, day, "due"

def _digest_body(entries: list[dict[str, Any]], day: str) -> str:
    grouped = {}
    for e in entries: grouped.setdefault(str(e.get("profile_id") or "unknown"), []).append(e)
    lines=[f"AICoder failure digest for {day}", f"Total failed runs: {len(entries)}", ""]
    for pid in sorted(grouped):
        xs=grouped[pid]; latest=xs[-1]; mx=max(int(x.get("streak") or 0) for x in xs)
        lines += [f"{pid}: {len(xs)} failed run(s), max streak {mx}", f"  latest model: {latest.get('model') or 'unknown'}", f"  latest run: {latest.get('run_id') or latest.get('plan_id') or '-'}", f"  latest error: {str(latest.get('error') or 'unknown')[:600]}", ""]
    return "\n".join(lines).rstrip()+"\n"

async def send_daily_failure_digest(*, now: datetime | None = None, force: bool = False) -> dict[str, Any]:
    from app.services.mail_service import mail_service
    async with _state_lock:
        state=_load_state(); due, day, reason=_digest_due(state, now)
        if not force and not due: return {"sent":False,"count":0,"reason":reason,"date":day}
        pending=[i for i in state.get("daily_failures",[]) if isinstance(i,dict)]
        if not pending:
            state["last_digest_date"]=day; state["last_digest_at"]=datetime.now(timezone.utc).isoformat(); _save_state(state)
            return {"sent":False,"count":0,"reason":"empty","date":day}
        ids={str(i.get("id") or "") for i in pending}; body=_digest_body(pending,day)
    try:
        await asyncio.to_thread(mail_service.send, "admin@ailinux.me", f"[TriForce] AICoder daily failure digest {day} ({len(pending)})", body)
    except Exception as exc:
        logger.warning("AICoder daily failure digest send failed: %s", exc)
        return {"sent":False,"count":len(pending),"reason":"mail_error","date":day}
    async with _state_lock:
        state=_load_state(); cur=state.get("daily_failures",[])
        state["daily_failures"]=[i for i in cur if not isinstance(i,dict) or str(i.get("id") or "") not in ids]
        state["last_digest_date"]=day; state["last_digest_at"]=datetime.now(timezone.utc).isoformat(); _save_state(state)
    return {"sent":True,"count":len(pending),"reason":"sent","date":day}

async def aicoder_failure_digest_loop(check_interval_s: int = DIGEST_CHECK_INTERVAL_S) -> None:
    while True:
        try: await send_daily_failure_digest()
        except asyncio.CancelledError: raise
        except Exception as exc: logger.warning("AICoder failure digest loop error: %s", exc)
        await asyncio.sleep(max(60, int(check_interval_s)))


async def _store_notification(
    *, profile_id: str, kind: str, priority: str, title: str, body: str,
    result: AICoderRunResult, cooldown_s: int = DEFAULT_COOLDOWN_S,
) -> bool:
    from app.mcp.notification_manager import create_event

    async with _state_lock:
        state = _load_state()
        cooldowns = state.setdefault('cooldowns', {})
        key = _event_key(profile_id, kind)
        now = time.time()
        last = float(cooldowns.get(key, 0) or 0)
        if now - last < max(0, int(cooldown_s)):
            return False
        cooldowns[key] = now
        _save_state(state)

    await create_event(
        title=title,
        body=body,
        source='agent',
        priority=priority,
        event_type=f'agent.{kind}',
        tags=['agent-runtime'],
        correlation_id=result.run_id or result.plan_id or profile_id,
        metadata={
            'profile_id': profile_id,
            'run_id': result.run_id,
            'plan_id': result.plan_id,
            'model': result.model,
            'exit_code': result.exit_code,
            'elapsed_ms': _elapsed_ms(result),
        },
    )
    return True


async def _send_critical_mail(profile_id: str, result: AICoderRunResult, streak: int) -> bool:
    """Send only threshold/escalation mail; ordinary failures never call SMTP."""
    from app.services.mail_service import mail_service

    subject = f'[TriForce] AICoder agent {profile_id} failed {streak} times'
    body = (
        f'Agent profile: {profile_id}\n'
        f'Failure streak: {streak}\n'
        f'Model: {result.model or "unknown"}\n'
        f'Run ID: {result.run_id or "-"}\n'
        f'Plan ID: {result.plan_id or "-"}\n'
        f'Error: {(result.error or "unknown")[:2000]}\n'
    )
    try:
        await asyncio.to_thread(mail_service.send, 'admin@ailinux.me', subject, body)
        return True
    except Exception:
        return False


async def record_aicoder_run(result: AICoderRunResult, *, cooldown_s: int = DEFAULT_COOLDOWN_S) -> dict[str, Any]:
    """Map one normalized AICoder run result to notifications/mail policy."""
    profile_id = result.profile_id
    elapsed_ms = _elapsed_ms(result)

    headless_denial = _is_headless_denial(result)
    async with _state_lock:
        state = _load_state()
        failures = state.setdefault('failure_streaks', {})
        previous = int(failures.get(profile_id, 0) or 0)
        if result.status == 'success' or result.status == 'paused' or headless_denial:
            streak = 0
            failures[profile_id] = 0
        else:
            streak = previous + 1
            failures[profile_id] = streak
            _queue_failure_for_digest(state, result, streak)
        _save_state(state)

    emitted = False
    mailed = False
    kind = ''

    if result.status == 'success':
        if elapsed_ms >= LONG_RUN_MS:
            kind = 'long_run_completed'
            emitted = await _store_notification(
                profile_id=profile_id, kind=kind, priority='normal',
                title=f'AICoder {profile_id} completed a long run',
                body=f'Run completed in {elapsed_ms / 1000:.0f}s with {result.model or "unknown model"}.',
                result=result, cooldown_s=cooldown_s,
            )
    elif headless_denial:
        kind = 'headless_denied'
        emitted = await _store_notification(
            profile_id=profile_id, kind=kind, priority='high',
            title=f'AICoder {profile_id}: headless action denied',
            body=(result.error or result.response or 'Headless approval policy denied the requested action.')[:2000],
            result=result, cooldown_s=cooldown_s,
        )
    elif result.status == 'paused':
        kind = 'run_paused'
    elif streak >= FAILURE_MAIL_THRESHOLD:
        kind = 'repeated_failure'
        emitted = await _store_notification(
            profile_id=profile_id, kind=kind, priority='critical',
            title=f'AICoder {profile_id} failed {streak} times consecutively',
            body=(result.error or 'AICoder run failed without error text.')[:2000],
            result=result, cooldown_s=cooldown_s,
        )
        if streak == FAILURE_MAIL_THRESHOLD:
            mailed = await _send_critical_mail(profile_id, result, streak)
    else:
        kind = 'run_failed'
        emitted = await _store_notification(
            profile_id=profile_id, kind=kind, priority='high',
            title=f'AICoder {profile_id} run failed',
            body=(result.error or 'AICoder run failed without error text.')[:2000],
            result=result, cooldown_s=cooldown_s,
        )

    return {'kind': kind, 'emitted': emitted, 'mailed': mailed, 'failure_streak': streak}
