from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_antigravity_wrapper_uses_current_cli_and_global_mcp_config():
    text = (ROOT / "triforce/bin/agy-triforce").read_text()
    assert "/home/zombie/.local/bin/agy" in text
    assert "/home/zombie/.gemini/config/mcp_config.json" in text
    assert "MCP_OAUTH_USER" not in text
    assert "MCP_OAUTH_PASS" not in text
    assert "serverUrl" not in text
    assert "Authorization" not in text
    assert "does not" in text
    assert "--dangerously-skip-permissions" in text


def test_legacy_gemini_wrapper_is_only_a_compatibility_shim():
    text = (ROOT / "triforce/bin/gemini-triforce").read_text()
    assert "agy-triforce" in text
    assert "exec " in text
    assert "@google/gemini-cli" not in text


def test_agent_controller_calls_antigravity_headlessly():
    text = (ROOT / "app/services/tristar/agent_controller.py").read_text()
    assert '"agent_id": "gemini-mcp"' in text  # public compatibility ID
    assert 'f"{TRIFORCE_BIN}/agy-triforce"' in text
    assert "agy-triforce --output-format text --print-timeout" in text
    assert "--print {safe_msg}" in text


def test_legacy_gemini_runtime_paths_use_antigravity_wrapper():
    task_spawner = (ROOT / "app/services/task_spawner.py").read_text()
    remote_task = (ROOT / "app/services/remote_task.py").read_text()
    assert '"cmd": ["gemini"]' not in task_spawner
    assert '["gemini", "-p", prompt]' not in remote_task
    assert "agy-triforce" in task_spawner
    assert "agy-triforce" in remote_task


def test_persisted_builtin_agent_is_migrated_to_antigravity():
    text = (ROOT / "app/services/tristar/agent_controller.py").read_text()
    assert 'agent_id == "gemini-mcp"' in text
    assert 'command == [f"{TRIFORCE_BIN}/gemini-triforce"]' in text
    assert 'command = [f"{TRIFORCE_BIN}/agy-triforce"]' in text


def test_antigravity_wrapper_is_explicitly_whitelisted():
    text = (ROOT / "app/services/tristar/agent_controller.py").read_text()
    assert '"/home/zombie/workspace/triforce/triforce/bin/agy-triforce"' in text
