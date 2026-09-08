from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import remote_coding_agent as service
from app.routes import mcp_node
from fastapi import HTTPException, WebSocketDisconnect
from types import SimpleNamespace


class FakeTier:
    value = "pro"


class FakeConnection:
    def __init__(self, *, writes: bool = False):
        self.client_id = "client-1"
        self.user_id = "user@example.test"
        self.tier = FakeTier()
        self.mode = "full"
        self.remote_coding_run_id = None
        self.client_info = {
            "client": "aicoder",
            "hostname": "workstation",
            "workspace": "/work/repo",
            "remote_profile": "write-preview" if writes else "read-only-light",
        }
        tools = set(service.REMOTE_READ_TOOLS) | service.REMOTE_CONTROL_TOOLS
        if writes:
            tools |= service.REMOTE_WRITE_TOOLS
        self.supported_tools = sorted(tools)
        self.send_tool_call = AsyncMock(return_value={
            "content": [{"type": "text", "text": "hello"}],
            "isError": False,
        })


def test_lists_only_full_aicoder_nodes(monkeypatch):
    good = FakeConnection()
    other = FakeConnection()
    other.client_id = "other"
    other.client_info = {"client": "something-else"}
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": good, "alias": good, "other": other})
    rows = service.list_remote_coding_nodes()
    assert len(rows) == 1
    assert rows[0].client_id == "client-1"
    assert rows[0].workspace == "/work/repo"
    assert rows[0].profile == "read-only-light"
    assert set(rows[0].supported_tools) == service.REMOTE_READ_TOOLS | service.REMOTE_CONTROL_TOOLS


@pytest.mark.asyncio
async def test_remote_tool_proxy_requires_explicit_advertisement():
    conn = FakeConnection()
    text = await service._call_client_tool(conn, "client_file_read", {"path": "README.md"})
    assert text == "hello"
    conn.send_tool_call.assert_awaited_once()

    with pytest.raises(LookupError):
        await service._call_client_tool(
            conn,
            "client_file_edit",
            {"path": "x", "operation": "create", "content": "x"},
        )
    with pytest.raises(PermissionError):
        await service._call_client_tool(conn, "client_shell", {"command": "id"})


@pytest.mark.asyncio
async def test_remote_write_error_is_returned_as_untrusted_tool_error():
    conn = FakeConnection(writes=True)
    conn.send_tool_call = AsyncMock(return_value={
        "content": [{"type": "text", "text": "exact match failed"}],
        "isError": True,
    })
    text = await service._call_client_tool(
        conn,
        "client_file_edit",
        {"path": "x.py", "operation": "replace", "old_text": "a", "new_text": "b"},
    )
    assert text == "REMOTE TOOL ERROR: exact match failed"


def test_custom_tool_set_matches_only_advertised_tools():
    read_conn = FakeConnection()
    read_tools = service._build_remote_tools(read_conn)
    assert {tool.__name__ for tool in read_tools} == service.REMOTE_READ_TOOLS

    write_conn = FakeConnection(writes=True)
    write_tools = service._build_remote_tools(write_conn)
    assert {tool.__name__ for tool in write_tools} == service.REMOTE_MODEL_TOOLS
    assert "client_run_state" not in {tool.__name__ for tool in write_tools}

    empty = FakeConnection()
    empty.supported_tools = []
    assert service._build_remote_tools(empty) == []


@pytest.mark.asyncio
async def test_file_edit_wrapper_forwards_only_preview_operations():
    conn = FakeConnection(writes=True)
    tools = {tool.__name__: tool for tool in service._build_remote_tools(conn)}
    await tools["client_file_edit"](
        "src/x.py",
        "replace",
        old_text="before",
        new_text="after",
    )
    conn.send_tool_call.assert_awaited_once_with(
        "client_file_edit",
        {
            "path": "src/x.py",
            "operation": "replace",
            "old_text": "before",
            "new_text": "after",
        },
        timeout=60.0,
    )


