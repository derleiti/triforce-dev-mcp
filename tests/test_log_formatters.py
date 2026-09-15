from __future__ import annotations

import logging

from app.utils.log_formatters import TriForceConsoleFormatter, log_category


def _record(name: str, level: int, message: str, args=()):
    return logging.LogRecord(name, level, __file__, 42, message, args, None)


def test_subsystem_categories_are_stable():
    assert log_category('uvicorn.access') == 'HTTP'
    assert log_category('uvicorn.error') == 'UVICORN'
    assert log_category('ailinux.mcp.tools') == 'MCP'
    assert log_category('ailinux.security') == 'SECURITY'
    assert log_category('ailinux.agents.worker') == 'AGENT'


def test_plain_console_format_is_structured_and_has_source():
    formatter = TriForceConsoleFormatter(use_color=False, include_source=True)
    text = formatter.format(_record('ailinux.mcp.tools', logging.WARNING, 'pair lease expired'))
    assert '│ WRN │ MCP' in text
    assert 'mcp.tools' in text
    assert 'pid=' in text
    assert 'pair lease expired' in text
    assert 'test_log_formatters.py:42' in text
    assert '\x1b[' not in text


def test_uvicorn_access_format_exposes_method_path_status_and_client():
    formatter = TriForceConsoleFormatter(use_color=False, include_source=True)
    record = _record(
        'uvicorn.access', logging.INFO,
        '%s - "%s %s HTTP/%s" %d',
        ('10.10.0.6:48123', 'POST', '/v1/mcp', '1.1', 200),
    )
    text = formatter.format(record)
    assert '│ INF │ HTTP' in text
    assert '10.10.0.6:48123' in text
    assert 'POST' in text
    assert '/v1/mcp HTTP/1.1' in text
    assert 'status=200' in text
    # Access lines are already structured and should not add Python source noise.
    assert 'test_log_formatters.py:42' not in text


def test_console_format_can_colour_severity_category_and_status():
    formatter = TriForceConsoleFormatter(use_color=True, include_source=False)
    record = _record(
        'uvicorn.access', logging.ERROR,
        '%s - "%s %s HTTP/%s" %d',
        ('127.0.0.1:1', 'DELETE', '/broken', '1.1', 503),
    )
    text = formatter.format(record)
    assert '\x1b[' in text
    assert 'DELETE' in text
    assert 'status=' in text
    assert '503' in text


def test_console_redacts_workspace_credentials_and_tokens():
    formatter = TriForceConsoleFormatter(use_color=False, include_source=False)
    record = _record(
        'uvicorn.error', logging.INFO,
        'WebSocket /v1/mcp/node/connect?mode=workspace&pair_code=ABCD-EFGH&machine_id=android Authorization: Bearer topsecret',
    )
    text = formatter.format(record)
    assert 'ABCD-EFGH' not in text
    assert 'topsecret' not in text
    assert 'pair_code=[REDACTED]' in text
    assert 'Bearer [REDACTED]' in text


def test_access_formatter_redacts_sensitive_query_parameters():
    formatter = TriForceConsoleFormatter(use_color=False, include_source=False)
    record = _record(
        'uvicorn.access', logging.INFO,
        '%s - "%s %s HTTP/%s" %d',
        ('172.18.0.10:1234', 'GET', '/connect?handoff_code=SECRET-CODE&x=1', '1.1', 200),
    )
    text = formatter.format(record)
    assert 'SECRET-CODE' not in text
    assert 'handoff_code=[REDACTED]' in text


def test_structured_failure_context_is_visible_without_arbitrary_extra_dump():
    formatter = TriForceConsoleFormatter(use_color=False, include_source=False)
    record = _record('ailinux.memory', logging.WARNING, 'memory_degraded')
    record.failure_type = 'provider_timeout'
    record.provider = 'episodic'
    record.fallback = 'cache'
    text = formatter.format(record)
    assert 'failure=provider_timeout' in text
    assert 'provider=episodic' in text
    assert 'fallback=cache' in text


def test_plain_file_formatter_redacts_secrets():
    from app.utils.log_formatters import RedactingFormatter
    formatter = RedactingFormatter('%(message)s')
    record = _record('ailinux.mcp', logging.INFO, "payload={'resume_token': 'dont-log-me', 'ok': True}")
    text = formatter.format(record)
    assert 'dont-log-me' not in text
    assert '[REDACTED]' in text


def test_structured_log_sanitizer_recurses_and_hides_workspace_context():
    from app.utils.log_formatters import sanitize_log_data

    source = {
        "path": "README.md",
        "workspace_token": "workspace-token-must-not-leak",
        "workspace_context": "private-chat-affinity",
        "nested": {
            "Authorization": "Bearer nested-secret",
            "items": [{"api_key": "nested-api-secret", "keep": "visible"}],
        },
        "_meta": {
            "openai/userAgent": "ChatGPT/test",
            "openai/locale": "de-DE",
            "openai/subject": "private-subject",
            "openai/session": "private-session",
            "openai/organization": "private-org",
            "openai/userLocation": {"latitude": 49.0, "longitude": 12.0},
        },
    }
    safe = sanitize_log_data(source)

    assert safe["path"] == "README.md"
    assert safe["workspace_token"] == "[REDACTED]"
    assert safe["workspace_context"] == "[REDACTED]"
    assert safe["nested"]["Authorization"] == "[REDACTED]"
    assert safe["nested"]["items"][0]["api_key"] == "[REDACTED]"
    assert safe["nested"]["items"][0]["keep"] == "visible"
    assert safe["_meta"]["openai/userAgent"] == "ChatGPT/test"
    assert safe["_meta"]["openai/locale"] == "de-DE"
    assert safe["_meta"]["openai/subject"] == "[REDACTED]"
    assert safe["_meta"]["openai/session"] == "[REDACTED]"
    assert safe["_meta"]["openai/organization"] == "[REDACTED]"
    assert safe["_meta"]["openai/userLocation"] == "[REDACTED]"
    assert source["workspace_token"] == "workspace-token-must-not-leak"


