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

    async def fake_run_profile(agent_id, message, timeout_override=None):
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
