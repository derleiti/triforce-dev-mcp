from app.services import mcp_workspace_bridge as bridge


def test_helper_device_tools_are_local_workspace_capabilities():
    assert {"computer_observe", "computer_screenshot", "clipboard_read", "clipboard_write"} <= bridge.LOCAL_TOOL_NAMES


def test_helper_device_schemas_are_exposed():
    # Schemas now come from the canonical registry via the workspace contract,
    # not from a module-level _TOOL_SCHEMAS list.
    schemas = {item["name"]: item for item in bridge._workspace_contract_base_tools()}
    assert schemas["aihelper_screenshot"]["annotations"]["readOnlyHint"] is True
    assert schemas["aihelper_clipboard_read"]["annotations"]["readOnlyHint"] is True
    assert schemas["aihelper_clipboard_write"]["annotations"]["readOnlyHint"] is False
    assert schemas["aihelper_clipboard_write"]["inputSchema"]["required"] == ["text"]


def test_device_consent_is_separate_from_workspace_write_mode():
    assert "clipboard_write" in bridge.DEVICE_WRITE_TOOLS
    assert "clipboard_write" not in bridge.BROWSER_WRITE_TOOLS
    assert bridge.workspace_tool_requires_write("clipboard_write", {"text": "x"}) is False


def test_global_computer_input_schema_covers_desktop_and_android_actions():
    schemas = {item["name"]: item for item in bridge._workspace_contract_base_tools()}
    schema = schemas["aihelper_input"]["inputSchema"]
    actions = set(schema["properties"]["action"]["enum"])
    assert {"click", "double_click", "move", "scroll", "type", "key"} <= actions
    assert {"tap", "long_press", "swipe", "wake", "wake_screen", "back", "home", "recents", "notifications"} <= actions
    assert {"x1", "y1", "x2", "y2", "duration_ms", "observe_after", "observe_wait_ms"} <= set(schema["properties"])
    assert schema["properties"]["observe_after"]["default"] is False
    assert schema["properties"]["observe_wait_ms"]["maximum"] == 500
    assert schemas["aihelper_input"]["annotations"]["readOnlyHint"] is False
