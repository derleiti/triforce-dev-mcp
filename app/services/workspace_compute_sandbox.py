"""Session-scoped internet-enabled Docker compute for paired workspaces.

The container never receives a server home directory, Docker socket, device, or
host network.  A bounded text mirror of the explicitly shared workspace is the
only bind mount and appears as ``~/workspace`` inside the sandbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path, PurePosixPath
import shutil
import time
import uuid
from typing import Any

SANDBOX_NETWORK = os.getenv("TRIFORCE_SANDBOX_NETWORK", "triforce-sandbox-egress")
SANDBOX_SUBNET = os.getenv("TRIFORCE_SANDBOX_SUBNET", "172.30.240.0/24")
SANDBOX_IMAGE = os.getenv("TRIFORCE_SANDBOX_IMAGE", "python:3.13-slim")
SANDBOX_ROOT = Path(os.getenv("TRIFORCE_SANDBOX_ROOT", str(Path.home() / ".local/share/triforce/compute")))
MAX_FILES = 1000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
BLOCKED_DESTINATIONS = (
    "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
)


def _safe_rel(raw: str) -> str:
    value = str(raw or "").replace("\\", "/").strip()
    while value.startswith("./"):
        value = value[2:]
    if value in {"", "."}:
        return ""
    p = PurePosixPath(value)
    windows_drive = len(value) >= 3 and value[0].isalpha() and value[1:3] == ":/"
    if windows_drive or p.is_absolute() or any(part in {"", ".", ".."} for part in p.parts):
        raise ValueError("sandbox cwd/path must stay inside the shared workspace")
    return p.as_posix()


def _excluded(path: str) -> bool:
    return path == ".workspacebackup" or path.startswith(".workspacebackup/")


def _structured(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise RuntimeError("workspace executor returned an invalid response")
    if result.get("isError"):
        text = ""
        content = result.get("content") or []
        if content and isinstance(content[0], dict):
            text = str(content[0].get("text") or "")
        raise RuntimeError(text or str(result.get("error") or "workspace executor failed"))
    data = result.get("structuredContent")
    return data if isinstance(data, dict) else result


async def _local_call(connection: Any, tool: str, arguments: dict[str, Any], mode: str, timeout: float = 120.0) -> dict[str, Any]:
    result = await connection.send_tool_call(
        "client_workspace_tool",
        {"tool": tool, "arguments": arguments, "mode": mode},
        timeout=timeout,
    )
    return _structured(result)


async def _run(argv: list[str], *, timeout: float = 30.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


async def _must(argv: list[str], *, timeout: float = 30.0) -> str:
    code, out, err = await _run(argv, timeout=timeout)
    if code:
        raise RuntimeError((err or out or "command failed").strip()[:1200])
    return out


async def ensure_network_isolation() -> None:
    """Create an outbound-only Docker network; fail closed if host isolation cannot be enforced."""
    docker = shutil.which("docker")
    if not docker:
        raise RuntimeError("Docker CLI is unavailable on the TriForce compute node")
    inspect, out, _ = await _run([
        docker, "network", "inspect", "--format", "{{(index .IPAM.Config 0).Subnet}}", SANDBOX_NETWORK
    ], timeout=10)
    if inspect != 0:
        await _must([
            docker, "network", "create", "--driver", "bridge", "--subnet", SANDBOX_SUBNET,
            "--opt", "com.docker.network.bridge.enable_icc=false", SANDBOX_NETWORK,
        ], timeout=20)
    elif out.strip() != SANDBOX_SUBNET:
        raise RuntimeError(
            f"Docker network {SANDBOX_NETWORK} has unexpected subnet {out.strip()!r}; refusing unsafe reuse"
        )

    iptables = shutil.which("iptables") or "/usr/sbin/iptables"
    sudo = shutil.which("sudo") or "/usr/bin/sudo"
    if not Path(iptables).exists() or not Path(sudo).exists():
        raise RuntimeError("iptables/sudo unavailable; refusing an unisolated internet sandbox")

    async def exists(args: list[str]) -> bool:
        code, _, _ = await _run([sudo, "-n", iptables, *args], timeout=8)
        return code == 0

    async def add_if_missing(check: list[str], add: list[str]) -> None:
        if not await exists(check):
            await _must([sudo, "-n", iptables, *add], timeout=8)

    if not await exists(["-S", "TRIFORCE-SANDBOX"]):
        await _must([sudo, "-n", iptables, "-N", "TRIFORCE-SANDBOX"], timeout=8)
    else:
        await _must([sudo, "-n", iptables, "-F", "TRIFORCE-SANDBOX"], timeout=8)
    await add_if_missing(
        ["-C", "DOCKER-USER", "-s", SANDBOX_SUBNET, "-j", "TRIFORCE-SANDBOX"],
        ["-I", "DOCKER-USER", "1", "-s", SANDBOX_SUBNET, "-j", "TRIFORCE-SANDBOX"],
    )
    await _must([sudo, "-n", iptables, "-A", "TRIFORCE-SANDBOX", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"], timeout=8)
    for destination in BLOCKED_DESTINATIONS:
        await _must([sudo, "-n", iptables, "-A", "TRIFORCE-SANDBOX", "-d", destination, "-j", "REJECT"], timeout=8)
    await _must([sudo, "-n", iptables, "-A", "TRIFORCE-SANDBOX", "-j", "RETURN"], timeout=8)
    # Traffic addressed to any interface of the TriForce host reaches INPUT, not
    # FORWARD.  This source-specific rule closes that path as well.
    await add_if_missing(
        ["-C", "INPUT", "-s", SANDBOX_SUBNET, "-j", "REJECT"],
        ["-I", "INPUT", "1", "-s", SANDBOX_SUBNET, "-j", "REJECT"],
    )


def _session_root(identity: str) -> Path:
    digest = hashlib.sha256(str(identity or "anonymous").encode("utf-8")).hexdigest()[:24]
    root = SANDBOX_ROOT / digest
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


async def _mirror_workspace(connection: Any, workspace: Path, mode: str, capabilities: set[str]) -> tuple[dict[str, str], list[str]]:
    baseline: dict[str, str] = {}
    warnings: list[str] = []
    if "file_tree" not in capabilities or "file_read" not in capabilities:
        return baseline, ["No file_tree/file_read capability; ~/workspace starts empty."]
    listing = await _local_call(connection, "file_tree", {"path": ".", "max_depth": 8, "max_entries": MAX_FILES}, mode)
    entries = listing.get("entries") or []
    if not isinstance(entries, list):
        raise RuntimeError("workspace file_tree returned invalid entries")
    total = 0
    for raw in entries[:MAX_FILES]:
        rel = _safe_rel(str(raw).rstrip("/"))
        if not rel or _excluded(rel):
            continue
        target = workspace / rel
        if str(raw).endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            data = await _local_call(connection, "file_read", {"path": rel}, mode)
            text = str(data.get("text") or "")
            size = len(text.encode("utf-8"))
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                warnings.append(f"Skipped {rel}: mirror size limit")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            baseline[rel] = text
            total += size
        except Exception as exc:
            warnings.append(f"Skipped {rel}: {str(exc)[:160]}")
    return baseline, warnings


def _scan_workspace(workspace: Path) -> dict[str, str]:
    current: dict[str, str] = {}
    total = 0
    for path in sorted(workspace.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"sandbox created a symlink; write-back refused: {path.relative_to(workspace)}")
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if _excluded(rel):
            continue
        if len(current) >= MAX_FILES:
            raise RuntimeError("sandbox workspace exceeds write-back file limit")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
            raise RuntimeError(f"sandbox write-back exceeds text mirror limit at {rel}")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"sandbox produced non-UTF-8 file; write-back refused: {rel}") from exc
        current[rel] = text
        total += size
    return current


async def _write_back(connection: Any, baseline: dict[str, str], current: dict[str, str], mode: str, run_id: str) -> dict[str, int]:
    changed = sorted(path for path, text in current.items() if baseline.get(path) != text)
    deleted = sorted(path for path in baseline if path not in current)
    if not changed and not deleted:
        return {"changed": 0, "deleted": 0, "backed_up": 0}

    backup_root = f".workspacebackup/{run_id}-compute-sandbox"
    lines = [
        "# Compute sandbox recovery",
        "",
        f"Run: {run_id}",
        "Files below were backed up before Docker sandbox write-back.",
        f"Restore copies from `{backup_root}/files/` to their original paths.",
        "",
        "## Changes",
    ]
    backed_up = 0
    for rel in changed + deleted:
        if rel in baseline:
            await _local_call(connection, "file_edit", {
                "path": f"{backup_root}/files/{rel}", "operation": "write", "content": baseline[rel],
            }, mode)
            backed_up += 1
            lines.append(f"- backup: `{rel}`")
        else:
            lines.append(f"- new file: `{rel}` (did not exist before the sandbox run)")
    await _local_call(connection, "file_edit", {
        "path": f"{backup_root}/backup.md", "operation": "write", "content": "\n".join(lines) + "\n",
    }, mode)

    for rel in changed:
        await _local_call(connection, "file_edit", {
            "path": rel, "operation": "write", "content": current[rel],
        }, mode)
    for rel in deleted:
        await _local_call(connection, "file_ops", {"action": "delete", "path": rel}, mode)
    return {"changed": len(changed), "deleted": len(deleted), "backed_up": backed_up}


async def execute_remote_compute(
    *, connection: Any, arguments: dict[str, Any], mode: str, capabilities: set[str], identity: str,
) -> dict[str, Any]:
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValueError("compute_execute requires command")
    cwd = _safe_rel(str(arguments.get("cwd") or "."))
    timeout = max(1, min(int(arguments.get("timeout") or 120), 300))
    await ensure_network_isolation()

    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    session_root = _session_root(identity)
    run_root = session_root / run_id
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=False)
    baseline, warnings = await _mirror_workspace(connection, workspace, mode, capabilities)
    writable = mode == "write" and "file_edit" in capabilities and "file_ops" in capabilities
    mount_mode = "rw" if writable else "ro"
    container_name = "triforce-sbx-" + hashlib.sha256((identity + run_id).encode()).hexdigest()[:20]
    docker = shutil.which("docker") or "docker"
    container_cwd = "/home/sandbox/workspace" + (f"/{cwd}" if cwd else "")
    args = [
        docker, "run", "--rm", "-i", "--name", container_name,
        "--network", SANDBOX_NETWORK,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
        "--tmpfs", "/home/sandbox:rw,nosuid,size=256m",
        "--pids-limit", "256", "--memory", "2g", "--cpus", "2",
        "--user", f"{os.getuid()}:{os.getgid()}",
        "--label", f"ailinux.triforce.sandbox={hashlib.sha256(identity.encode()).hexdigest()[:16]}",
        "-e", "HOME=/home/sandbox", "-e", "TMPDIR=/tmp",
        "-v", f"{workspace}:/home/sandbox/workspace:{mount_mode}",
        "-w", container_cwd,
        SANDBOX_IMAGE, "/bin/sh", "-lc", command,
    ]
    code = 1
    stdout = stderr = ""
    try:
        code, stdout, stderr = await _run(args, timeout=timeout + 15)
    except asyncio.TimeoutError:
        await _run([docker, "rm", "-f", container_name], timeout=15)
        stderr = f"command timed out after {timeout}s"
        code = 124
    finally:
        # --rm normally handles this; force cleanup covers a killed docker CLI.
        await _run([docker, "rm", "-f", container_name], timeout=15)

    sync = {"changed": 0, "deleted": 0, "backed_up": 0}
    if writable:
        current = _scan_workspace(workspace)
        sync = await _write_back(connection, baseline, current, mode, run_id)
    shutil.rmtree(run_root, ignore_errors=True)

    pieces = []
    if stdout:
        pieces.append("stdout:\n" + stdout[:12000])
    if stderr:
        pieces.append("stderr:\n" + stderr[:6000])
    pieces.append(
        f"exit_code={code} backend=triforce_docker sandboxed=true internet=public-only "
        f"workspace=~/workspace writeback={str(writable).lower()}"
    )
    if warnings:
        pieces.append("mirror_warnings:\n" + "\n".join(warnings[:20]))
    return {
        "content": [{"type": "text", "text": "\n".join(pieces)}],
        "structuredContent": {
            "ok": code == 0, "exit_code": code, "backend": "triforce_docker",
            "sandboxed": True, "internet": "public-only", "workspace": "~/workspace",
            "workspace_writeback": writable, "sync": sync, "warnings": warnings[:20],
        },
        "isError": code != 0,
    }
