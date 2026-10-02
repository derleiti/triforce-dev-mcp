from types import SimpleNamespace

import pytest

from app.services import workspace_openshell_sandbox as openshell_backend


@pytest.mark.asyncio
async def test_openshell_compute_is_fail_closed_when_disabled(monkeypatch):
    monkeypatch.setattr(
        openshell_backend,
        "get_settings",
        lambda: SimpleNamespace(
            openshell_compute_enabled=False,
            openshell_cli="openshell",
            openshell_image="python:3.13-slim",
            openshell_cpu="2",
            openshell_memory="2Gi",
        ),
    )

    with pytest.raises(RuntimeError, match="disabled"):
        await openshell_backend.execute_openshell_compute(
            connection=object(),
            arguments={"command": "true"},
            mode="read_only",
            capabilities=set(),
            identity="test",
        )


@pytest.mark.asyncio
async def test_openshell_compute_uses_bounded_lifecycle_and_writeback(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        openshell_compute_enabled=True,
        openshell_cli="openshell",
        openshell_image="python:3.13-slim",
        openshell_cpu="2",
        openshell_memory="2Gi",
    )
    monkeypatch.setattr(openshell_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(openshell_backend.shutil, "which", lambda _name: "/usr/bin/openshell")
    monkeypatch.setattr(openshell_backend, "_session_root", lambda _identity: tmp_path)

    async def fake_mirror(_connection, workspace, _mode, _capabilities):
        (workspace / "README.md").write_text("before", encoding="utf-8")
        return {"README.md": "before"}, []

    writeback_calls = []

    async def fake_write_back(_connection, baseline, current, mode, run_id):
        writeback_calls.append((baseline, current, mode, run_id))
        return {"changed": 1, "deleted": 0, "backed_up": 1}

    monkeypatch.setattr(openshell_backend, "_mirror_workspace", fake_mirror)
    monkeypatch.setattr(openshell_backend, "_scan_workspace", lambda _root: {"README.md": "after"})
    monkeypatch.setattr(openshell_backend, "_write_back", fake_write_back)
    monkeypatch.setattr(openshell_backend, "_consume_transfer_sentinel", lambda _root, _marker: None)

    calls = []

    async def fake_run(argv, *, timeout=30.0):
        calls.append((list(argv), timeout))
        if "sandbox" in argv and "exec" in argv and argv[-1] == "printf hi":
            return 0, "hi", ""
        if argv[-1] == "du -sb /sandbox/workspace | cut -f1":
            return 0, "1024\n", ""
        return 0, "", ""

    monkeypatch.setattr(openshell_backend, "_run", fake_run)

    result = await openshell_backend.execute_openshell_compute(
        connection=object(),
        arguments={"command": "printf hi", "cwd": ".", "timeout": 20},
        mode="write",
        capabilities={"file_tree", "file_read", "file_edit", "file_ops"},
        identity="session-test",
    )

    assert result["structuredContent"]["ok"] is True
    assert result["structuredContent"]["backend"] == "triforce_openshell"
    assert result["structuredContent"]["internet"] == "deny-by-default"
    assert result["structuredContent"]["workspace_writeback"] is True
    assert result["structuredContent"]["workspace_writeback_completed"] is True
    assert result["structuredContent"]["writeback_error"] is None
    assert result["structuredContent"]["sync"]["backed_up"] == 1
    assert writeback_calls and writeback_calls[0][0] == {"README.md": "before"}
    assert writeback_calls[0][1] == {"README.md": "after"}

    argv_calls = [argv for argv, _timeout in calls]
    create = next(argv for argv in argv_calls if argv[:3] == ["/usr/bin/openshell", "sandbox", "create"])
    assert "--no-auto-providers" in create
    assert create[create.index("--cpu") + 1] == "2"
    assert create[create.index("--memory") + 1] == "2Gi"
    sandbox_name = create[create.index("--name") + 1]
    assert len(sandbox_name) <= 19

    user_exec = next(argv for argv in argv_calls if argv[-1] == "printf hi")
    assert user_exec[-3:] == ["/bin/sh", "-lc", "printf hi"]
    assert any(argv[:3] == ["/usr/bin/openshell", "sandbox", "upload"] for argv in argv_calls)
    assert any(argv[:3] == ["/usr/bin/openshell", "sandbox", "download"] for argv in argv_calls)
    assert any(argv[:3] == ["/usr/bin/openshell", "sandbox", "delete"] for argv in argv_calls)



def test_transfer_sentinel_rejects_unexpected_download_layout(tmp_path):
    with pytest.raises(RuntimeError, match="layout verification failed"):
        openshell_backend._consume_transfer_sentinel(tmp_path, "marker")


def test_transfer_sentinel_accepts_exact_marker_and_removes_it(tmp_path):
    sentinel = tmp_path / openshell_backend.TRANSFER_SENTINEL
    sentinel.write_text("marker", encoding="utf-8")
    openshell_backend._consume_transfer_sentinel(tmp_path, "marker")
    assert not sentinel.exists()


@pytest.mark.asyncio
async def test_openshell_cleanup_removes_local_mirror_when_delete_raises(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        openshell_compute_enabled=True,
        openshell_cli="openshell",
        openshell_image="python:3.13-slim",
        openshell_cpu="1",
        openshell_memory="1Gi",
    )
    monkeypatch.setattr(openshell_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(openshell_backend.shutil, "which", lambda _name: "/usr/bin/openshell")
    monkeypatch.setattr(openshell_backend, "_session_root", lambda _identity: tmp_path)

    async def fake_mirror(_connection, workspace, _mode, _capabilities):
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        return {"a.txt": "a"}, []

    calls = {"delete": 0}

    async def fake_run(argv, *, timeout=30.0):
        if argv[:3] == ["/usr/bin/openshell", "sandbox", "delete"]:
            calls["delete"] += 1
            raise TimeoutError("delete timeout")
        return 0, "", ""

    monkeypatch.setattr(openshell_backend, "_mirror_workspace", fake_mirror)
    monkeypatch.setattr(openshell_backend, "_run", fake_run)

    result = await openshell_backend.execute_openshell_compute(
        connection=object(),
        arguments={"command": "true"},
        mode="read",
        capabilities={"file_tree", "file_read"},
        identity="cleanup-test",
    )

    assert result["structuredContent"]["ok"] is True
    assert calls["delete"] == 1
    assert any("cleanup failed" in item for item in result["structuredContent"]["warnings"])
    assert list(tmp_path.glob("openshell-*")) == []



@pytest.mark.asyncio
async def test_create_timeout_still_attempts_best_effort_delete(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        openshell_compute_enabled=True,
        openshell_cli="openshell",
        openshell_image="python:3.13-slim",
        openshell_cpu="1",
        openshell_memory="1Gi",
    )
    monkeypatch.setattr(openshell_backend, "get_settings", lambda: settings)
    monkeypatch.setattr(openshell_backend.shutil, "which", lambda _name: "/usr/bin/openshell")
    monkeypatch.setattr(openshell_backend, "_session_root", lambda _identity: tmp_path)

    async def fake_mirror(_connection, workspace, _mode, _capabilities):
        return {}, []

    deleted = []

    async def fake_run(argv, *, timeout=30.0):
        if argv[:3] == ["/usr/bin/openshell", "sandbox", "create"]:
            raise TimeoutError("create timeout")
        if argv[:3] == ["/usr/bin/openshell", "sandbox", "delete"]:
            deleted.append(argv[-1])
            return 1, "", "not found"
        return 0, "", ""

    monkeypatch.setattr(openshell_backend, "_mirror_workspace", fake_mirror)
    monkeypatch.setattr(openshell_backend, "_run", fake_run)

    result = await openshell_backend.execute_openshell_compute(
        connection=object(),
        arguments={"command": "true", "timeout": 20},
        mode="read",
        capabilities=set(),
        identity="create-timeout",
    )

    assert result["structuredContent"]["exit_code"] == 124
    assert "sandbox_create timed out" in result["content"][0]["text"]
    assert deleted
    assert list(tmp_path.glob("openshell-*")) == []
