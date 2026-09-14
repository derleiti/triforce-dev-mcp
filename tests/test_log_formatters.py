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
