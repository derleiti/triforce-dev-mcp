import asyncio
from unittest.mock import patch
from app.mcp.handlers_v4 import HandlerRegistry


def test_v4_registers_agent_chat_handlers():
    registry = HandlerRegistry(); registry.initialize()
    for name in ("agent_chat_list", "agent_chat_read", "agent_chat_stream", "agent_chat_summary", "agent_chat_cleanup"):
        assert registry.get(name) is not None


def test_v4_agent_chat_list_dispatches():
    registry = HandlerRegistry(); registry.initialize()
    fake_logger = type("FakeLogger", (), {"list_sessions": lambda self: []})()
    with patch("app.services.agent_chat_logger.get_chat_logger", return_value=fake_logger):
        result = asyncio.run(registry.call("agent_chat_list", {}))
    assert result["sessions"] == []
