"""Public crash/self-test ingestion. Recipient and sender are fixed server-side."""
from __future__ import annotations

import asyncio
from typing import Any
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.services.bug_reports import submit_report
from app.utils.rate_limit_compat import RateLimiter

router = APIRouter(prefix="/bugs", tags=["Bug Reports"])


class BugReport(BaseModel):
    app: str = Field(min_length=1, max_length=128)
    repo: str = Field(default="", max_length=128)
    version: str = Field(default="", max_length=64)
    platform: str = Field(default="", max_length=64)
    os_version: str = Field(default="", max_length=128)
    arch: str = Field(default="", max_length=64)
    channel: str = Field(default="", max_length=64)
    event_type: str = Field(default="crash", max_length=32)
    delivery: str = Field(default="automatic", max_length=32)
    exception_type: str = Field(default="", max_length=256)
    exception_message: str = Field(default="", max_length=4000)
    stack: str = Field(default="", max_length=48000)
    logs: list[Any] = Field(default_factory=list, max_length=250)
    selftest: dict[str, Any] | list[Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    user_message: str = Field(default="", max_length=8000)
    install_id: str = Field(default="", max_length=256)


@router.post("/report", status_code=202, dependencies=[Depends(RateLimiter(times=10, seconds=60))])
async def receive_bug_report(report: BugReport, request: Request) -> dict[str, Any]:
    # Do not persist raw IPs. RateLimiter handles source-level abuse control.
    payload = report.model_dump()
    payload["metadata"] = dict(payload.get("metadata") or {})
    payload["metadata"]["transport"] = "https"
    return await asyncio.to_thread(submit_report, payload)


@router.get("/health")
async def bug_report_health() -> dict[str, Any]:
    return {"ok": True, "endpoint": "/v1/bugs/report", "recipient": "bugs@ailinux.me"}
