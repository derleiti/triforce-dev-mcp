from __future__ import annotations

from typing import Any, Dict

from app.services import mercatai


MERCATAI_TOOL_SCHEMAS = [
    {
        "name": "mercatai_market",
        "description": "Read Mercatai marketplace/discovery data: public activity, agent discovery metadata, safety policy metadata, or Stripe onboarding countries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["activity", "discovery", "safety", "onboarding_countries"],
                    "default": "activity",
                }
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_tasks",
        "description": "List Mercatai paid tasks, inspect one task including Mercatai's server-side execution_authorized signal, or list bids on a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "get", "bids"], "default": "list"},
                "task_id": {"type": "string", "description": "Task UUID for get/bids"},
                "status": {
                    "type": "string",
                    "enum": ["open", "bidding", "assigned", "in_progress", "review", "completed"],
                },
                "category": {
                    "type": "string",
                    "enum": ["research", "data_analysis", "content", "code_review", "procurement", "translation", "finance"],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_bid",
        "description": "Submit an authenticated Mercatai bid. Refuses demo/non-biddable tasks before sending the bid.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "price_eur", "delivery_hours"],
            "properties": {
                "task_id": {"type": "string"},
                "price_eur": {"type": "number", "minimum": 1},
                "delivery_hours": {"type": "integer", "minimum": 1, "maximum": 8760},
                "approach_summary": {"type": "string"},
                "sample_preview": {"type": "string", "maxLength": 1000},
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_deliver",
        "description": "Deliver work for an assigned Mercatai task. Hard fail-closed gate requires the authenticated task response to expose execution_authorized=true before delivery is sent.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "delivery_note"],
            "properties": {
                "task_id": {"type": "string"},
                "delivery_note": {"type": "string", "minLength": 1, "maxLength": 50000},
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_agent",
        "description": "Mercatai agent integration status/authentication, public reputation, or profile visibility.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "reputation", "visibility"], "default": "status"},
                "agent_ref": {"type": "string", "description": "Agent UUID or agent_id for reputation"},
                "agent_uuid": {"type": "string", "description": "Database UUID for visibility"},
                "profile_visibility": {"type": "string", "enum": ["public", "private"]},
                "verify": {"type": "boolean", "default": True},
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_stripe",
        "description": "Read/start/refresh Mercatai Stripe Connect onboarding. Country is never guessed; onboarding requires the real payout-account country.",
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": ["countries", "status", "onboard", "refresh"]},
                "agent_uuid": {"type": "string"},
                "country": {"type": "string", "minLength": 2, "maxLength": 2},
                "business_type": {
                    "type": "string",
                    "enum": ["individual", "company", "non_profit", "government_entity"],
                },
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_report",
        "description": "Report a live Mercatai task under the platform Trust & Safety Code. One report per agent/task is enforced by Mercatai.",
        "inputSchema": {
            "type": "object",
            "required": ["task_id", "reason_code"],
            "properties": {
                "task_id": {"type": "string"},
                "reason_code": {"type": "string"},
                "details": {"type": "string", "maxLength": 1000},
            },
        },
        "x_inventory": "mercatai",
    },
    {
        "name": "mercatai_developer",
        "description": "Read Mercatai developer API usage or affiliate earnings. Requires a separate documented mct_ developer key, not the normal agent API key.",
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": ["usage", "earnings"]},
            },
        },
        "x_inventory": "mercatai",
    },
]


async def _market(params: Dict[str, Any]):
    return await mercatai.market(str(params.get("action") or "activity"))


async def _tasks(params: Dict[str, Any]):
    return await mercatai.tasks(
        action=str(params.get("action") or "list"),
        task_id=params.get("task_id"),
        status_filter=params.get("status"),
        category=params.get("category"),
        limit=int(params.get("limit") or 20),
    )


async def _bid(params: Dict[str, Any]):
    return await mercatai.submit_bid(
        task_id=str(params.get("task_id") or ""),
        price_eur=float(params.get("price_eur")),
        delivery_hours=int(params.get("delivery_hours")),
        approach_summary=params.get("approach_summary"),
        sample_preview=params.get("sample_preview"),
    )


async def _deliver(params: Dict[str, Any]):
    return await mercatai.deliver(
        task_id=str(params.get("task_id") or ""),
        delivery_note=str(params.get("delivery_note") or ""),
    )


async def _agent(params: Dict[str, Any]):
    return await mercatai.agent(
        action=str(params.get("action") or "status"),
        agent_ref=params.get("agent_ref"),
        agent_uuid=params.get("agent_uuid"),
        profile_visibility=params.get("profile_visibility"),
        verify=bool(params.get("verify", True)),
    )


async def _stripe(params: Dict[str, Any]):
    return await mercatai.stripe(
        action=str(params.get("action") or ""),
        agent_uuid=params.get("agent_uuid"),
        country=params.get("country"),
        business_type=params.get("business_type"),
    )


async def _report(params: Dict[str, Any]):
    return await mercatai.report_task(
        task_id=str(params.get("task_id") or ""),
        reason_code=str(params.get("reason_code") or ""),
        details=params.get("details"),
    )


async def _developer(params: Dict[str, Any]):
    return await mercatai.developer(str(params.get("action") or ""))


MERCATAI_HANDLERS = {
    "mercatai_market": _market,
    "mercatai_tasks": _tasks,
    "mercatai_bid": _bid,
    "mercatai_deliver": _deliver,
    "mercatai_agent": _agent,
    "mercatai_stripe": _stripe,
    "mercatai_report": _report,
    "mercatai_developer": _developer,
}
