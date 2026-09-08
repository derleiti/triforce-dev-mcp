"""Experimental remote coding agent for connected AICoder nodes.

This service deliberately separates reasoning from execution:
- Google Antigravity SDK owns the agent/conversation loop.
- TriForce owns authentication, node selection and orchestration.
- The connected AICoder owns workspace confinement and local tool execution.

Read tools are always eligible. A single constrained file-edit tool is available
only when the connected AICoder explicitly advertises its local write-preview
profile. Shell, delete and arbitrary write forwarding are never registered here.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.routes.mcp_node import CONNECTED_CLIENTS, ClientConnection

logger = logging.getLogger("ailinux.remote_coding_agent")

REMOTE_READ_TOOLS = {
    "client_file_read",
    "client_file_list",
    "client_codebase_search",
    "client_git_status",
}
REMOTE_WRITE_TOOLS = {"client_file_edit"}
REMOTE_CONTROL_TOOLS = {"client_run_state"}
REMOTE_MODEL_TOOLS = REMOTE_READ_TOOLS | REMOTE_WRITE_TOOLS
REMOTE_TOOLS = REMOTE_MODEL_TOOLS | REMOTE_CONTROL_TOOLS


@dataclass
class RemoteCodingNode:
    client_id: str
    user_id: str
    tier: str
    hostname: str
    workspace: str
    profile: str
    supported_tools: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_id": self.client_id,
            "user_id": self.user_id,
            "tier": self.tier,
            "hostname": self.hostname,
            "workspace": self.workspace,
            "profile": self.profile,
            "supported_tools": list(self.supported_tools),
        }


def _unique_connections() -> List[tuple[str, ClientConnection]]:
    """Collapse registry aliases so each physical WebSocket appears once."""
    seen: set[int] = set()
    rows: List[tuple[str, ClientConnection]] = []
    for registry_id, connection in CONNECTED_CLIENTS.items():
        identity = id(connection)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append((registry_id, connection))
    return rows


def _node_view(registry_id: str, connection: ClientConnection) -> RemoteCodingNode:
    info = connection.client_info if isinstance(connection.client_info, dict) else {}
    supported = [
        str(name) for name in (connection.supported_tools or [])
        if str(name) in REMOTE_TOOLS
    ]
    return RemoteCodingNode(
        client_id=connection.client_id or registry_id,
        user_id=connection.user_id,
        tier=connection.tier.value,
        hostname=str(info.get("hostname") or ""),
        workspace=str(info.get("workspace") or ""),
        profile=str(info.get("remote_profile") or info.get("mode") or connection.mode),
        supported_tools=sorted(set(supported)),
    )


def list_remote_coding_nodes() -> List[RemoteCodingNode]:
    rows: List[RemoteCodingNode] = []
    for registry_id, connection in _unique_connections():
        info = connection.client_info if isinstance(connection.client_info, dict) else {}
        if info.get("client") != "aicoder":
            continue
        if connection.mode == "telemetry_only":
            continue
        node = _node_view(registry_id, connection)
        if set(node.supported_tools) & REMOTE_MODEL_TOOLS:
            rows.append(node)
    return rows


def resolve_remote_coding_node(client_id: str) -> ClientConnection:
    connection = CONNECTED_CLIENTS.get(client_id)
    if connection is None:
        raise LookupError(f"AICoder node not connected: {client_id}")
    info = connection.client_info if isinstance(connection.client_info, dict) else {}
    if info.get("client") != "aicoder":
        raise LookupError(f"Connected client is not an AICoder node: {client_id}")
    if connection.mode == "telemetry_only":
        raise PermissionError(f"AICoder node is telemetry-only: {client_id}")
    return connection


async def _call_client_tool(
    connection: ClientConnection,
    name: str,
    arguments: Dict[str, Any],
    *,
    timeout: float = 60.0,
) -> str:
    if name not in REMOTE_TOOLS:
        raise PermissionError(f"remote coding preview blocks tool: {name}")
    if name not in set(connection.supported_tools or []):
        raise LookupError(f"AICoder node does not advertise tool: {name}")
    recalled = {}
    if name in {'client_file_read', 'client_file_edit'}:
        from .memory_runtime import remote_recall
        recalled = await remote_recall(connection, 'file_opened',
            run_id=str(arguments.get('_run_id', '')), file=str(arguments.get('path', '')),
            task=str(arguments.get('_task', '')), model=str(arguments.get('_model', '')))
    try:
        result = await connection.send_tool_call(name, arguments, timeout=timeout)
    except Exception as exc:
        from .memory_runtime import remote_recall, remote_record
        await remote_recall(connection, 'tool_failed', run_id=str(arguments.get('_run_id', '')),
                            tool=name, error_type=type(exc).__name__, error=str(exc))
        await remote_record(connection, 'tool_failed', run_id=str(arguments.get('_run_id', '')),
                            tool=name, error_type=type(exc).__name__, error=str(exc), verification_state='failed')
        raise
    if not isinstance(result, dict):
        return "REMOTE TOOL ERROR: invalid MCP result (expected an object)"
    blocks = result.get("content")
    texts = [
        block["text"] for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ] if isinstance(blocks, list) else []
    if result.get("isError") or "error" in result:
        detail = "\n".join(texts) or str(result.get("error") or "remote tool failed without details")
        from .memory_runtime import remote_recall, remote_record
        recalled = await remote_recall(connection, 'tool_failed',
            run_id=str(arguments.get('_run_id', '')), tool=name, error=detail)
        await remote_record(connection, 'tool_failed', run_id=str(arguments.get('_run_id', '')),
                            tool=name, error=detail, verification_state='failed')
        suffix = '\n' + recalled['context'] if recalled.get('context') else ''
        return "REMOTE TOOL ERROR: " + detail + suffix
    if not texts:
        return "REMOTE TOOL ERROR: invalid MCP result (missing text content)"
    suffix = "\n" + recalled["context"] if recalled.get("context") else ""
    return "\n".join(texts) + suffix


def _build_remote_tools(connection: ClientConnection) -> List[Callable[..., Awaitable[str]]]:
    """Create Antigravity custom tools bound to one AICoder connection."""

    async def client_file_read(path: str, start_line: int = 1, end_line: int = 400) -> str:
        """Read a UTF-8 text file from the connected AICoder workspace."""
        return await _call_client_tool(
            connection,
            "client_file_read",
            {"path": path, "start_line": start_line, "end_line": end_line},
        )

    async def client_file_list(path: str = ".", recursive: bool = False) -> str:
        """List files below a directory in the connected AICoder workspace."""
        return await _call_client_tool(
            connection,
            "client_file_list",
            {"path": path, "recursive": recursive},
        )

    async def client_codebase_search(
        query: str,
        path: str = ".",
        file_pattern: str = "*",
    ) -> str:
        """Search text/regex across files in the connected AICoder workspace."""
        return await _call_client_tool(
            connection,
            "client_codebase_search",
            {"query": query, "path": path, "file_pattern": file_pattern},
        )

    async def client_git_status(path: str = ".") -> str:
        """Read git status for a repository in the connected AICoder workspace."""
        return await _call_client_tool(connection, "client_git_status", {"path": path})

    async def client_file_edit(
        path: str,
        operation: str,
        content: str = "",
        old_text: str = "",
        new_text: str = "",
    ) -> str:
        """Create a new file or exact-replace one unique text span on an opted-in AICoder node."""
        arguments: Dict[str, Any] = {"path": path, "operation": operation}
        if operation == "create":
            arguments["content"] = content
        elif operation == "replace":
            arguments["old_text"] = old_text
            arguments["new_text"] = new_text
        return await _call_client_tool(connection, "client_file_edit", arguments)

    candidates: Dict[str, Callable[..., Awaitable[str]]] = {
        "client_file_read": client_file_read,
        "client_file_list": client_file_list,
        "client_codebase_search": client_codebase_search,
        "client_git_status": client_git_status,
        "client_file_edit": client_file_edit,
    }
    advertised = set(connection.supported_tools or []) & REMOTE_MODEL_TOOLS
    return [tool for name, tool in candidates.items() if name in advertised]


async def _read_worker_stderr(stream, sink: list[str]) -> None:
    if stream is None:
        return
    while True:
        chunk = await stream.read(4096)
        if not chunk:
            return
        # Bound memory and drain even diagnostics without newlines.
        sink.append(chunk.decode("utf-8", errors="replace"))
        del sink[:-40]


def normalize_antigravity_model_base_url(value: str) -> str:
    """Normalize the SDK 0.1.9 LocalOpenAI base URL contract.

    The bundled harness appends ``/v1/chat/completions`` itself. Accept the
    conventional OpenAI-compatible ``.../v1`` form as input without producing
    the broken ``.../v1/v1/chat/completions`` path.
    """
    base = str(value or "").strip().rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return base


async def _run_antigravity_worker(
    connection: ClientConnection,
    *,
    task: str,
    model: str,
    system: str,
    run_id: str,
    timeout: float = 180.0,
) -> Dict[str, Any]:
    """Run Antigravity 0.1.9 in its isolated venv and proxy remote tool RPC."""
    import asyncio
    import json
    import os
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    python_bin = Path(os.getenv(
        "TRIFORCE_ANTIGRAVITY_PYTHON",
        str(repo_root / ".venv-antigravity" / "bin" / "python"),
    ))
    worker = Path(os.getenv(
        "TRIFORCE_ANTIGRAVITY_WORKER",
        str(repo_root / "scripts" / "antigravity_remote_worker.py"),
    ))
    if not python_bin.is_file():
        raise RuntimeError(
            "isolated Antigravity runtime missing; create .venv-antigravity from requirements-antigravity.txt"
        )
    if not worker.is_file():
        raise RuntimeError(f"Antigravity worker missing: {worker}")

    proc = await asyncio.create_subprocess_exec(
        str(python_bin), str(worker),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(repo_root),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    assert proc.stdin is not None and proc.stdout is not None
    stderr_lines: list[str] = []
    stderr_task = asyncio.create_task(_read_worker_stderr(proc.stderr, stderr_lines))
    prefix = b"TRIFORCE_ANTIGRAVITY_RPC "

    async def send(payload: Dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    model_base_url = normalize_antigravity_model_base_url(
        os.getenv("TRIFORCE_ANTIGRAVITY_MODEL_BASE_URL", "http://127.0.0.1:9000")
    )
    start_message = {
        "type": "start",
        "task": task,
        "model": model,
        "base_url": model_base_url,
        "system": system,
        "tools": sorted(set(connection.supported_tools or []) & REMOTE_MODEL_TOOLS),
    }

    async def exchange() -> Dict[str, Any]:
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                code = await proc.wait()
                detail = "\n".join(stderr_lines[-8:])
                raise RuntimeError(f"Antigravity worker exited {code}: {detail}".strip())
            if not raw.startswith(prefix):
                continue
            try:
                message = json.loads(raw[len(prefix):])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise RuntimeError("Invalid Antigravity RPC JSON") from exc
            if not isinstance(message, dict):
                raise RuntimeError("Invalid Antigravity RPC message: expected an object")
            kind = message.get("type")
            if kind == "tool_call":
                call_id = str(message.get("id") or "")
                name = str(message.get("name") or "")
                args = message.get("arguments")
                if not call_id or not isinstance(args, dict):
                    raise RuntimeError("Invalid Antigravity RPC tool call")
                try:
                    if name not in REMOTE_MODEL_TOOLS:
                        raise PermissionError(f"worker cannot invoke tool: {name}")
                    remote_args = dict(args)
                    remote_args["_run_id"] = run_id
                    remote_args["_task"] = task
                    remote_args["_model"] = model
                    result = await _call_client_tool(connection, name, remote_args)
                    await send({"type": "tool_result", "id": call_id, "result": result})
                except Exception as exc:
                    await send({"type": "tool_result", "id": call_id, "error": str(exc)})
            elif kind == "final":
                if not isinstance(message.get("response"), str) or not message["response"].strip():
                    raise RuntimeError("Invalid Antigravity final response")
                return message
            elif kind == "error":
                raise RuntimeError(str(message.get("message") or "Antigravity worker failed"))
            else:
                raise RuntimeError(f"Invalid Antigravity RPC message type: {kind}")

    try:
        async with asyncio.timeout(max(0.01, min(300.0, timeout))):
            await send(start_message)
            result = await exchange()
            proc.stdin.close()
            code = await asyncio.wait_for(proc.wait(), timeout=3)
            if code != 0:
                raise RuntimeError(f"Antigravity worker exited {code}")
            if "client_run_state" in set(connection.supported_tools or []):
                acknowledgement = await _call_client_tool(connection, "client_run_state", {
                    "_run_id": run_id,
                    "_task": task,
                    "_model": model,
                    "status": "completed",
                    "response": result["response"],
                })
                if acknowledgement.startswith("REMOTE TOOL ERROR:"):
                    raise RuntimeError(f"Remote run state was not persisted: {acknowledgement}")
    except (Exception, asyncio.CancelledError) as exc:
        if "client_run_state" in set(connection.supported_tools or []):
            try:
                acknowledgement = await _call_client_tool(connection, "client_run_state", {
                    "_run_id": run_id,
                    "_task": task,
                    "_model": model,
                    "status": "paused",
                    "reason": str(exc) or type(exc).__name__,
                }, timeout=3.0)
                if acknowledgement.startswith("REMOTE TOOL ERROR:"):
                    logger.error("failed to persist remote coding pause state for %s: %s", run_id, acknowledgement)
            except Exception:
                logger.exception("failed to persist remote coding pause state for %s", run_id)
        raise
    finally:
        if proc.stdin and not proc.stdin.is_closing():
            proc.stdin.close()
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass  # The child exited between the returncode check and signal.
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
        # A descendant may still hold stderr open after the worker exits.
        try:
            await asyncio.wait_for(stderr_task, timeout=3)
        except asyncio.CancelledError:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            raise
        except Exception:
            stderr_task.cancel()
            await asyncio.gather(stderr_task, return_exceptions=True)
            logger.warning("Antigravity stderr reader did not finish cleanly for %s", run_id)
    return result


async def run_remote_coding_agent(
    *,
    client_id: str,
    task: str,
    model: Optional[str] = None,
    system: str = "",
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run one isolated Antigravity turn against an explicitly advertised AICoder tool surface."""
    import os
    import re
    import uuid

    if not task.strip():
        raise ValueError("task is required")
    connection = resolve_remote_coding_node(client_id)
    advertised = set(connection.supported_tools or []) & REMOTE_MODEL_TOOLS
    if not advertised:
        raise RuntimeError("AICoder node exposes no compatible remote coding tools")

    selected_run_id = str(run_id or f"remote-{uuid.uuid4().hex}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", selected_run_id):
        raise ValueError("invalid run_id")

    selected_model = model or os.getenv(
        "TRIFORCE_ANTIGRAVITY_MODEL",
        "ollama/gemma4:12b",
    )
    # Registry aliases share this physical connection and its execution guard.
    # Claim before the first await; release on failure and cancellation as well.
    if connection.remote_coding_run_id is not None:
        raise RuntimeError("A remote coding run is already running on this node")
    connection.remote_coding_run_id = selected_run_id
    try:
        from .memory_runtime import remote_recall
        recalled = await remote_recall(connection, 'run_resumed' if run_id else 'task_started',
                                       run_id=selected_run_id, task=task, model=selected_model)
        if recalled.get('context'):
            system = system + '\n\n' + recalled['context']
        worker_result = await _run_antigravity_worker(
            connection,
            task=task,
            model=selected_model,
            system=system,
            run_id=selected_run_id,
        )
    finally:
        connection.remote_coding_run_id = None
    from .memory_runtime import remote_record
    await remote_record(connection, 'run_completed', run_id=selected_run_id, task=task, model=selected_model,
                        findings=str(worker_result.get("response") or "")[:4000], verification_state='completed')
    node = _node_view(client_id, connection)
    return {
        "status": "completed",
        "run_id": selected_run_id,
        "runtime": "antigravity-sdk-sidecar",
        "mode": node.profile or "read-only-light",
        "client": node.to_dict(),
        "model": selected_model,
        "response": str(worker_result.get("response") or ""),
        "usage": worker_result.get("usage"),
    }
