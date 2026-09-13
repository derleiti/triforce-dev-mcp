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
