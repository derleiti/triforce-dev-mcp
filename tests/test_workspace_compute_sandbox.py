from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import mcp_workspace_sessions as sessions
from app.services import workspace_compute_sandbox as sandbox


class FakeRedis:
    def __init__(self):
        self.data = {}

    def setex(self, key, ttl, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)


class FakeConnection:
    async def send_tool_call(self, *args, **kwargs):
        raise AssertionError("unexpected helper call")


def test_pair_ticket_survives_process_memory_loss_without_persisting_raw_code(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, "_redis_client", lambda: fake)
    sessions._WEB_PAIR.clear()
    code = sessions.create_web_pair_code()
    pair_hash = sessions._pair_key(code)
    raw = fake.get(sessions.REDIS_PAIR_PREFIX + pair_hash)
    assert raw
    assert code not in raw
    payload = json.loads(raw)
    assert payload["pair_hash"] == pair_hash

    # Simulate a backend restart: process memory is gone, Redis remains.
    sessions._WEB_PAIR.clear()
    restored = sessions.resolve_web_pair_code(code)
    assert restored is not None
    assert restored["join_hash"] == pair_hash
    assert restored["code"] == code


def test_sandbox_paths_never_escape_workspace():
    assert sandbox._safe_rel(".") == ""
    assert sandbox._safe_rel("src/main.py") == "src/main.py"
    for value in ("/etc/passwd", "../etc/passwd", "src/../../etc", "C:\\Windows"):
        with pytest.raises(ValueError):
            sandbox._safe_rel(value)


@pytest.mark.asyncio
async def test_remote_compute_plan_is_hardened_and_mounts_only_workspace(monkeypatch, tmp_path):
    calls = []

    async def fake_isolation():
        return None

    async def fake_mirror(connection, workspace, mode, capabilities):
        (workspace / "hello.txt").write_text("hello", encoding="utf-8")
        return {"hello.txt": "hello"}, []

    async def fake_run(argv, timeout=30):
        calls.append(list(argv))
        if len(argv) > 1 and argv[1] == "run":
            return 0, "/home/sandbox/workspace\n", ""
        return 0, "", ""

    monkeypatch.setattr(sandbox, "ensure_network_isolation", fake_isolation)
    monkeypatch.setattr(sandbox, "_mirror_workspace", fake_mirror)
    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(sandbox, "SANDBOX_ROOT", tmp_path)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    result = await sandbox.execute_remote_compute(
        connection=FakeConnection(), arguments={"command": "pwd", "cwd": ".", "timeout": 5},
        mode="read_only", capabilities={"file_tree", "file_read", "compute_execute"}, identity="user:chat",
    )
    run = next(call for call in calls if len(call) > 1 and call[1] == "run")
    joined = " ".join(run)
    assert "--network triforce-sandbox-egress" in joined
    assert "--cap-drop ALL" in joined
    assert "no-new-privileges" in joined
    assert "--read-only" in joined
    assert "/home/sandbox/workspace:ro" in joined
    assert "/var/run/docker.sock" not in joined
    assert "--network host" not in joined
    assert "--privileged" not in joined
    assert result["structuredContent"]["workspace"] == "~/workspace"
    assert result["structuredContent"]["internet"] == "public-only"


@pytest.mark.asyncio
async def test_writeback_creates_recovery_snapshot_before_mutation(monkeypatch):
    calls = []

    async def fake_local(connection, tool, arguments, mode, timeout=120.0):
        calls.append((tool, dict(arguments)))
        return {"ok": True}

    monkeypatch.setattr(sandbox, "_local_call", fake_local)
    result = await sandbox._write_back(
        object(), {"old.txt": "before", "gone.txt": "remove me"},
        {"old.txt": "after", "new.txt": "new"}, "write", "run123",
    )
    paths = [args.get("path") for _, args in calls]
    assert ".workspacebackup/run123-compute-sandbox/files/old.txt" in paths
    assert ".workspacebackup/run123-compute-sandbox/files/gone.txt" in paths
    assert ".workspacebackup/run123-compute-sandbox/backup.md" in paths
    first_real_mutation = next(i for i, (_, args) in enumerate(calls) if args.get("path") in {"old.txt", "new.txt", "gone.txt"})
    backup_manifest = next(i for i, (_, args) in enumerate(calls) if args.get("path", "").endswith("/backup.md"))
    assert backup_manifest < first_real_mutation
    assert result == {"changed": 2, "deleted": 1, "backed_up": 2}
