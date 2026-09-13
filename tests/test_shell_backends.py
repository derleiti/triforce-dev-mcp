"""Tests for the canonical shell backend layer of the Loom workspace executor."""
from __future__ import annotations

import shutil

import pytest

from local_workspace_client import shell_backends as sb
from local_workspace_client.runtime import WorkspaceRuntime


@pytest.fixture
def clean_env(monkeypatch):
    for key in (sb.ENV_BACKEND, sb.ENV_RELEASE, sb.ENV_DOCKER_IMAGE, sb.ENV_DOCKER_NETWORK):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_release_off_blocks_every_backend(clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "off")
    released, reason = sb.is_released("bubblewrap")
    assert released is False
    assert "off" in reason


def test_auto_releases_sandboxed_only(clean_env):
    assert sb.is_released("bubblewrap")[0] is True
    assert sb.is_released("docker")[0] is True
    assert sb.is_released("linux")[0] is False
    assert sb.is_released("termux")[0] is False
    assert sb.is_released("powershell")[0] is False


def test_explicit_backend_release_is_scoped(clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "termux")
    assert sb.is_released("termux")[0] is True
    assert sb.is_released("linux")[0] is False


def test_release_on_covers_unsandboxed(clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "on")
    assert sb.is_released("linux")[0] is True


def test_unknown_pinned_backend_is_rejected(clean_env):
    clean_env.setenv(sb.ENV_BACKEND, "nonsense")
    backend, reason = sb.detect_backend()
    assert backend == ""
    assert "unknown backend" in reason


def test_posix_argv_uses_pipefail_only_for_bash():
    assert sb._posix_argv("/bin/bash", "true") == ["/bin/bash", "-o", "pipefail", "-c", "true"]
    assert sb._posix_argv("/bin/sh", "true") == ["/bin/sh", "-c", "true"]


def test_empty_command_is_rejected(tmp_path, clean_env):
    with pytest.raises(sb.ShellUnavailable):
        sb.build_shell_plan("   ", root=tmp_path, work=tmp_path)


def test_unreleased_backend_raises(tmp_path, clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "off")
    with pytest.raises(sb.ShellUnavailable):
        sb.build_shell_plan("echo hi", root=tmp_path, work=tmp_path)


def test_docker_plan_binds_workspace(tmp_path, clean_env, monkeypatch):
    clean_env.setenv(sb.ENV_DOCKER_IMAGE, "alpine:3")
    monkeypatch.setattr(sb, "_which", lambda *names: "/usr/bin/docker")
    sub = tmp_path / "pkg"
    sub.mkdir()
    plan = sb.build_shell_plan("echo hi", root=tmp_path, work=sub)
    assert plan.backend == "docker"
    assert plan.sandboxed is True
    assert f"{tmp_path}:/workspace" in plan.argv
    assert "/workspace/pkg" in plan.argv


def test_powershell_plan_is_non_interactive(tmp_path, clean_env, monkeypatch):
    clean_env.setenv(sb.ENV_BACKEND, "powershell")
    clean_env.setenv(sb.ENV_RELEASE, "on")
    monkeypatch.setattr(sb, "_which", lambda *names: "pwsh")
    plan = sb.build_shell_plan("Get-Location", root=tmp_path, work=tmp_path)
    assert plan.backend == "powershell"
    assert "-NonInteractive" in plan.argv
    assert plan.sandboxed is False
    assert plan.cwd == str(tmp_path)


def test_capabilities_hide_execution_tools_when_not_released(tmp_path, clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "off")
    tools = WorkspaceRuntime(tmp_path, writable=True).available_tools()
    assert "file_edit" in tools
    for blocked in ("shell", "task_runner", "binary_exec", "lint", "test"):
        assert blocked not in tools


def test_capabilities_expose_shell_when_released(tmp_path, clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "on")
    assert "shell" in WorkspaceRuntime(tmp_path, writable=True).available_tools()


def test_read_only_workspace_never_executes(tmp_path, clean_env):
    clean_env.setenv(sb.ENV_RELEASE, "on")
    text, err = WorkspaceRuntime(tmp_path, writable=False)._sandbox("echo hi")
    assert err is True
    assert "read-only" in text


@pytest.mark.skipif(shutil.which("bwrap") is None, reason="bubblewrap not installed")
def test_bubblewrap_executes_inside_workspace(tmp_path, clean_env):
    text, err = WorkspaceRuntime(tmp_path, writable=True)._sandbox("echo loom-ok")
    assert err is False, text
    assert "loom-ok" in text
    assert "backend=bubblewrap" in text
