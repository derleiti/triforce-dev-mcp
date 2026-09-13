from __future__ import annotations

import pytest

from app.mcp.handlers_v4 import HandlerRegistry
from app.mcp import structured_admin


@pytest.mark.asyncio
async def test_remote_task_delegates_to_shared_remote_runner(monkeypatch):
    captured = {}
    async def fake_runner(params):
        captured.update(params)
        return {"success": True, "output": "ok", "exit_code": 0}
    monkeypatch.setattr(structured_admin, "handle_task_runner", fake_runner)
    registry = HandlerRegistry()
    registry._register_remote_handlers()

    result = await registry.call("remote_task", {
        "host": "zombie-pc", "command": "hostname", "timeout": 17,
    })

    assert result["success"] is True
    assert captured == {
        "action": "execute_remote",
        "host": "zombie-pc",
        "task_data": "hostname",
        "timeout": 17,
    }


@pytest.mark.asyncio
async def test_remote_task_accepts_registered_ip(monkeypatch):
    captured = {}
    async def fake_runner(params):
        captured.update(params)
        return {"success": True}
    monkeypatch.setattr(structured_admin, "handle_task_runner", fake_runner)
    registry = HandlerRegistry()
    registry._register_remote_handlers()

    await registry.call("remote_task", {"host": "10.10.0.2", "command": "uptime"})
    assert captured["host"] == "zombie-pc"


@pytest.mark.asyncio
async def test_remote_task_rejects_unknown_host_and_missing_command():
    registry = HandlerRegistry()
    registry._register_remote_handlers()
    unknown = await registry.call("remote_task", {"host": "203.0.113.9", "command": "true"})
    missing = await registry.call("remote_task", {"host": "zombie-pc"})
    assert "Unknown host" in unknown["error"]
    assert missing["error"] == "command parameter required"
