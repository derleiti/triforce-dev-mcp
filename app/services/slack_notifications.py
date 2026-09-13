"""Optional Slack transport for TriForce notifications.

Disabled by default. Slack is a short-lived communication surface; TriForce/GitHub
remain the durable source of truth. The transport only emits high-signal events.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any, Mapping

logger = logging.getLogger("ailinux.slack.notifications")

_TRUE = {"1", "true", "yes", "on"}
_DEFAULT_CHANNELS = {
    "ops": "ops",
    "bugs": "bugs",
    "releases": "releases",
    "mcp": "mcp",
    "dev": "dev",
}


def _enabled() -> bool:
    return os.environ.get("SLACK_ENABLED", "false").strip().lower() in _TRUE


def _channel_map() -> dict[str, str]:
    channels = dict(_DEFAULT_CHANNELS)
    for key in channels:
        value = os.environ.get(f"SLACK_CHANNEL_{key.upper()}", "").strip().lstrip("#")
        if value:
            channels[key] = value
    return channels


def _route(event: Mapping[str, Any]) -> str | None:
    """Return a logical channel for high-signal events, otherwise None."""
    priority = str(event.get("priority", "normal")).lower()
    event_type = str(event.get("event_type", "")).lower()
    tags = {str(tag).lower() for tag in (event.get("tags") or [])}

    if tags & {"release", "deploy", "deployment", "rollback", "package", "packaging"}:
        return "releases"
    if event_type.startswith("incident.") or event_type.startswith("ops."):
        return "ops" if priority in {"high", "critical"} else None
    if "security" in tags or "incident" in tags:
        return "ops"
    if event_type == "support.bug_report" or "bug" in tags:
        return "bugs" if priority in {"high", "critical"} else None
    if "mcp" in tags or event_type.startswith("mcp."):
        return "mcp" if priority in {"high", "critical"} else None
    if priority == "critical":
        return "ops"
    return None


def _format_message(event: Mapping[str, Any]) -> str:
    priority = str(event.get("priority", "normal")).upper()
    event_type = str(event.get("event_type", "event"))
    title = str(event.get("title", "Notification")).strip()
    body = str(event.get("body", "")).strip()
    action_url = str(event.get("action_url", "")).strip()
    event_id = str(event.get("id", "")).strip()

    lines = [f"[{priority}] [{event_type}] {title}"]
    if body:
        lines.append(body[:1800])
    if action_url:
        lines.append(f"Details: {action_url}")
    if event_id:
        lines.append(f"TriForce event: {event_id}")
    return "\n".join(lines)[:2900]


def _post_sync(token: str, channel: str, text: str) -> tuple[bool, str]:
    payload = json.dumps({"channel": channel, "text": text, "unfurl_links": False}).encode("utf-8")
    request = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "AILinux-TriForce/SlackTransport",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return False, f"transport_error:{type(exc).__name__}"
    if not result.get("ok"):
        return False, str(result.get("error") or "slack_api_error")
    return True, str(result.get("ts") or "ok")


async def deliver_slack_notification(event: Mapping[str, Any]) -> dict[str, Any]:
    """Deliver one event when Slack is enabled and the event passes signal filtering."""
    if not _enabled():
        return {"sent": False, "reason": "disabled"}

    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    if not token:
        return {"sent": False, "reason": "missing_token"}

    logical_channel = _route(event)
    if logical_channel is None:
        return {"sent": False, "reason": "filtered"}

    channel = _channel_map()[logical_channel]
    ok, detail = await asyncio.to_thread(_post_sync, token, channel, _format_message(event))
    if ok:
        logger.info("Slack notification sent | channel=%s | event=%s", channel, event.get("id", ""))
        return {"sent": True, "channel": channel, "detail": detail}

    logger.warning("Slack notification failed | channel=%s | error=%s", channel, detail)
    return {"sent": False, "channel": channel, "reason": detail}


def schedule_slack_notification(event: Mapping[str, Any]) -> None:
    """Schedule delivery without delaying the primary notification path."""
    if not _enabled():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(deliver_slack_notification(dict(event)))
