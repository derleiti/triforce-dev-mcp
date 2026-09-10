#!/usr/bin/env python3
"""Standalone anonymous local-workspace node for TriForce MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import signal
import socket
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

from .runtime import READ_TOOLS, WRITE_TOOLS, WorkspaceRuntime


def node_url(base_url: str, pair_code: str) -> str:
    parsed = urlsplit(base_url.rstrip("/"))
    if parsed.scheme not in {"http", "https", "ws", "wss"}:
        raise ValueError("server URL must use http(s) or ws(s)")
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    path = parsed.path.rstrip("/") + "/v1/mcp/node/connect"
    query = urlencode({
        "mode": "workspace",
        "pair_code": pair_code.strip().upper(),
        "machine_id": socket.gethostname(),
        "client_version": "2.85-workspace",
    })
    return urlunsplit((scheme, parsed.netloc, path, query, ""))


def mcp_url(base_url: str) -> str:
    return base_url.rstrip("/") + "/v1/mcp"


class WorkspaceNode:
    def __init__(self, base_url: str, runtime: WorkspaceRuntime, pair_code: str):
        self.base_url = base_url.rstrip("/")
        self.runtime = runtime
        self.pair_code = pair_code.strip().upper()
        if len(self.pair_code) < 8:
            raise ValueError("pairing code is required")
        self.stop_event = asyncio.Event()

    @property
    def tool_names(self) -> list[str]:
        return sorted(WRITE_TOOLS if self.runtime.writable else READ_TOOLS)

    async def _announce(self, websocket) -> None:
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "client/info",
            "params": {
                "client": "triforce-workspace",
                "platform": platform.system().lower(),
                "hostname": socket.gethostname(),
                "server_version": "2.85-workspace",
                "mode": "workspace",
                "workspace": "local",
                "remote_profile": self.runtime.mode,
            },
        }))
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/list",
            "params": {"tools": ["client_workspace_tool"]},
        }))
        await websocket.send(json.dumps({
            "jsonrpc": "2.0",
            "method": "workspace/share",
            "params": {
                "task": self.runtime.task,
                "mode": self.runtime.mode,
            },
        }))

    async def _revoke(self, websocket) -> None:
        try:
            await websocket.send(json.dumps({"jsonrpc": "2.0", "method": "workspace/revoke", "params": {}}))
            await asyncio.sleep(0.05)
        except Exception:
            pass

    async def _handle(self, websocket, message: dict) -> None:
        method = message.get("method")
        if method == "connected":
            await self._announce(websocket)
            return
        if method in {"workspace/shared", "workspace/revoked", "pong"}:
            if method == "workspace/shared" and not bool((message.get("params") or {}).get("ok", False)):
                raise RuntimeError(str((message.get("params") or {}).get("error") or "workspace pairing failed"))
            return
        if method != "tools/call":
            return
        request_id = message.get("id")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("name") != "client_workspace_tool":
            result = {"content": [{"type": "text", "text": "only client_workspace_tool is accepted"}], "isError": True}
        else:
            outer = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            tool = str(outer.get("tool") or "")
            arguments = outer.get("arguments") if isinstance(outer.get("arguments"), dict) else {}
            requested_mode = str(outer.get("mode") or "read_only")
            if requested_mode == "write" and not self.runtime.writable:
                result = {"content": [{"type": "text", "text": "local workspace is read-only"}], "isError": True}
            else:
                result = await asyncio.to_thread(self.runtime.execute, tool, arguments)
        await websocket.send(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False))

    async def run(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Python package 'websockets' is required") from exc
        url = node_url(self.base_url, self.pair_code)
        async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2 * 1024 * 1024) as websocket:
            print(json.dumps({
                "event": "connected",
                "mcp_url": mcp_url(self.base_url),
                "pair_code": self.pair_code,
                "mode": self.runtime.mode,
                "workspace": str(self.runtime.root),
                "task": self.runtime.task,
                "analysis": self.runtime.analyze(),
            }, ensure_ascii=False), flush=True)
            try:
                while not self.stop_event.is_set():
                    recv_task = asyncio.create_task(websocket.recv())
                    stop_task = asyncio.create_task(self.stop_event.wait())
                    done, _pending = await asyncio.wait({recv_task, stop_task}, timeout=25, return_when=asyncio.FIRST_COMPLETED)
                    if stop_task in done and stop_task.result():
                        recv_task.cancel()
                        try:
                            await recv_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        break
                    stop_task.cancel()
                    if recv_task not in done:
                        recv_task.cancel()
                        try:
                            await recv_task
                        except (asyncio.CancelledError, Exception):
                            pass
                        await websocket.send(json.dumps({"jsonrpc": "2.0", "method": "ping"}))
                        continue
                    raw = recv_task.result()
                    try:
                        message = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(message, dict):
                        await self._handle(websocket, message)
            finally:
                await self._revoke(websocket)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="triforce-workspace", description="Pair one local folder with a public TriForce MCP session")
    p.add_argument("--workspace", required=True, help="Local folder to expose")
    p.add_argument("--pair", required=False, default="", help="Pairing code returned by workspace_status")
    p.add_argument("--task", default="", help="Task/instructions for the connected AI")
    p.add_argument("--server", default="https://api.ailinux.me", help="TriForce server base URL")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--read-only", action="store_true", help="Read-only access (default)")
    mode.add_argument("--write", action="store_true", help="Allow edits and sandboxed local execution")
    p.add_argument("--analyze", action="store_true", help="Analyze the folder locally and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = WorkspaceRuntime(Path(args.workspace), writable=bool(args.write), task=args.task)
    except Exception as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2
    if args.analyze:
        print(json.dumps(runtime.analyze(), ensure_ascii=False, indent=2))
        return 0
    if not args.pair.strip():
        print("workspace error: --pair is required (ask TriForce workspace_status for the code)", file=sys.stderr)
        return 2
    try:
        node = WorkspaceNode(args.server, runtime, args.pair)
    except Exception as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2

    async def runner() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, node.stop_event.set)
            except NotImplementedError:
                pass
        await node.run()

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"workspace node error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
