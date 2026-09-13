from app.services import mcp_workspace_bridge as bridge


def test_helper_device_tools_are_local_workspace_capabilities():
    assert {"computer_observe", "computer_screenshot", "clipboard_read", "clipboard_write"} <= bridge.LOCAL_TOOL_NAMES


def test_helper_device_schemas_are_exposed():
    schemas = {item["name"]: item for item in bridge._TOOL_SCHEMAS}
    assert schemas["computer_screenshot"]["annotations"]["readOnlyHint"] is True
    assert schemas["clipboard_read"]["annotations"]["readOnlyHint"] is True
    assert schemas["clipboard_write"]["annotations"]["readOnlyHint"] is False
    assert schemas["clipboard_write"]["inputSchema"]["required"] == ["text"]


def test_device_consent_is_separate_from_workspace_write_mode():
    assert "clipboard_write" in bridge.DEVICE_WRITE_TOOLS
    assert "clipboard_write" not in bridge.BROWSER_WRITE_TOOLS
    assert bridge.workspace_tool_requires_write("clipboard_write", {"text": "x"}) is False
