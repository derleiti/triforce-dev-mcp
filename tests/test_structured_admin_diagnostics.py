from __future__ import annotations

import pytest

from app.mcp import structured_admin as sa


@pytest.mark.asyncio
async def test_remote_docker_status_uses_sudo(monkeypatch):
    captured = {}
    async def fake_ssh(host, cmd, timeout=30):
        captured['host'] = host
        captured['cmd'] = cmd
        return {'success': True, 'output': 'ok', 'errors': None, 'exit_code': 0}
    monkeypatch.setattr(sa, '_ssh_run', fake_ssh)

    result = await sa.handle_remote_admin({'action': 'docker_status', 'host': 'zombie-pc'})

    assert result['success'] is True
    assert captured['cmd'] == ['sudo', 'docker', 'ps', '--format', '{{.Names}}\t{{.Status}}']


def test_disk_usage_detail_has_no_unexpanded_glob():
    cmd, needs_sudo, timeout, _ = sa.COMMAND_TEMPLATES['disk_usage_detail']
    assert cmd == ['du', '-x', '-h', '--max-depth=1', '/home/zombie/workspace/triforce']
    assert needs_sudo is True
    assert timeout == 30
    assert not any('*' in part for part in cmd)


@pytest.mark.asyncio
async def test_local_triforce_restart_is_deferred_instead_of_self_killing_shell(monkeypatch):
    from app.services.system_control import system_control

    calls = []

    async def fake_restart_backend(delay):
        calls.append(delay)
        return {"status": "initiated", "message": "deferred restart"}

    async def forbidden_sudo(*args, **kwargs):
        raise AssertionError("local TriForce restart must not call synchronous systemctl")

    monkeypatch.setattr(system_control, "restart_backend", fake_restart_backend)
    monkeypatch.setattr(sa, "_sudo", forbidden_sudo)

    result = await sa.handle_service_control({"action": "restart", "service": "triforce", "delay": 3})

    assert calls == [3]
    assert result == {
        "action": "restart",
        "service": "triforce",
        "unit": sa._unit("triforce"),
        "status": "initiated",
        "message": "deferred restart",
    }


@pytest.mark.asyncio
async def test_other_service_restart_still_uses_systemctl(monkeypatch):
    captured = []

    async def fake_sudo(cmd, timeout=30):
        captured.append((cmd, timeout))
        return {"success": True, "output": "", "errors": None, "exit_code": 0}

    monkeypatch.setattr(sa, "_sudo", fake_sudo)
    service = next(name for name in sa.SERVICES if name != "triforce")
    result = await sa.handle_service_control({"action": "restart", "service": service})

    assert result["success"] is True
    assert captured == [(["systemctl", "restart", sa._unit(service)], 30)]
