import asyncio
from pathlib import Path
from unittest.mock import patch

from app.services.aicoder_runner import AICoderRunResult
from app.services.tristar.agent_controller import AgentConfig, AgentController, AgentInstance, AgentType


def test_agent_config_runtime_roundtrip_surface():
    cfg = AgentConfig(agent_id="pilot", agent_type=AgentType.AICODER, name="Pilot", command=["/usr/bin/aicoder"], runtime="aicoder")
    assert cfg.to_dict()["runtime"] == "aicoder"


def test_call_agent_routes_aicoder_runtime_without_starting_legacy_process(tmp_path: Path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["pilot"] = AgentInstance(config=AgentConfig(
        agent_id="pilot", agent_type=AgentType.AICODER, name="Pilot", command=["/usr/bin/aicoder"], runtime="aicoder"
    ))

    async def fake_run_profile(agent_id, message, timeout_override=None, team_mode_override=None):
        return AICoderRunResult(profile_id=agent_id, status="success", response="OK", exit_code=0, run_id="r1")

    async def forbidden_start(*args, **kwargs):
        raise AssertionError("legacy start_agent must not be used for AICoder runtime")

    controller.start_agent = forbidden_start
    with patch("app.services.tristar.agent_controller.run_profile", fake_run_profile):
        result = asyncio.run(controller.call_agent("pilot", "hello", timeout=5))
    assert result["status"] == "success"
    assert result["response"] == "OK"
    assert result["run_id"] == "r1"


def test_start_agent_prepares_aicoder_without_legacy_process(tmp_path: Path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["pilot"] = AgentInstance(config=AgentConfig(
        agent_id="pilot", agent_type=AgentType.AICODER, name="Pilot", command=["/usr/bin/aicoder"], runtime="aicoder"
    ))
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    with patch("app.services.tristar.agent_controller.load_profile", return_value={"id": "pilot"}), \
         patch("app.services.tristar.agent_controller.prepare_instance_home", return_value=fake_home), \
         patch("app.services.tristar.agent_controller.apply_profile_state") as apply_state:
        result = asyncio.run(controller.start_agent("pilot"))
    assert result["status"] == "ready"
    assert result["runtime"] == "aicoder"
    assert controller.agents["pilot"].process is None
    assert controller.agents["pilot"].status.value == "ready"
    apply_state.assert_called_once()


def test_stop_agent_does_not_require_process_for_aicoder_runtime(tmp_path: Path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    instance = AgentInstance(config=AgentConfig(
        agent_id="pilot", agent_type=AgentType.AICODER, name="Pilot", command=["/usr/bin/aicoder"], runtime="aicoder"
    ))
    controller.agents["pilot"] = instance
    result = asyncio.run(controller.stop_agent("pilot"))
    assert result["status"] == "stopped"
    assert result["runtime"] == "aicoder"
    assert instance.status.value == "stopped"


def test_same_aicoder_agent_calls_are_serialized(tmp_path):
    import asyncio
    from unittest.mock import patch
    from app.services.aicoder_runner import AICoderRunResult
    from app.services.tristar.agent_controller import AgentController, AgentConfig, AgentInstance, AgentType

    controller = AgentController(str(tmp_path))
    controller._initialized = True
    controller.agents["pilot"] = AgentInstance(AgentConfig(agent_id="pilot", agent_type=AgentType.AICODER, name="Pilot", command=["/usr/bin/aicoder"], runtime="aicoder"))
    active = 0
    peak = 0

    async def fake_run(*args, **kwargs):
        nonlocal active, peak
        active += 1; peak = max(peak, active)
        await asyncio.sleep(0.03)
        active -= 1
        return AICoderRunResult(profile_id="pilot", status="success", response="OK")

    async def main():
        with patch("app.services.tristar.agent_controller.run_profile", side_effect=fake_run), \
             patch("app.services.tristar.agent_controller.record_aicoder_run", return_value={"kind":"","emitted":False,"mailed":False}):
            await asyncio.gather(controller.call_agent("pilot", "one"), controller.call_agent("pilot", "two"))

    asyncio.run(main())
    assert peak == 1


def test_reconcile_adds_missing_standard_agents_and_preserves_custom(tmp_path):
    from app.services.tristar.agent_controller import DEFAULT_AGENTS
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller.agents["custom"] = AgentInstance(config=AgentConfig(
        agent_id="custom", agent_type=AgentType.AICODER, name="Custom", command=["/usr/bin/aicoder"], runtime="aicoder"
    ))
    changed = controller._reconcile_default_agents()
    expected = {row["agent_id"] for row in DEFAULT_AGENTS}
    assert expected.issubset(controller.agents)
    assert expected.issubset(set(changed))
    assert "custom" in controller.agents
    assert all(controller.agents[agent_id].config.runtime == "aicoder" for agent_id in expected)


def test_reconcile_migrates_builtin_runtime_without_overwriting_env(tmp_path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller.agents["claude-mcp"] = AgentInstance(config=AgentConfig(
        agent_id="claude-mcp", agent_type=AgentType.CLAUDE, name="Old", command=["/usr/local/bin/claude"],
        runtime="legacy", env={"PATH": "custom-path"}
    ))
    changed = controller._reconcile_default_agents()
    assert "claude-mcp" in changed
    assert controller.agents["claude-mcp"].config.runtime == "aicoder"
    assert controller.agents["claude-mcp"].config.env["PATH"] == "custom-path"


def test_ensure_builtin_profiles_creates_missing_only(tmp_path):
    import json
    from app.services.tristar.agent_controller import _ensure_builtin_aicoder_profiles
    root = tmp_path / "profiles"; root.mkdir()
    existing = root / "claude-mcp.json"
    existing.write_text(json.dumps({"id":"claude-mcp","model":"custom/model"}))
    created = _ensure_builtin_aicoder_profiles(root, workspace=str(tmp_path))
    assert "claude-mcp" not in created
    assert json.loads(existing.read_text())["model"] == "custom/model"
    assert "mistral-mcp" in created
    mistral = json.loads((root / "mistral-mcp.json").read_text())
    assert mistral["model"] == "mistral/mistral-medium-latest"
    assert mistral["system_prompt_source"] == "triforce"


def test_initialize_reconciles_defaults_and_profiles_in_own_data_dir(tmp_path):
    import json
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    asyncio.run(controller.initialize())
    try:
        assert "mistral-mcp" in controller.agents
        assert controller.agents["mistral-mcp"].config.runtime == "aicoder"
        profile = tmp_path / "agents" / "profiles" / "mistral-mcp.json"
        assert profile.exists()
        assert json.loads(profile.read_text())["model"] == "mistral/mistral-medium-latest"
    finally:
        asyncio.run(controller.shutdown())


def test_native_cli_profile_auto_routes_simple_task_direct_without_aicoder(tmp_path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["gemini-mcp"] = AgentInstance(config=AgentConfig(
        agent_id="gemini-mcp", agent_type=AgentType.GEMINI, name="Gemini",
        command=["/home/zombie/triforce/triforce/bin/agy-triforce"], runtime="aicoder",
        working_dir=str(tmp_path),
    ))

    class FakeProcess:
        returncode = 0
        async def communicate(self):
            return b"DIRECT_OK", b""
        def kill(self):
            pass

    async def fake_create(*args, **kwargs):
        return FakeProcess()

    async def forbidden_run_profile(*args, **kwargs):
        raise AssertionError("simple task must not start AICoder")

    with patch("app.services.tristar.agent_controller.asyncio.create_subprocess_exec", fake_create), \
         patch("app.services.tristar.agent_controller.query_account_provider_quota", return_value={"quota_exhausted": False}), \
         patch("app.services.tristar.agent_controller.run_profile", forbidden_run_profile):
        result = asyncio.run(controller.call_agent("gemini-mcp", "lies README.md", timeout=45))

    assert result["status"] == "success"
    assert result["response"] == "DIRECT_OK"
    assert result["routing"]["mode"] == "direct"


def test_gemini_direct_print_timeout_is_not_reported_as_success(tmp_path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["gemini-mcp"] = AgentInstance(config=AgentConfig(
        agent_id="gemini-mcp", agent_type=AgentType.GEMINI, name="Gemini",
        command=["/home/zombie/triforce/triforce/bin/agy-triforce"], runtime="aicoder",
        working_dir=str(tmp_path),
    ))

    class FakeProcess:
        returncode = 0
        async def communicate(self):
            return b"[agy] print timeout after 40s with turn in progress; returning partial output", b""
        def kill(self):
            pass

    async def fake_create(*args, **kwargs):
        return FakeProcess()

    with patch("app.services.tristar.agent_controller.asyncio.create_subprocess_exec", fake_create), \
         patch("app.services.tristar.agent_controller.query_account_provider_quota", return_value={"quota_exhausted": False}):
        result = asyncio.run(controller.call_agent("gemini-mcp", "lies README.md", timeout=45))

    assert result["status"] == "timeout"
    assert "print timeout after 40s" in result["error"]
    assert result["routing"]["mode"] == "direct"



def test_gemini_direct_quota_preflight_fails_fast_without_starting_provider(tmp_path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["gemini-mcp"] = AgentInstance(config=AgentConfig(
        agent_id="gemini-mcp", agent_type=AgentType.GEMINI, name="Gemini",
        command=["/home/zombie/triforce/triforce/bin/agy-triforce"], runtime="aicoder",
        working_dir=str(tmp_path),
    ))

    async def forbidden_create(*args, **kwargs):
        raise AssertionError("quota-limited provider must not be started")

    quota = {
        "quota_exhausted": True,
        "quota_retry_after_seconds": 3600,
        "quota_reset_at": "2026-09-12T12:00:00+02:00",
    }
    notification = {"kind": "quota_limited", "emitted": True, "mailed": False}
    profile = {"id": "gemini-mcp", "model": "account:gemini/gemini-3.8-flash-high"}

    with patch("app.services.tristar.agent_controller.query_account_provider_quota", return_value=quota), \
         patch("app.services.tristar.agent_controller.load_profile", return_value=profile), \
         patch("app.services.tristar.agent_controller.record_agent_quota_limited", return_value=notification) as record_quota, \
         patch("app.services.tristar.agent_controller.asyncio.create_subprocess_exec", forbidden_create), \
         patch("app.services.model_availability.availability_service.mark_quota_exhausted") as mark_quota:
        result = asyncio.run(controller.call_agent("gemini-mcp", "lies README.md", timeout=45))

    assert result["status"] == "error"
    assert result["error_code"] == "quota_exhausted"
    assert result["provider"] == "gemini"
    assert result["model"] == "account:gemini/gemini-3.8-flash-high"
    assert result["quota_retry_after_seconds"] == 3600
    assert result["quota_reset_at"] == "2026-09-12T12:00:00+02:00"
    assert result["routing"]["mode"] == "direct"
    assert result["notification"]["kind"] == "quota_limited"
    mark_quota.assert_called_once_with(
        "account:gemini/gemini-3.8-flash-high",
        "Gemini/Antigravity quota exhausted until 2026-09-12T12:00:00+02:00",
        retry_after_seconds=3600,
    )
    record_quota.assert_awaited_once()

def test_native_cli_profile_explicit_team_routes_aicoder_team_on(tmp_path):
    controller = AgentController(data_dir=str(tmp_path / "agents"))
    controller._initialized = True
    controller.agents["gemini-mcp"] = AgentInstance(config=AgentConfig(
        agent_id="gemini-mcp", agent_type=AgentType.GEMINI, name="Gemini",
        command=["/home/zombie/triforce/triforce/bin/agy-triforce"], runtime="aicoder",
        working_dir=str(tmp_path),
    ))
    seen = {}

    async def fake_run_profile(agent_id, message, **kwargs):
        seen.update(kwargs)
        return AICoderRunResult(profile_id=agent_id, status="success", response="TEAM_OK", exit_code=0)

    with patch("app.services.tristar.agent_controller.run_profile", fake_run_profile), \
         patch("app.services.tristar.agent_controller.record_aicoder_run", return_value={"kind":"","emitted":False,"mailed":False}):
        result = asyncio.run(controller.call_agent("gemini-mcp", "team run: refaktor das Modul", timeout=45))

    assert result["status"] == "success"
    assert result["routing"]["mode"] == "team"
    assert seen["team_mode_override"] == "on"
