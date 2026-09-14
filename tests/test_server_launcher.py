from __future__ import annotations

from pathlib import Path

from app.server_launcher import build_server_process


def test_launcher_exports_file_values_for_legacy_getenv_consumers(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text(
        "TRIFORCE_BIND_HOST=127.0.0.1\n"
        "TRIFORCE_API_PORT=19121\n"
        "MCP_OAUTH_USER=local-admin\n"
        "MCP_OAUTH_PASS=file-secret\n"
    )
    argv, env = build_server_process(config_path=cfg, environ={"PATH": "/usr/bin"}, python="/x/python")
    assert env["MCP_OAUTH_USER"] == "local-admin"
    assert env["MCP_OAUTH_PASS"] == "file-secret"
    assert argv[argv.index("--port") + 1] == "19121"


def test_process_environment_remains_highest_priority(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_API_PORT=19121\nMCP_OAUTH_USER=file-user\n")
    argv, env = build_server_process(
        config_path=cfg,
        environ={"TRIFORCE_API_PORT": "19122", "MCP_OAUTH_USER": "override-user"},
        python="/x/python",
    )
    assert argv[argv.index("--port") + 1] == "19122"
    assert env["MCP_OAUTH_USER"] == "override-user"


def test_launcher_argv_contains_no_secret_values(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("MCP_OAUTH_PASS=very-secret-value\n")
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    assert "very-secret-value" not in " ".join(argv)


def test_launcher_pins_websocket_liveness_defaults(tmp_path: Path):
    """Uvicorn's own 20s pong deadline must never decide executor liveness."""
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_API_PORT=19121\n")
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    interval = float(argv[argv.index("--ws-ping-interval") + 1])
    timeout = float(argv[argv.index("--ws-ping-timeout") + 1])
    assert interval == 20.0
    assert timeout == 240.0
    # The transport must outlive a throttled mobile tab but stay below the
    # workspace heartbeat, which owns logical executor liveness.
    assert interval < timeout < 300.0


def test_launcher_websocket_liveness_is_configurable(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_WS_PING_INTERVAL=25\nTRIFORCE_WS_PING_TIMEOUT=180\n")
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    assert float(argv[argv.index("--ws-ping-interval") + 1]) == 25.0
    assert float(argv[argv.index("--ws-ping-timeout") + 1]) == 180.0


def test_launcher_bounds_graceful_shutdown_below_systemd_outer_timeout(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_API_PORT=19121\n")
    monkeypatch.delenv("TRIFORCE_GRACEFUL_SHUTDOWN_TIMEOUT", raising=False)
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    timeout = argv[argv.index("--timeout-graceful-shutdown") + 1]
    assert timeout == "5"
    # systemd TimeoutStopSec is 45s; Uvicorn must finish before the outer kill.
    assert int(timeout) < 45


def test_launcher_graceful_shutdown_is_configurable(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_GRACEFUL_SHUTDOWN_TIMEOUT=7\n")
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    assert argv[argv.index("--timeout-graceful-shutdown") + 1] == "7"


def test_launcher_rejects_fractional_graceful_shutdown(tmp_path: Path):
    import pytest
    from pydantic import ValidationError

    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_GRACEFUL_SHUTDOWN_TIMEOUT=5.5\n")
    with pytest.raises(ValidationError):
        build_server_process(config_path=cfg, environ={}, python="/x/python")


def test_launcher_trusts_only_configured_forwarding_proxy(tmp_path: Path):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("TRIFORCE_FORWARDED_ALLOW_IPS=172.18.0.10\n")
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    assert "--proxy-headers" in argv
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "172.18.0.10"


def test_launcher_does_not_trust_arbitrary_forwarding_peers_by_default(tmp_path: Path, monkeypatch):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("")
    # BaseSettings also consults the real process environment. Remove the
    # production proxy override so this assertion exercises the schema default.
    monkeypatch.delenv("TRIFORCE_FORWARDED_ALLOW_IPS", raising=False)
    argv, _env = build_server_process(config_path=cfg, environ={}, python="/x/python")
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "127.0.0.1"


def test_log_collector_does_not_treat_uvicorn_error_logger_name_as_error(tmp_path: Path):
    from logging.handlers import RotatingFileHandler
    from app.utils.system_log_collector import SystemLogCollector

    collector = SystemLogCollector()
    target = tmp_path / "collector-error.log"
    collector._error_handler = RotatingFileHandler(target, encoding="utf-8")
    collector._extract_and_log_errors(
        "triforce",
        "2026-09-14 18:00:00 | INFO | uvicorn.error | Application startup complete.",
    )
    collector._error_handler.flush()
    assert target.read_text() == ""


def test_log_collector_keeps_explicit_error_levels(tmp_path: Path):
    from logging.handlers import RotatingFileHandler
    from app.utils.system_log_collector import SystemLogCollector

    collector = SystemLogCollector()
    target = tmp_path / "collector-error.log"
    collector._error_handler = RotatingFileHandler(target, encoding="utf-8")
    collector._extract_and_log_errors(
        "triforce",
        "2026-09-14 18:00:00 | ERROR | uvicorn.error | real failure",
    )
    collector._error_handler.flush()
    assert "real failure" in target.read_text()


def test_log_collector_understands_pretty_unicode_levels(tmp_path: Path):
    from logging.handlers import RotatingFileHandler
    from app.utils.system_log_collector import SystemLogCollector

    collector = SystemLogCollector()
    target = tmp_path / "collector-pretty.log"
    collector._error_handler = RotatingFileHandler(target, encoding="utf-8")
    collector._extract_and_log_errors(
        "boot",
        "2026-09-14 19:15:30.436 │ INF │ UVICORN │ uvicorn.error │ Started server process",
    )
    collector._extract_and_log_errors(
        "boot",
        "2026-09-14 19:15:31.436 │ ERR │ UVICORN │ uvicorn.error │ real failure",
    )
    collector._error_handler.flush()
    content = target.read_text()
    assert "Started server process" not in content
    assert "real failure" in content


def _load_nova_log_monitor_for_test():
    import importlib.util
    module_path = Path(__file__).parents[1] / "scripts" / "tools" / "nova_log_monitor.py"
    spec = importlib.util.spec_from_file_location("nova_log_monitor_regression", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_nova_monitor_pretty_info_logger_name_does_not_trigger_error():
    monitor = _load_nova_log_monitor_for_test()
    line = "2026-09-14 19:15:30.436 │ INF │ UVICORN │ uvicorn.error │ Started server process"
    assert monitor.classify_line(line) is None


def test_nova_monitor_pretty_uvicorn_error_remains_actionable():
    monitor = _load_nova_log_monitor_for_test()
    line = "2026-09-14 19:15:30.436 │ ERR │ UVICORN │ uvicorn.error │ timeout graceful shutdown exceeded"
    assert monitor.classify_line(line) == "error"


def test_log_collector_does_not_infer_errors_from_unstructured_journal_payload(tmp_path: Path):
    from logging.handlers import RotatingFileHandler
    from app.utils.system_log_collector import SystemLogCollector

    collector = SystemLogCollector()
    target = tmp_path / "collector-journal.log"
    collector._error_handler = RotatingFileHandler(target, encoding="utf-8")
    collector._extract_and_log_errors(
        "systemd",
        "2026-09-14T19:19:02+02:00 sudo[1]: COMMAND=/usr/bin/systemctl reset-failed motd-news.service",
    )
    collector._error_handler.flush()
    assert target.read_text() == ""
