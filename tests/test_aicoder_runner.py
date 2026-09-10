import asyncio
import json
from pathlib import Path

from app.services.aicoder_runner import AICoderRunner, parse_ndjson, prepare_instance_home


def test_parse_ndjson_keeps_structured_events_and_diagnostics():
    events, raw = parse_ndjson([
        json.dumps({"type": "run_start", "run_id": "r1"}),
        "diagnostic text",
        json.dumps({"type": "result", "status": "completed", "response": "OK"}),
    ])
    assert [item["type"] for item in events] == ["run_start", "result"]
    assert raw == ["diagnostic text"]


def test_prepare_instance_home_isolates_aicoder_state_and_links_provider_login(tmp_path: Path):
    source = tmp_path / "source"
    (source / ".config" / "ai-coder").mkdir(parents=True)
    (source / ".config" / "ai-coder" / "session.json").write_text('{"token":"secret"}')
    (source / ".config" / "ai-coder" / "state.json").write_text('{"selected_model":"x"}')
    (source / ".codex").mkdir()
    (source / ".npm-global").mkdir()
    (source / ".local" / "bin").mkdir(parents=True)
    (source / ".local" / "share" / "uv" / "tools").mkdir(parents=True)

    home = prepare_instance_home("pilot", source_home=source, instance_root=tmp_path / "instances")

    assert (home / ".codex").is_symlink()
    assert (home / ".codex").resolve() == (source / ".codex").resolve()
    assert (home / ".npm-global").is_symlink()
    assert (home / ".local" / "bin").is_symlink()
    assert (home / ".local" / "share" / "uv" / "tools").is_symlink()
    assert (home / ".config" / "ai-coder" / "session.json").read_text() == '{"token":"secret"}'
    assert not (home / ".config" / "ai-coder" / "state.json").is_symlink()
    assert (home / ".config" / "aicoder").is_dir()


def test_runner_normalizes_completed_result(tmp_path: Path):
    fake = tmp_path / "aicoder"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "print(json.dumps({'type':'run_start','run_id':'r1','model':'m'}))\n"
        "print(json.dumps({'type':'final','response':'OK','model':'m'}))\n"
        "print(json.dumps({'type':'run_terminal','run_id':'r1','status':'completed','plan_id':'p1'}))\n"
        "print(json.dumps({'type':'result','status':'completed','response':'OK','model':'m','plan_id':'p1','error':''}))\n"
    )
    fake.chmod(0o755)
    workspace = tmp_path / "ws"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    result = asyncio.run(AICoderRunner(str(fake)).run(
        profile_id="pilot", prompt="test", workspace=workspace, home=home, timeout=5,
    ))
    assert result.status == "success"
    assert result.response == "OK"
    assert result.run_id == "r1"
    assert result.plan_id == "p1"


def test_runner_timeout_kills_process_group(tmp_path: Path):
    fake = tmp_path / "aicoder"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    workspace = tmp_path / "ws"
    home = tmp_path / "home"
    workspace.mkdir()
    home.mkdir()

    result = asyncio.run(AICoderRunner(str(fake)).run(
        profile_id="pilot", prompt="test", workspace=workspace, home=home, timeout=1,
    ))
    assert result.status == "timeout"


def test_apply_profile_state_updates_only_profile_owned_keys(tmp_path: Path):
    from app.services.aicoder_runner import apply_profile_state
    home = tmp_path / "home"
    cfg = home / ".config" / "ai-coder"
    cfg.mkdir(parents=True)
    (cfg / "state.json").write_text(json.dumps({"keep_me": 1, "approval_mode": "ask"}))
    apply_profile_state(home, {
        "model": "mistral/codestral-latest",
        "approval_mode": "autopilot",
        "enabled_tools": ["code_read"],
        "workspace": "/tmp/project",
        "timeout": 90,
        "team_mode": "off",
    })
    state = json.loads((cfg / "state.json").read_text())
    assert state["keep_me"] == 1
    assert state["selected_model"] == "mistral/codestral-latest"
    assert state["approval_mode"] == "autopilot"
    assert state["enabled_tools"] == ["code_read"]
    assert state["workspace_root"] == "/tmp/project"
    assert state["request_timeout"] == 90
    assert state["team_runtime_mode"] == "off"


def test_public_result_is_compact_but_events_remain_available():
    from app.services.aicoder_runner import AICoderRunResult
    result = AICoderRunResult(
        profile_id="pilot", status="success", response="OK", run_id="r1",
        events=[
            {"type": "tool_call", "name": "file_read"},
            {"type": "tool_result", "name": "file_read", "is_error": False},
            {"type": "performance_summary", "wall_ms": 123, "model_requests": 2},
            {"type": "run_terminal", "elapsed_ms": 123},
        ],
    )
    public = result.to_dict()
    assert "events" not in public
    assert public["event_summary"] == {
        "event_count": 4, "tool_calls": ["file_read"], "tool_call_count": 1,
        "tool_error_count": 0, "elapsed_ms": 123, "model_requests": 2,
    }
    assert result.to_dict(include_events=True)["events"] == result.events


def test_claude_account_run_strips_api_billing_env(tmp_path, monkeypatch):
    fake = tmp_path / "aicoder"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "bad = any(os.environ.get(k) for k in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','ANTHROPIC_BASE_URL'))\n"
        "print(json.dumps({'type':'result','status':'completed' if not bad else 'failed','response':'OK' if not bad else '', 'model':'account:claude/sonnet','error':'' if not bad else 'leaked'}))\n"
    )
    fake.chmod(0o755)
    ws = tmp_path / "ws"; home = tmp_path / "home"; ws.mkdir(); home.mkdir()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.invalid")
    result = asyncio.run(AICoderRunner(str(fake)).run(profile_id="pilot", prompt="x", workspace=ws, home=home, timeout=5, model="account:claude/sonnet"))
    assert result.status == "success"
    assert result.response == "OK"
