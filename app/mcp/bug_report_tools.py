"""Admin-only MCP tools for AILinux crash/self-test triage."""
from __future__ import annotations

from typing import Any

from app.services.bug_reports import get_report, list_reports, stats, update_status

BUG_REPORT_TOOLS = [
    {
        "name": "bug_reports_list",
        "description": "List persisted AILinux crash, error and startup self-test reports. Admin-only; supports status/app/fingerprint filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
                "status": {"type": "string", "enum": ["", "new", "triaged", "resolved", "ignored"], "default": ""},
                "app": {"type": "string", "maxLength": 128, "default": ""},
                "fingerprint": {"type": "string", "maxLength": 64, "default": ""},
            },
        },
        "annotations": {"readOnlyHint": True},
        "x_inventory": "admin",
    },
    {
        "name": "bug_report_get",
        "description": "Read one persisted AILinux bug report including redacted logs, stack trace, self-test and metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {"report_id": {"type": "string", "minLength": 8, "maxLength": 64}},
            "required": ["report_id"],
        },
        "annotations": {"readOnlyHint": True},
        "x_inventory": "admin",
    },
    {
        "name": "bug_report_stats",
        "description": "Aggregate AILinux bug-report counts by status, app and fingerprint for triage and regression analysis.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
        "x_inventory": "admin",
    },
    {
        "name": "bug_report_status",
        "description": "Set a persisted AILinux bug report triage state to new, triaged, resolved or ignored.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string", "minLength": 8, "maxLength": 64},
                "status": {"type": "string", "enum": ["new", "triaged", "resolved", "ignored"]},
            },
            "required": ["report_id", "status"],
        },
        "annotations": {"readOnlyHint": False, "idempotentHint": True},
        "x_inventory": "admin",
    },
]


async def handle_bug_reports_list(params: dict[str, Any]) -> dict[str, Any]:
    rows = list_reports(
        limit=int(params.get("limit") or 50),
        status=str(params.get("status") or ""),
        app=str(params.get("app") or ""),
        fingerprint=str(params.get("fingerprint") or ""),
    )
    return {"ok": True, "count": len(rows), "reports": rows}


async def handle_bug_report_get(params: dict[str, Any]) -> dict[str, Any]:
    report_id = str(params.get("report_id") or "").strip()
    row = get_report(report_id)
    if row is None:
        return {"ok": False, "code": "BUG_REPORT_NOT_FOUND", "report_id": report_id}
    return {"ok": True, "report": row}


async def handle_bug_report_stats(params: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, **stats()}


async def handle_bug_report_status(params: dict[str, Any]) -> dict[str, Any]:
    return update_status(str(params.get("report_id") or "").strip(), str(params.get("status") or "").strip())


BUG_REPORT_HANDLERS = {
    "bug_reports_list": handle_bug_reports_list,
    "bug_report_get": handle_bug_report_get,
    "bug_report_stats": handle_bug_report_stats,
    "bug_report_status": handle_bug_report_status,
}
