#!/usr/bin/env python3
"""Standalone anonymous local-workspace node for TriForce MCP."""
from __future__ import annotations

import argparse
import asyncio
import json
import platform
import signal
import socket
import os
import hashlib
import urllib.request
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


def _state_file(root: Path) -> Path:
    key = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]
    return Path.home() / ".config" / "ailinux" / "workspace-sessions" / f"{key}.json"


def _load_state(root: Path) -> dict:
    path = _state_file(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(root: Path, data: dict) -> None:
    path = _state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def _clear_state(root: Path) -> None:
    try:
        _state_file(root).unlink()
    except FileNotFoundError:
        pass


def _resume_ticket(base_url: str, token: str) -> str:
    body = json.dumps({"resume_token": token}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/v1/mcp/workspace/resume-ticket",
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ticket = str(data.get("pair_code") or "")
    if not ticket:
        raise RuntimeError("server returned no workspace resume ticket")
    return ticket


class WorkspaceNode:
    def __init__(self, base_url: str, runtime: WorkspaceRuntime, pair_code: str = "", resume_token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.runtime = runtime
        self.pair_code = pair_code.strip().upper()
        self.resume_token = str(resume_token or "").strip()
        if len(self.pair_code) < 8 and not self.resume_token:
            raise ValueError("pairing code or resume token is required")
        self.stop_event = asyncio.Event()
        self.forget_on_exit = False

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
                "access_mode": self.runtime.mode,
                "capabilities": self.tool_names,
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
        if method in {"workspace/shared", "workspace/paired", "workspace/revoked", "pong"}:
            if method in {"workspace/shared", "workspace/paired"}:
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                if not bool(params.get("ok", False)):
                    raise RuntimeError(str(params.get("error") or "workspace pairing failed"))
                token = str(params.get("resume_token") or "").strip()
                if token:
                    self.resume_token = token
                    _save_state(self.runtime.root, {
                        "server": self.base_url,
                        "workspace": str(self.runtime.root),
                        "mode": self.runtime.mode,
                        "task": self.runtime.task,
                        "resume_token": token,
                    })
                print(json.dumps({
                    "event": "workspace_paired" if method == "workspace/paired" else "workspace_shared",
                    "waiting_for_session": bool(params.get("waiting_for_session", False)),
                    "mode": str(params.get("mode") or self.runtime.mode),
                    "lease_id": str(params.get("lease_id") or ""),
                    "persistent": bool(self.resume_token),
                }), flush=True)
            if method == "workspace/revoked":
                _clear_state(self.runtime.root)
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

        delay = 1.0
        while not self.stop_event.is_set():
            credential = self.pair_code
            if self.resume_token:
                credential = await asyncio.to_thread(_resume_ticket, self.base_url, self.resume_token)
            url = node_url(self.base_url, credential)
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, close_timeout=5, max_size=2 * 1024 * 1024) as websocket:
                    delay = 1.0
                    print(json.dumps({
                        "event": "connected",
                        "mcp_url": mcp_url(self.base_url),
                        "mode": self.runtime.mode,
                        "workspace": str(self.runtime.root),
                        "task": self.runtime.task,
                        "analysis": self.runtime.analyze(),
                        "resumed": bool(self.resume_token),
                    }, ensure_ascii=False), flush=True)
                    while not self.stop_event.is_set():
                        try:
                            raw = await asyncio.wait_for(websocket.recv(), timeout=25)
                        except asyncio.TimeoutError:
                            await websocket.send(json.dumps({"jsonrpc": "2.0", "method": "ping"}))
                            continue
                        try:
                            message = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if isinstance(message, dict):
                            await self._handle(websocket, message)
                    if self.forget_on_exit:
                        await self._revoke(websocket)
                    return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                if not self.resume_token:
                    raise
                print(json.dumps({"event": "reconnecting", "error": str(exc), "delay_seconds": delay}), flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, 30.0)



def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="triforce-workspace", description="Pair one local folder with a public TriForce MCP session")
    p.add_argument("--workspace", required=True, help="Local folder to expose")
    p.add_argument("--pair", required=False, default="", help="One-time pairing code returned by workspace_status")
    p.add_argument("--forget", action="store_true", help="Forget a saved persistent workspace session and exit")
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
    if args.forget:
        _clear_state(runtime.root)
        print(json.dumps({"event": "workspace_session_forgotten", "workspace": str(runtime.root)}))
        return 0
    saved = _load_state(runtime.root)
    resume_token = str(saved.get("resume_token") or "")
    server = str(saved.get("server") or args.server) if resume_token and not args.pair.strip() else args.server
    if not args.pair.strip() and not resume_token:
        print("workspace error: --pair is required once; later runs resume automatically", file=sys.stderr)
        return 2
    try:
        node = WorkspaceNode(server, runtime, args.pair, resume_token=resume_token if not args.pair.strip() else "")
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