def test_antigravity_sdk_is_isolated_from_core_requirements():
    core = open("requirements.txt", encoding="utf-8").read()
    isolated = open("requirements-antigravity.txt", encoding="utf-8").read()
    assert not any(line.strip().startswith("google-antigravity") for line in core.splitlines())
    assert "google-antigravity==0.1.13" in isolated


def test_antigravity_base_url_accepts_openai_v1_form():
    assert service.normalize_antigravity_model_base_url("http://127.0.0.1:9000/v1") == "http://127.0.0.1:9000"
    assert service.normalize_antigravity_model_base_url("http://127.0.0.1:9000/v1/") == "http://127.0.0.1:9000"
    assert service.normalize_antigravity_model_base_url("http://127.0.0.1:9000") == "http://127.0.0.1:9000"


@pytest.mark.asyncio
async def test_run_id_is_forwarded_and_control_tool_stays_internal(monkeypatch):
    conn = FakeConnection(writes=True)
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": conn})
    worker = AsyncMock(return_value={"response": "DONE: ok", "usage": None})
    monkeypatch.setattr(service, "_run_antigravity_worker", worker)

    result = await service.run_remote_coding_agent(
        client_id="client-1",
        task="change safely",
        model="test/model",
        run_id="remote-resume-1",
    )

    assert result["run_id"] == "remote-resume-1"
    worker.assert_awaited_once()
    assert worker.await_args.kwargs["run_id"] == "remote-resume-1"


def test_invalid_run_id_is_rejected_before_worker(monkeypatch):
    conn = FakeConnection(writes=True)
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": conn})
    with pytest.raises(ValueError, match="invalid run_id"):
        import asyncio
        asyncio.run(service.run_remote_coding_agent(
            client_id="client-1",
            task="x",
            run_id="bad/run/id",
        ))


@pytest.mark.parametrize("result", [None, [], {}, {"content": []},
    {"content": [{"type": "text", "text": None}]},
    {"isError": True}, {"error": {"message": "disconnected"}}])
async def test_invalid_remote_results_are_explicit_errors(result):
    conn = FakeConnection()
    conn.send_tool_call.return_value = result
    text = await service._call_client_tool(conn, "client_file_read", {"path": "x"})
    assert text.startswith("REMOTE TOOL ERROR:")


@pytest.fixture
def isolated_worker(monkeypatch, tmp_path):
    """Run the real parent RPC/cleanup loop against an offline child process."""
    worker = tmp_path / "worker.py"
    monkeypatch.setenv("TRIFORCE_ANTIGRAVITY_PYTHON", sys.executable)
    monkeypatch.setenv("TRIFORCE_ANTIGRAVITY_WORKER", str(worker))
    processes = []
    create = asyncio.create_subprocess_exec

    async def record_process(*args, **kwargs):
        proc = await create(*args, **kwargs)
        processes.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", record_process)

    def configure(body):
        worker.write_text(
            "import sys, json, time\n"
            "def emit(value):\n"
            "    print('TRIFORCE_ANTIGRAVITY_RPC ' + json.dumps(value), flush=True)\n"
            "start = json.loads(sys.stdin.readline())\n" + body,
            encoding="utf-8",
        )
        return processes

    return configure


async def run_worker(conn, **kwargs):
    return await service._run_antigravity_worker(
        conn, task="inspect", model="test/model", system="", run_id="test-run", **kwargs,
    )


@pytest.mark.parametrize("body,match", [
    ('emit({"type": "final", "response": "done"})\nsys.exit(7)\n', "exited 7"),
    ('emit({"type": "final"})\n', "final"),
    ('emit([])\n', "RPC"),
    ("print('TRIFORCE_ANTIGRAVITY_RPC {broken', flush=True)\n", "RPC"),
])
async def test_worker_rejects_false_success_and_invalid_rpc(isolated_worker, body, match):
    processes = isolated_worker(body)
    conn = FakeConnection()
    with pytest.raises(RuntimeError, match=match):
        await run_worker(conn)
    assert processes[0].returncode is not None
    assert not any(call.args[1].get("status") == "completed"
                   for call in conn.send_tool_call.await_args_list)


