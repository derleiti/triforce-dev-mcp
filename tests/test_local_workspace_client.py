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


def test_workspace_clear_preserves_root():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        (root / '.hidden').write_text('x', encoding='utf-8')
        (root / 'sub').mkdir()
        (root / 'sub' / 'file.txt').write_text('y', encoding='utf-8')
        runtime = WorkspaceRuntime(root, writable=True)
        denied = runtime.execute('workspace_clear', {'confirm': 'NO'})
        assert denied['isError'] is True
        assert (root / '.hidden').exists()
        cleared = runtime.execute('workspace_clear', {'confirm': 'DELETE_ALL'})
        assert cleared['isError'] is False
        assert root.exists() and root.is_dir()
        assert list(root.iterdir()) == []
        assert cleared['structuredContent']['root_preserved'] is True


def test_workspace_session_state_roundtrip(monkeypatch):
    from local_workspace_client import client
    with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as home:
        root = Path(temp)
        monkeypatch.setattr(client.Path, 'home', staticmethod(lambda: Path(home)))
        client._save_state(root, {'resume_token': 'secret', 'server': 'https://api.ailinux.me'})
        state_path = client._state_file(root)
        assert state_path.exists()
        assert oct(state_path.stat().st_mode & 0o777) == '0o600'
        assert client._load_state(root)['resume_token'] == 'secret'
        client._clear_state(root)
        assert not state_path.exists()


def test_mutating_file_edit_creates_shared_fallback(monkeypatch):
    with tempfile.TemporaryDirectory() as temp, tempfile.TemporaryDirectory() as backups:
        root = Path(temp)
        target = root / 'x.txt'
        target.write_text('before', encoding='utf-8')
        monkeypatch.setenv('AILINUX_WORKSPACE_BACKUP_ROOT', backups)
        runtime = WorkspaceRuntime(root, writable=True)
        result = runtime.execute('file_edit', {'path': 'x.txt', 'operation': 'write', 'content': 'after'})
        assert result['isError'] is False
        backup = Path(result['structuredContent']['backup'])
        assert backup.is_dir()
        assert (backup / 'files' / 'x.txt').read_text(encoding='utf-8') == 'before'
        backup_doc = (backup / 'backup.md').read_text(encoding='utf-8')
        assert 'Source workspace' in backup_doc
        assert 'Recovery' in backup_doc
        index = Path(backups) / 'INDEX.md'
        assert str(backup) in index.read_text(encoding='utf-8')
        assert target.read_text(encoding='utf-8') == 'after'