def test_multifile_mcp_log_never_persists_workspace_credentials(tmp_path):
    import asyncio
    import json

    from app.utils.triforce_logging import MultiFileLogger

    logger = MultiFileLogger(log_dir=str(tmp_path))
    asyncio.run(
        logger.log_mcp(
            "tools/call:workspace_status",
            {
                "workspace_token": "SENTINEL_TOKEN_DO_NOT_LOG",
                "workspace_context": "SENTINEL_CONTEXT_DO_NOT_LOG",
                "path": "README.md",
            },
            {"ok": True},
            1.25,
        )
    )
    log_path = logger._get_dated_path("mcp", "mcpserver")
    raw = log_path.read_text(encoding="utf-8")
    record = json.loads(raw.strip())

    assert "SENTINEL_TOKEN_DO_NOT_LOG" not in raw
    assert "SENTINEL_CONTEXT_DO_NOT_LOG" not in raw
    assert record["params"]["workspace_token"] == "[REDACTED]"
    assert record["params"]["workspace_context"] == "[REDACTED]"
    assert record["params"]["path"] == "README.md"


def test_multifile_writer_redacts_result_preview_and_error_text(tmp_path):
    import asyncio

    from app.utils.triforce_logging import MultiFileLogger

    logger = MultiFileLogger(log_dir=str(tmp_path))
    asyncio.run(
        logger.log_mcp_tool_call(
            tool_name="workspace_pair",
            params={"action": "claim"},
            result_status="success",
            latency_ms=2.0,
            result_preview="{'workspace_token': 'RESULT_SECRET_SENTINEL', 'ok': True}",
            error="workspace_context=CONTEXT_SECRET_SENTINEL",
        )
    )
    raw = logger._get_dated_path("mcp", "mcp_calls").read_text(encoding="utf-8")

    assert "RESULT_SECRET_SENTINEL" not in raw
    assert "CONTEXT_SECRET_SENTINEL" not in raw
    assert "[REDACTED]" in raw



def test_redact_sensitive_hides_legacy_session_capabilities():
    from app.utils.log_formatters import redact_sensitive

    for raw in (
        "SSE_CONNECT | Session: SESSION_CAPABILITY_SENTINEL | ok",
        "Mcp-Session-Id: SESSION_HEADER_SENTINEL",
        "/v1/mcp/messages?session_id=SESSION_QUERY_SENTINEL",
    ):
        safe = redact_sensitive(raw)
        assert "SENTINEL" not in safe
        assert "[REDACTED]" in safe


def test_central_log_entry_serialization_is_defense_in_depth_safe():
    import json

    from app.utils.triforce_logging import LogCategory, LogLevel, TriForceLogEntry

    entry = TriForceLogEntry(
        timestamp="2026-09-14T21:00:00+00:00",
        trace_id="trace-safe",
        category=LogCategory.ERROR,
        level=LogLevel.ERROR,
        source="regression-test",
        message="workspace_token=MESSAGE_SECRET_SENTINEL",
        session_id="SESSION_SECRET_SENTINEL",
        tool_params={"nested": {"api_key": "PARAM_SECRET_SENTINEL"}},
        error_message="resume_token=ERROR_SECRET_SENTINEL",
        stack_trace="Authorization: Bearer STACK_SECRET_SENTINEL",
        metadata={"workspace_context": "META_SECRET_SENTINEL"},
    )
    payload = entry.to_dict()
    raw = json.dumps(payload)

    for sentinel in (
        "MESSAGE_SECRET_SENTINEL",
        "SESSION_SECRET_SENTINEL",
        "PARAM_SECRET_SENTINEL",
        "ERROR_SECRET_SENTINEL",
        "STACK_SECRET_SENTINEL",
        "META_SECRET_SENTINEL",
    ):
        assert sentinel not in raw
    assert payload["session_id"] == "[REDACTED]"
    assert payload["tool_params"]["nested"]["api_key"] == "[REDACTED]"
    assert payload["metadata"]["workspace_context"] == "[REDACTED]"


def test_audit_entry_serialization_redacts_params_metadata_errors_and_session():
    import json

    from app.services.triforce.audit_logger import AuditEntry, AuditLevel

    entry = AuditEntry(
        timestamp="2026-09-14T21:00:00Z",
        trace_id="trace-audit",
        session_id="AUDIT_SESSION_SENTINEL",
        llm_id="tester",
        action="tool_call",
        level=AuditLevel.ERROR,
        params={"workspace_token": "AUDIT_PARAM_SENTINEL"},
        error_message="password=AUDIT_ERROR_SENTINEL",
        metadata={"workspace_context": "AUDIT_META_SENTINEL"},
    )
    payload = entry.to_dict()
    raw = json.dumps(payload)

    for sentinel in (
        "AUDIT_SESSION_SENTINEL",
        "AUDIT_PARAM_SENTINEL",
        "AUDIT_ERROR_SENTINEL",
        "AUDIT_META_SENTINEL",
    ):
        assert sentinel not in raw
    assert payload["session_id"] == "[REDACTED]"