async def test_worker_success_requires_state_acknowledgement(isolated_worker):
    processes = isolated_worker('emit({"type": "final", "response": "done"})\n')
    conn = FakeConnection()
    conn.send_tool_call.return_value = {"isError": True, "content": []}
    with pytest.raises(RuntimeError, match="state"):
        await run_worker(conn)
    assert processes[0].returncode == 0


async def test_worker_timeout_and_cancellation_reap_child(isolated_worker):
    processes = isolated_worker('time.sleep(60)\n')
    conn = FakeConnection()
    with pytest.raises(TimeoutError):
        await run_worker(conn, timeout=0.05)
    assert processes[0].returncode is not None

    started = asyncio.Event()
    original = asyncio.create_subprocess_exec

    async def notify_start(*args, **kwargs):
        proc = await original(*args, **kwargs)
        started.set()
        return proc

    from unittest.mock import patch
    with patch.object(asyncio, "create_subprocess_exec", notify_start):
        pending = asyncio.create_task(run_worker(conn))
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert all(proc.returncode is not None for proc in processes)


async def test_default_remote_model_uses_gemma(monkeypatch):
    conn = FakeConnection()
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": conn})
    monkeypatch.delenv("TRIFORCE_ANTIGRAVITY_MODEL", raising=False)
    worker = AsyncMock(return_value={"response": "done"})
    monkeypatch.setattr(service, "_run_antigravity_worker", worker)
    result = await service.run_remote_coding_agent(client_id="client-1", task="inspect")
    assert result["model"] == "ollama/gemma4:12b"


async def test_control_only_node_is_not_runnable(monkeypatch):
    conn = FakeConnection()
    conn.supported_tools = ["client_run_state"]
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": conn})
    assert service.list_remote_coding_nodes() == []
    worker = AsyncMock()
    monkeypatch.setattr(service, "_run_antigravity_worker", worker)
    with pytest.raises(RuntimeError, match="no compatible"):
        await service.run_remote_coding_agent(client_id="client-1", task="inspect")
    worker.assert_not_awaited()


async def test_worker_start_send_failure_reaps_child(isolated_worker, monkeypatch):
    processes = isolated_worker('time.sleep(60)\n')
    original = asyncio.create_subprocess_exec

    async def broken_pipe(*args, **kwargs):
        proc = await original(*args, **kwargs)
        monkeypatch.setattr(proc.stdin, "drain", AsyncMock(side_effect=BrokenPipeError("closed")))
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", broken_pipe)
    with pytest.raises(BrokenPipeError):
        await run_worker(FakeConnection())
    assert processes[0].returncode is not None


async def test_successful_worker_proxies_tools_and_drains_large_stderr(isolated_worker):
    processes = isolated_worker(
        'sys.stderr.write("x" * 200000)\nsys.stderr.flush()\n'
        'emit({"type": "tool_call", "id": "read-1", "name": "client_file_read", '
        '"arguments": {"path": "README.md"}})\n'
        'result = json.loads(sys.stdin.readline())\n'
        'assert result["id"] == "read-1" and result["result"] == "hello"\n'
        'emit({"type": "final", "response": "inspected"})\n'
    )
    conn = FakeConnection()
    result = await run_worker(conn)
    assert result["response"] == "inspected"
    assert processes[0].returncode == 0
    assert conn.send_tool_call.await_args_list[0].args[1]["_run_id"] == "test-run"
    assert conn.send_tool_call.await_args_list[-1].args[1]["status"] == "completed"


