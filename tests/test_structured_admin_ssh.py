from __future__ import annotations

import pytest

from app.mcp import structured_admin as sa


@pytest.mark.asyncio
async def test_ssh_run_quotes_remote_argv_as_single_command(monkeypatch):
    captured = {}

    async def fake_run(cmd, timeout=30):
        captured['cmd'] = cmd
        captured['timeout'] = timeout
        return {'success': True, 'output': '', 'errors': None, 'exit_code': 0}

    monkeypatch.setattr(sa, '_run', fake_run)
    result = await sa._ssh_run(
        'zombie-pc',
        ['sudo', 'docker', 'ps', '--format', '{{.Names}}\t{{.Status}}'],
        timeout=17,
    )

    assert result['success'] is True
    assert captured['timeout'] == 17
    assert captured['cmd'][-2] == 'zombie@10.10.0.2'
    assert captured['cmd'][-1] == "sudo docker ps --format '{{.Names}}\t{{.Status}}'"


@pytest.mark.asyncio
async def test_ssh_run_preserves_bash_c_script_as_one_argument(monkeypatch):
    captured = {}

    async def fake_run(cmd, timeout=30):
        captured['cmd'] = cmd
        return {'success': True, 'output': '', 'errors': None, 'exit_code': 0}

    monkeypatch.setattr(sa, '_run', fake_run)
    await sa._ssh_run('zombie-pc', ['bash', '-c', 'echo one; echo two'])

    assert captured['cmd'][-1] == "bash -c 'echo one; echo two'"
