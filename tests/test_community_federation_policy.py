import pytest

from app.services.community_federation_policy import CommunityFederationPolicy, CommunityPolicyError
from app.services.distributed_compute import ComputeTask, ConnectedClient, DistributedComputeManager, TaskStatus


class Settings:
    federation_community_enabled = True
    federation_community_allowed_tiers = "registered,pro,enterprise"
    federation_community_max_message_bytes = 4096
    federation_community_max_task_bytes = 4096
    federation_community_max_models = 4
    federation_community_mistral_audit_enabled = False


def patch_settings(monkeypatch):
    monkeypatch.setattr("app.services.community_federation_policy.get_settings", lambda: Settings())


def test_registration_is_bounded_and_allowlisted(monkeypatch):
    patch_settings(monkeypatch)
    policy = CommunityFederationPolicy()
    reg = policy.validate_registration(
        capability="webgpu",
        gpu_name="Test GPU",
        estimated_tflops=12.5,
        supported_models=["embedding_small", "sentiment"],
    )
    assert reg.capability == "WEBGPU"
    assert reg.supported_models == ["embedding_small", "sentiment"]
    with pytest.raises(CommunityPolicyError):
        policy.validate_registration(
            capability="SHELL_ROOT",
            gpu_name="x",
            estimated_tflops=1,
            supported_models=[],
        )


def test_guest_is_not_allowed(monkeypatch):
    patch_settings(monkeypatch)
    policy = CommunityFederationPolicy()
    with pytest.raises(CommunityPolicyError):
        policy.validate_tier("guest")
    assert policy.validate_tier("registered") == "registered"


def test_community_worker_cannot_receive_private_task():
    client = ConnectedClient(
        session_id="c1",
        websocket=None,
        capability="WEBGPU",
        supported_models=["embedding_small"],
        trust_level="community",
    )
    private = ComputeTask("t1", "embedding", ["x"], "embedding_small", allow_community=False)
    shared = ComputeTask("t2", "embedding", ["x"], "embedding_small", allow_community=True)
    assert client.can_handle(private) is False
    assert client.can_handle(shared) is True


@pytest.mark.asyncio
async def test_community_result_is_unverified_and_credit_pending():
    manager = DistributedComputeManager()
    client = ConnectedClient(
        session_id="c1",
        websocket=None,
        capability="WEBGPU",
        supported_models=["embedding_small"],
        trust_level="community",
        current_task="t1",
        is_available=False,
    )
    task = ComputeTask("t1", "embedding", ["x"], "embedding_small", allow_community=True)
    task.status = TaskStatus.ASSIGNED
    task.assigned_to = "c1"
    manager._clients["c1"] = client
    manager._task_queue["t1"] = task

    await manager.report_task_result("c1", "t1", True, result=[0.1, 0.2], compute_time=0.5)

    assert task.result_verified is False
    assert task.result_source_trust == "community"
    assert client.credits_earned == 0.0
    assert client.credits_pending > 0.0