@pytest.fixture
def worker_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "antigravity_remote_worker.py"
    spec = importlib.util.spec_from_file_location("remote_worker_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_worker_serializes_concurrent_rpc_readers(worker_module, monkeypatch):
    active = 0
    maximum = 0
    emitted = {}

    def emit(message):
        emitted[asyncio.current_task()] = message

    async def read_message():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        # Yield once to expose competing readers, without wall-clock timing.
        await asyncio.sleep(0)
        active -= 1
        message = emitted[asyncio.current_task()]
        return {"type": "tool_result", "id": message["id"], "result": message["name"]}

    monkeypatch.setattr(worker_module, "emit", emit)
    monkeypatch.setattr(worker_module, "read_message", read_message)
    results = await asyncio.gather(
        worker_module.remote_tool("read-a", {}), worker_module.remote_tool("read-b", {}),
    )
    assert results == ["read-a", "read-b"]
    assert maximum == 1, "stdin is a shared stream; each RPC must own its response"


@pytest.fixture
def offline_node_chat(monkeypatch):
    from app.routes import client_chat
    for name in ("call_ollama", "call_openrouter"):
        monkeypatch.setattr(client_chat, name, AsyncMock(return_value={}))


@pytest.mark.parametrize("endpoint", ["list", "call", "chat"])
async def test_node_endpoints_reject_invalid_tokens(monkeypatch, endpoint, offline_node_chat):
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: (_ for _ in ()).throw(ValueError("invalid")))
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", {"victim": FakeConnection()})
    with pytest.raises(HTTPException) as error:
        if endpoint == "list":
            await mcp_node.list_connected_clients(authorization="Bearer bad")
        elif endpoint == "call":
            await mcp_node.call_client_tool(mcp_node.ProxyToolRequest(client_id="victim", tool="client_file_read"), authorization="Bearer bad")
        else:
            await mcp_node.chat_with_client_files("victim", "read files", authorization="Bearer bad")
    assert error.value.status_code == 401


@pytest.mark.parametrize("endpoint", ["call", "chat"])
async def test_node_owner_is_required_before_remote_execution(monkeypatch, endpoint, offline_node_chat):
    conn = FakeConnection()
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", {"victim": conn})
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": "attacker"})
    with pytest.raises(HTTPException) as error:
        if endpoint == "call":
            await mcp_node.call_client_tool(mcp_node.ProxyToolRequest(client_id="victim", tool="client_file_read"), authorization="Bearer valid")
        else:
            await mcp_node.chat_with_client_files("victim", "read files", authorization="Bearer valid")
    assert error.value.status_code == 404
    conn.send_tool_call.assert_not_awaited()


async def test_node_listing_is_account_scoped_and_deduplicated(monkeypatch):
    own = mcp_node.ClientConnection("own", "owner", None, mcp_node.UserTier.PRO)
    other = mcp_node.ClientConnection("other", "other-owner", None, mcp_node.UserTier.PRO)
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", {"own": own, "alias": own, "other": other})
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": "owner"})
    result = await mcp_node.list_connected_clients(authorization="Bearer valid")
    assert result["count"] == 1
    assert result["clients"][0]["client_id"] == "own"


async def test_node_timeout_covers_websocket_send():
    async def blocked_send(request):
        await asyncio.Future()

    conn = mcp_node.ClientConnection("own", "owner", SimpleNamespace(send_json=blocked_send), mcp_node.UserTier.PRO)
    with pytest.raises(HTTPException) as error:
        async with asyncio.timeout(0.5):
            await conn.send_tool_call("client_file_read", {}, timeout=0.01)
    assert error.value.status_code == 504
    assert conn.pending_requests == {}


@pytest.mark.parametrize("reply", [{"error": None}, {"error": "failed"}, {}])
async def test_malformed_node_reply_fails_pending_call(reply):
    conn = None

    async def send(request):
        conn.handle_response({"id": request["id"], **reply})

    conn = mcp_node.ClientConnection("own", "owner", SimpleNamespace(send_json=send), mcp_node.UserTier.PRO)
    with pytest.raises(RuntimeError):
        await conn.send_tool_call("client_file_read", {}, timeout=0.01)
    assert conn.pending_requests == {}


