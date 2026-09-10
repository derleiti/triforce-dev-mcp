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
