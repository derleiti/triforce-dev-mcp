import asyncio
from unittest.mock import AsyncMock, patch

from app.mcp import handlers_v4
from app.utils.unified_logger import tool_result_error


def test_tool_result_error_detects_dict_and_json_failures():
    assert tool_result_error({"error": "boom"}) == "boom"
    assert tool_result_error('{"error":"missing browser","url":"https://example.test"}') == "missing browser"
    assert tool_result_error({"error": {"message": "quota exhausted"}}) == "quota exhausted"


def test_tool_result_error_ignores_success_and_plain_text():
    assert tool_result_error({"ok": True}) is None
    assert tool_result_error('{"status":"loaded"}') is None
    assert tool_result_error("normal result") is None


def test_call_tool_logs_structured_handler_failure_as_error():
    result = '{"error":"browser executable missing"}'
    with (
        patch.object(handlers_v4.handler_registry, "call", new=AsyncMock(return_value=result)),
        patch.object(handlers_v4.logger, "error") as log_error,
        patch.object(handlers_v4.logger, "info") as log_info,
        patch("app.utils.unified_logger.log_tool_call"),
    ):
        returned = asyncio.run(handlers_v4.call_tool("browser_navigate", {"url": "https://example.test"}))
    assert returned == result
    assert any("TOOL_CALL_RESULT_ERROR | browser_navigate" in call.args[0] for call in log_error.call_args_list)
    assert not any("TOOL_CALL_OK | browser_navigate" in call.args[0] for call in log_info.call_args_list)
