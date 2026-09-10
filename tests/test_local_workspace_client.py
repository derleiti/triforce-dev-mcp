from __future__ import annotations

import tempfile
from pathlib import Path

from local_workspace_client.client import mcp_url, node_url
from local_workspace_client.runtime import READ_TOOLS, WRITE_TOOLS, WorkspaceRuntime


def test_runtime_analyzes_and_confines_workspace():
    with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as outside:
        root = Path(temp)
        (root / 'src').mkdir()
        (root / 'src' / 'app.py').write_text("print('hello')\n", encoding='utf-8')
        runtime = WorkspaceRuntime(root, writable=False, task='inspect')
        info = runtime.analyze()
        assert info['files'] == 1
        assert info['task'] == 'inspect'
        assert runtime.resolve('src/app.py', must_exist=True) == (root / 'src' / 'app.py').resolve()
        try:
            runtime.resolve(Path(outside) / 'secret.txt')
        except PermissionError:
            pass
        else:
            raise AssertionError('outside path was not rejected')


def test_read_only_blocks_writes_and_write_mode_edits():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        read_runtime = WorkspaceRuntime(root, writable=False)
        blocked = read_runtime.execute('file_edit', {'path': 'x.txt', 'operation': 'create', 'content': 'x'})
        assert blocked['isError'] is True
        assert not (root / 'x.txt').exists()
        write_runtime = WorkspaceRuntime(root, writable=True)
        created = write_runtime.execute('file_edit', {'path': 'x.txt', 'operation': 'create', 'content': 'x'})
        assert created['isError'] is False
        assert (root / 'x.txt').read_text() == 'x'


def test_tool_sets_and_fixed_public_mcp_url():
    assert 'shell' not in READ_TOOLS
    assert 'file_edit' not in READ_TOOLS
    assert {'shell', 'file_edit', 'test'} <= WRITE_TOOLS
    url = node_url('https://api.ailinux.me', 'ABCD-1234-EF56')
    assert url.startswith('wss://api.ailinux.me/v1/mcp/node/connect?')
    assert 'mode=workspace' in url
    assert 'pair_code=ABCD-1234-EF56' in url
    assert 'token=' not in url
    assert mcp_url('https://api.ailinux.me') == 'https://api.ailinux.me/v1/mcp'
