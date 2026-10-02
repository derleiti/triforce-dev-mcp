"""OpenShell-backed disposable compute for paired workspaces.

This backend is opt-in and fail-closed. It reuses the existing bounded workspace
mirror and write-back machinery, but executes the command inside an OpenShell
sandbox with the gateway's policy enforcement. Network access is deny-by-default
unless an operator explicitly changes the sandbox policy out of band.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tarfile
import time
import uuid
from typing import Any

from app.config import get_settings

from .workspace_compute_sandbox import (
    _mirror_workspace,
    _run,
    _safe_rel,
    _scan_workspace,
    _session_root,
    _write_back,
)


def _resolve_cli(configured: str) -> str:
    value = str(configured or "openshell").strip()
    if "/" in value:
        path = Path(value)
        if path.is_file() and path.stat().st_mode & 0o111:
            return str(path)
        raise RuntimeError(f"OpenShell CLI is unavailable: {value}")
    found = shutil.which(value)
    if not found:
        raise RuntimeError("OpenShell CLI is unavailable on this compute node")
    return found


async def _must(argv: list[str], *, timeout: float = 60.0) -> str:
    code, out, err = await _run(argv, timeout=timeout)
    if code:
        raise RuntimeError((err or out or "OpenShell command failed").strip()[:1600])
    return out


def _sandbox_name(identity: str, run_id: str) -> str:
    digest = hashlib.sha256(f"{identity}:{run_id}".encode("utf-8")).hexdigest()[:12]
    return f"tf-os-{digest}"


def _pack_workspace(workspace: Path, archive: Path) -> None:
    with tarfile.open(archive, "w") as tf:
        for path in sorted(workspace.rglob("*")):
            if path.is_symlink():
                raise RuntimeError("workspace mirror contains a symlink; OpenShell upload refused")
            tf.add(path, arcname=path.relative_to(workspace).as_posix(), recursive=False)


async def execute_openshell_compute(
    *, connection: Any, arguments: dict[str, Any], mode: str, capabilities: set[str], identity: str,
) -> dict[str, Any]:
    settings = get_settings()
    if not bool(settings.openshell_compute_enabled):
        raise RuntimeError(
            "OpenShell compute backend is disabled; set TRIFORCE_OPENSHELL_COMPUTE_ENABLED=true on the compute node"
        )

    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("compute_execute requires command")
    cwd = _safe_rel(str(arguments.get("cwd") or "."))
    timeout = max(1, min(int(arguments.get("timeout") or 120), 300))

    cli = _resolve_cli(settings.openshell_cli)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    session_root = _session_root(identity)
    run_root = session_root / ("openshell-" + run_id)
    workspace = run_root / "workspace"
    download_root = run_root / "download"
    archive = run_root / "workspace.tar"
    workspace.mkdir(parents=True, exist_ok=False)
    download_root.mkdir(parents=True, exist_ok=True)

    baseline, warnings = await _mirror_workspace(connection, workspace, mode, capabilities)
    writable = mode == "write" and "file_edit" in capabilities and "file_ops" in capabilities
    _pack_workspace(workspace, archive)

    sandbox = _sandbox_name(identity, run_id)
    created = False
    code = 1
    stdout = stderr = ""
    cleanup_warning = ""
    sync = {"changed": 0, "deleted": 0, "backed_up": 0}
    try:
        await _must([
            cli, "sandbox", "create",
            "--name", sandbox,
            "--from", str(settings.openshell_image),
            "--cpu", str(settings.openshell_cpu),
            "--memory", str(settings.openshell_memory),
            "--no-auto-providers",
            "--detach",
            "--no-tty",
            "--", "/bin/sh", "-lc", "sleep infinity",
        ], timeout=180)
        created = True
        await _must([cli, "sandbox", "upload", sandbox, str(archive), "/tmp"], timeout=120)
        await _must([
            cli, "sandbox", "exec", "-n", sandbox, "--no-tty", "--no-login-shell",
            "--", "/bin/sh", "-lc",
            "mkdir -p /sandbox/workspace && tar -xf /tmp/workspace.tar -C /sandbox/workspace",
        ], timeout=60)

        sandbox_cwd = "/sandbox/workspace" + (f"/{cwd}" if cwd else "")
        code, stdout, stderr = await _run([
            cli, "sandbox", "exec", "-n", sandbox,
            "--workdir", sandbox_cwd,
            "--no-tty", "--no-login-shell",
            "--", "/bin/sh", "-lc", command,
        ], timeout=timeout + 15)

        if writable:
            await _must([
                cli, "sandbox", "exec", "-n", sandbox, "--no-tty", "--no-login-shell",
                "--", "/bin/sh", "-lc", "mkdir -p /sandbox/workspace",
            ], timeout=30)
            await _must([
                cli, "sandbox", "download", sandbox, "/sandbox/workspace", str(download_root),
            ], timeout=120)
            current = _scan_workspace(download_root)
            sync = await _write_back(connection, baseline, current, mode, run_id)
    except TimeoutError:
        code = 124
        stderr = f"command timed out after {timeout}s"
    finally:
        if created:
            delete_code, _, delete_err = await _run([cli, "sandbox", "delete", sandbox], timeout=45)
            if delete_code:
                cleanup_warning = (delete_err or "OpenShell sandbox cleanup failed").strip()[:500]
        shutil.rmtree(run_root, ignore_errors=True)

    pieces: list[str] = []
    if stdout:
        pieces.append("stdout:\n" + stdout[:12000])
    if stderr:
        pieces.append("stderr:\n" + stderr[:6000])
    pieces.append(
        f"exit_code={code} backend=triforce_openshell sandboxed=true internet=deny-by-default "
        f"workspace=~/workspace writeback={str(writable).lower()}"
    )
    if warnings:
        pieces.append("mirror_warnings:\n" + "\n".join(warnings[:20]))
    if cleanup_warning:
        warnings = [*warnings, cleanup_warning]
        pieces.append("cleanup_warning:\n" + cleanup_warning)

    return {
        "content": [{"type": "text", "text": "\n".join(pieces)}],
        "structuredContent": {
            "ok": code == 0,
            "exit_code": code,
            "backend": "triforce_openshell",
            "sandboxed": True,
            "internet": "deny-by-default",
            "workspace": "~/workspace",
            "workspace_writeback": writable,
            "sync": sync,
            "warnings": warnings[:20],
        },
        "isError": code != 0,
    }
