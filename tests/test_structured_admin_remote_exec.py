import pytest

from app.mcp import structured_admin as admin


@pytest.mark.asyncio
async def test_restart_triforce_is_scheduled_before_transport_is_disrupted(monkeypatch):
    calls = []

    async def fake_run(argv, timeout=30):
        calls.append((list(argv), timeout))
        return {"success": True, "output": "", "errors": None, "exit_code": 0, "elapsed_ms": 3}

    monkeypatch.setattr(admin, "_run", fake_run)
    result = await admin.handle_remote_exec({"node": "hetzner", "command": "restart_triforce"})

    assert result["success"] is True
    assert result["command"] == "restart_triforce"
    ssh_argv = calls[0][0]
    remote = ssh_argv[ssh_argv.index("--") + 1:]
    assert remote[:2] == ["sudo", "systemd-run"]
    assert "--on-active=2s" in remote
    assert any(part.startswith("--unit=ailinux-mcp-restart-triforce-") for part in remote)
    assert remote[-3:] == ["/bin/systemctl", "restart", "triforce"]
    assert all(part not in {"sh", "bash", "-c"} for part in remote)


@pytest.mark.asyncio
async def test_readonly_remote_action_remains_direct(monkeypatch):
    calls = []

    async def fake_run(argv, timeout=30):
        calls.append(list(argv))
        return {"success": True, "output": "active", "errors": None, "exit_code": 0, "elapsed_ms": 1}

    monkeypatch.setattr(admin, "_run", fake_run)
    result = await admin.handle_remote_exec({"node": "zombie-pc", "command": "status"})

    assert result["success"] is True
    remote = calls[0][calls[0].index("--") + 1:]
    assert remote == ["systemctl", "is-active", "triforce"]