@pytest.mark.parametrize("anonymous", [False, True])
async def test_websocket_cannot_replace_another_account_alias(monkeypatch, anonymous):
    victim = FakeConnection()
    registry = {"victim-session": victim}
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", registry)
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": "attacker", "client_id": "attacker-client"})
    monkeypatch.setattr(mcp_node.tier_service, "get_user_tier", lambda user: mcp_node.UserTier.GUEST)
    ws = SimpleNamespace(
        accept=AsyncMock(), send_json=AsyncMock(), close=AsyncMock(),
        query_params={"mode": "telemetry" if anonymous else "full"},
        receive_json=AsyncMock(side_effect=WebSocketDisconnect()),
    )
    await mcp_node.websocket_connect(ws, token=None if anonymous else "valid", session_id="victim-session", user_id=victim.user_id, tier="enterprise")
    assert registry.get("victim-session") is victim
    for call in ws.send_json.await_args_list:
        if call.args[0].get("method") == "connected":
            assert call.args[0]["params"]["tier"] != "enterprise"


async def test_disconnect_fails_waiters_and_late_replies_are_ignored():
    sent = asyncio.Event()
    request_ids = []

    async def send(request):
        request_ids.append(request["id"])
        sent.set()

    conn = mcp_node.ClientConnection("own", "owner", SimpleNamespace(send_json=send), mcp_node.UserTier.PRO)
    pending = asyncio.create_task(conn.send_tool_call("client_file_read", {}))
    await sent.wait()
    conn.disconnect()
    with pytest.raises(ConnectionError, match="disconnected"):
        await pending
    conn.handle_response({"id": request_ids[0], "result": {}})
    conn.handle_response({"id": [], "error": None})
    assert conn.pending_requests == {}
    with pytest.raises(HTTPException):
        await conn.send_tool_call("client_file_read", {})


async def test_old_socket_cleanup_preserves_reconnected_session(monkeypatch):
    registry = {}
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", registry)
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": "owner", "client_id": "own"})
    monkeypatch.setattr(mcp_node.tier_service, "get_user_tier", lambda user: mcp_node.UserTier.PRO)
    entered = [asyncio.Event(), asyncio.Event()]

    def socket(index):
        async def receive():
            entered[index].set()
            await asyncio.Future()
        return SimpleNamespace(accept=AsyncMock(), send_json=AsyncMock(), close=AsyncMock(), query_params={}, receive_json=receive)

    first = asyncio.create_task(mcp_node.websocket_connect(socket(0), token="valid", session_id="alias"))
    second = None
    try:
        await entered[0].wait()
        old = registry["own"]
        second = asyncio.create_task(mcp_node.websocket_connect(socket(1), token="valid", session_id="alias"))
        await entered[1].wait()
        new = registry["own"]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert old.closed
        assert new is not old
        assert registry["own"] is new and registry["alias"] is new
    finally:
        for pending in (first, second):
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert registry == {}


@pytest.mark.parametrize("is_error", [False, True])
async def test_node_proxy_preserves_tool_error_status(monkeypatch, is_error):
    conn = FakeConnection()
    conn.send_tool_call.return_value["isError"] = is_error
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", {conn.client_id: conn})
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": conn.user_id})
    result = await mcp_node.call_client_tool(
        mcp_node.ProxyToolRequest(client_id=conn.client_id, tool="client_file_read"), authorization="Bearer valid",
    )
    assert result.success is not is_error
    conn.send_tool_call.assert_awaited_once()


