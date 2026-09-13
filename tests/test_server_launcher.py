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
