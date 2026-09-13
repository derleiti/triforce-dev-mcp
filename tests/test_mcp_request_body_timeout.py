from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.routes import mcp


class _SlowRequest:
    def __init__(self) -> None:
        self.state = SimpleNamespace(trace_id="trace-timeout-test")

    async def json(self):
        await asyncio.sleep(0.05)
        return {"jsonrpc": "2.0"}


class _FastRequest:
    def __init__(self) -> None:
        self.state = SimpleNamespace(trace_id="trace-fast-test")

    async def json(self):
        return {"jsonrpc": "2.0", "method": "initialize"}


@pytest.mark.asyncio
async def test_mcp_request_body_read_has_bounded_timeout(monkeypatch):
    monkeypatch.setattr(mcp, "MCP_REQUEST_BODY_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(asyncio.TimeoutError):
        await mcp._read_mcp_json_body(_SlowRequest(), mcp.logging.getLogger("test"), "session-test")


@pytest.mark.asyncio
async def test_mcp_request_body_read_accepts_complete_json(monkeypatch):
    monkeypatch.setattr(mcp, "MCP_REQUEST_BODY_TIMEOUT_SECONDS", 0.5)
    body = await mcp._read_mcp_json_body(_FastRequest(), mcp.logging.getLogger("test"), "session-test")
    assert body["method"] == "initialize"