@pytest.mark.parametrize("verification,expected_code", [("other-file", 6), ("git", 6), ("same-file", 0)])
async def test_write_verification_requires_reading_the_edited_path(worker_module, monkeypatch, verification, expected_code):
    from types import ModuleType
    emitted = []
    monkeypatch.setattr(worker_module, "emit", emitted.append)
    monkeypatch.setattr(worker_module, "read_message", AsyncMock(return_value={
        "type": "start", "task": "edit", "model": "offline", "base_url": "http://localhost",
        "tools": ["client_file_edit", "client_file_read", "client_git_status"],
    }))
    monkeypatch.setattr(worker_module, "remote_tool", AsyncMock(return_value="ok"))

    class Agent:
        def __init__(self, config):
            self.tools = {tool.__name__: tool for tool in config.tools}
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def chat(self, task):
            self.calls += 1
            if self.calls == 1:
                await self.tools["client_file_edit"]("edited.py", "create", content="ok")
                if verification == "git":
                    await self.tools["client_git_status"]()
                else:
                    await self.tools["client_file_read"]("./edited.py" if verification == "same-file" else "unrelated.py")
            return SimpleNamespace(text=AsyncMock(return_value="done"), usage_metadata=None)

    sdk = ModuleType("google.antigravity")
    sdk.Agent = Agent
    sdk.BuiltinTools = SimpleNamespace(FINISH="finish")
    sdk.CapabilitiesConfig = SimpleNamespace
    sdk.LocalOpenAIAgentConfig = SimpleNamespace
    monkeypatch.setitem(sys.modules, "google.antigravity", sdk)
    assert await worker_module.main() == expected_code
    assert emitted[-1]["type"] == ("final" if expected_code == 0 else "error")


async def test_concurrent_remote_runs_cannot_edit_same_node(monkeypatch):
    conn = FakeConnection(writes=True)
    monkeypatch.setattr(service, "CONNECTED_CLIENTS", {"client-1": conn, "alias": conn})
    entered = asyncio.Event()

    async def worker(*args, **kwargs):
        if entered.is_set():
            raise AssertionError("duplicate worker started")
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(service, "_run_antigravity_worker", worker)
    first = asyncio.create_task(service.run_remote_coding_agent(client_id="client-1", task="edit", run_id="same-run"))
    try:
        await entered.wait()
        with pytest.raises(RuntimeError, match="already running"):
            await service.run_remote_coding_agent(client_id="alias", task="edit", run_id="same-run")
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
    assert conn.remote_coding_run_id is None
    monkeypatch.setattr(service, "_run_antigravity_worker", AsyncMock(return_value={"response": "resumed"}))
    result = await service.run_remote_coding_agent(client_id="client-1", task="resume", run_id="same-run")
    assert result["response"] == "resumed"


async def test_heartbeat_cleanup_does_not_remove_a_replacement(monkeypatch):
    from datetime import datetime, timedelta
    old_a = mcp_node.ClientConnection("a", "owner", SimpleNamespace(close=AsyncMock()), mcp_node.UserTier.PRO)
    old_b = mcp_node.ClientConnection("b", "owner", SimpleNamespace(close=AsyncMock()), mcp_node.UserTier.PRO)
    new_b = mcp_node.ClientConnection("b", "owner", SimpleNamespace(close=AsyncMock()), mcp_node.UserTier.PRO)
    for conn in (old_a, old_b):
        conn.last_seen = datetime.now() - timedelta(seconds=200)
    registry = {"a": old_a, "b": old_b, "b-alias": old_b}

    async def replace_b(**kwargs):
        registry["b"] = new_b

    old_a.websocket.close.side_effect = replace_b
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", registry)
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await mcp_node._heartbeat_monitor()
    assert registry == {"b": new_b}
    assert not new_b.closed
    old_b.websocket.close.assert_awaited_once()


async def test_unimplemented_file_chat_does_not_claim_tool_access(monkeypatch, offline_node_chat):
    from app.routes import client_chat
    conn = FakeConnection()
    monkeypatch.setattr(mcp_node, "CONNECTED_CLIENTS", {conn.client_id: conn})
    monkeypatch.setattr(mcp_node, "decode_jwt_token", lambda token: {"sub": conn.user_id})
    with pytest.raises(HTTPException) as error:
        await mcp_node.chat_with_client_files(conn.client_id, "inspect files", authorization="Bearer valid")
    assert error.value.status_code == 501
    client_chat.call_ollama.assert_not_awaited()
    client_chat.call_openrouter.assert_not_awaited()
