from app.mcp.compatibility import (
    build_discover_result,
    build_initialize_result,
    detect_profile,
    is_modern_request,
    logical_session_id,
    negotiate_protocol_version,
    stamp_modern_result_meta,
)


def test_protocol_negotiation_accepts_supported_versions():
    assert negotiate_protocol_version("2024-11-05") == "2024-11-05"
    assert negotiate_protocol_version("2025-03-26") == "2025-03-26"
    assert negotiate_protocol_version("2025-06-18") == "2025-06-18"
    assert negotiate_protocol_version("2025-11-25") == "2025-11-25"


def test_protocol_negotiation_falls_back_to_current_default():
    assert negotiate_protocol_version("2099-01-01") == "2025-11-25"
    assert negotiate_protocol_version("2026-07-28") == "2025-11-25"
    assert negotiate_protocol_version(None) == "2024-11-05"


def test_provider_detection_is_advisory_and_generic_by_default():
    assert detect_profile({"name": "unknown-client"}, {}).name == "generic"
    assert detect_profile({"name": "Claude Desktop"}, {}).name == "anthropic"
    assert detect_profile({"name": "Gemini"}, {}).name == "google"
    assert detect_profile({"name": "Grok Bot"}, {}).name == "xai"
    assert detect_profile({"name": "Codex"}, {}).name == "openai"


def test_openai_headers_enable_session_fallback_without_exposing_raw_ids():
    headers = {
        "user-agent": "openai-mcp/1.0",
        "x-openai-session": "session-secret",
        "x-openai-subject": "subject-secret",
    }
    sid = logical_session_id(explicit_session_id=None, headers=headers)
    assert sid.startswith("openai-")
    assert "session-secret" not in sid
    assert "subject-secret" not in sid


def test_explicit_session_id_always_wins():
    sid = logical_session_id(
        explicit_session_id="mcp-explicit",
        headers={
            "x-openai-session": "s",
            "x-openai-subject": "u",
        },
    )
    assert sid == "mcp-explicit"


def test_initialize_shape_is_consistent():
    result = build_initialize_result(
        {"protocolVersion": "2025-03-26", "clientInfo": {"name": "Gemini"}},
        server_name="ailinux-mcp-server",
        server_version="test",
        instructions="hello",
    )
    assert result["protocolVersion"] == "2025-03-26"
    assert result["serverInfo"]["name"] == "ailinux-mcp-server"
    assert result["capabilities"]["tools"]["listChanged"] is True
    assert result["capabilities"]["prompts"]["listChanged"] is True
    assert result["capabilities"]["resources"]["listChanged"] is True


def test_compatibility_profiles_do_not_contain_authorization_flags():
    for name in ("generic", "openai", "anthropic", "google", "xai"):
        profile = detect_profile({"name": name}, {})
        keys = set(profile.__dataclass_fields__)
        assert "allowed_tools" not in keys
        assert "full_access" not in keys
        assert "role" not in keys
        assert "scope" not in keys


def test_initialize_can_preserve_legacy_tool_capability_hints():
    result = build_initialize_result(
        {"protocolVersion": "2024-11-05"},
        server_name="ailinux-mcp-server",
        server_version="test",
        instructions="hello",
        tool_capabilities_extra={"tristar": True, "memory": True, "mesh": True},
    )
    assert result["capabilities"]["tools"] == {
        "listChanged": True,
        "tristar": True,
        "memory": True,
        "mesh": True,
    }


def test_provider_transport_profiles_match_known_compatibility_constraints():
    assert detect_profile({"name": "Gemini"}, {}).streamable_http is True
    assert detect_profile({"name": "Gemini"}, {}).legacy_sse is False
    assert detect_profile({"name": "Grok"}, {}).streamable_http is True
    assert detect_profile({"name": "Grok"}, {}).legacy_sse is True
    assert detect_profile({"name": "unknown"}, {}).legacy_sse is True


def test_modern_discover_advertises_both_protocol_eras():
    result = build_discover_result(
        server_name="ailinux-mcp-server",
        server_version="test",
        instructions="hello",
    )
    assert "2024-11-05" in result["supportedVersions"]
    assert "2025-11-25" in result["supportedVersions"]
    assert "2026-07-28" in result["supportedVersions"]
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "ailinux-mcp-server"


def test_modern_request_detection_accepts_header_or_meta():
    assert is_modern_request(
        {"jsonrpc":"2.0","method":"tools/list","params":{}},
        {"MCP-Protocol-Version":"2026-07-28"},
    )
    assert is_modern_request(
        {
            "jsonrpc":"2.0",
            "method":"tools/list",
            "params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28"}},
        },
        {},
    )


def test_modern_response_gets_server_identity_without_overwriting_meta():
    response = {"jsonrpc":"2.0","id":1,"result":{"tools":[],"_meta":{"custom":"kept"}}}
    stamped = stamp_modern_result_meta(
        response,
        server_name="ailinux-mcp-server",
        server_version="test",
    )
    assert stamped["result"]["_meta"]["custom"] == "kept"
    assert stamped["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["version"] == "test"
