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

    calls = []

    async def fake_run(argv, *, timeout=30.0):
        calls.append((list(argv), timeout))
        if "sandbox" in argv and "exec" in argv and argv[-1] == "printf hi":
            return 0, "hi", ""
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
