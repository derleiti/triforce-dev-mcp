from __future__ import annotations

from pathlib import Path

import json
from types import SimpleNamespace

import pytest

from app.mcp.tool_registry_unified import get_canonical_all_tools
from app.routes.mcp import handle_tools_list
from app.services import mcp_workspace_bridge as bridge
from app.mcp import workspace_tool_contract


class FakeRequest:
    def __init__(self, method: str, full: bool, session_id: str):
        self.headers = {}
        self.client = SimpleNamespace(host="203.0.113.10")
        self.state = SimpleNamespace(
            mcp_auth_method=method,
            mcp_auth_user=method,
            mcp_auth_full_access=full,
            mcp_session_id=session_id,
        )


def _semantic(tool: dict) -> str:
    return json.dumps(
        {"name": tool["name"], "inputSchema": tool.get("inputSchema") or {}},
        sort_keys=True,
        separators=(",", ":"),
    )


def _full_share_binding() -> dict:
    capabilities = [
        tool["name"] for tool in bridge.canonical_workspace_tools()
        if tool["name"] not in bridge.CONTROL_TOOLS
    ]
    grants = [
        {"resource": "workspace", "action": "read"},
        {"resource": "workspace", "action": "write"},
        {"resource": "display", "action": "observe"},
        {"resource": "display", "action": "control"},
        {"resource": "device", "action": "read"},
        {"resource": "device", "action": "control"},
        {"resource": "clipboard", "action": "read"},
        {"resource": "clipboard", "action": "write"},
        {"resource": "compute", "action": "execute"},
    ]
    connection = SimpleNamespace(share_manifest={"grants": grants})
    return {"mode": "write", "capabilities": capabilities, "connection": connection}


@pytest.mark.asyncio
async def test_workspace_control_contract_is_versioned_and_complete():
    result = await handle_tools_list({}, request=FakeRequest("public_guest", False, "schema-public"))
    tools = {tool["name"]: tool for tool in result["tools"]}

    assert "aihelper_pair" in tools
    pair_props = tools["aihelper_pair"]["inputSchema"]["properties"]
    assert {"action", "code", "workspace_context", "wait_seconds"} <= set(pair_props)
    assert {"status", "pair", "reconnect", "disconnect"} <= set(pair_props["action"]["enum"])

    contract_fp = bridge.workspace_contract_fingerprint()
    assert len(contract_fp) == 64
    tool = tools["aihelper_pair"]
    assert tool["x_schema_version"] == bridge.WORKSPACE_SCHEMA_VERSION
    assert tool["x_schema_fingerprint"] == bridge.workspace_tool_schema_fingerprint(tool)
    assert tool["x_workspace_contract_fingerprint"] == contract_fp


@pytest.mark.asyncio
async def test_public_and_internal_workspace_contracts_have_identical_semantic_schemas(monkeypatch):
    monkeypatch.setattr(bridge, "get_workspace_lease", lambda session_id: _full_share_binding())
    public = await handle_tools_list({}, request=FakeRequest("public_guest", False, "schema-public"))
    internal = await handle_tools_list({}, request=FakeRequest("bearer", True, "schema-internal"))
    public_tools = {tool["name"]: tool for tool in public["tools"]}
    internal_tools = {tool["name"]: tool for tool in internal["tools"]}

    for projected in bridge.canonical_workspace_tools():
        name = projected["name"]
        assert name in public_tools
        assert name in internal_tools
        assert _semantic(public_tools[name]) == _semantic(internal_tools[name])
        assert public_tools[name]["x_schema_fingerprint"] == internal_tools[name]["x_schema_fingerprint"]


@pytest.mark.asyncio
async def test_shared_tool_names_use_canonical_registry_schema_hash(monkeypatch):
    monkeypatch.setattr(bridge, "get_workspace_lease", lambda session_id: _full_share_binding())
    result = await handle_tools_list({}, request=FakeRequest("public_guest", False, "schema-local"))
    local = {tool["name"]: tool for tool in result["tools"]}
    canonical = {tool["name"]: tool for tool in get_canonical_all_tools()}
    shared = {tool["name"] for tool in bridge.canonical_workspace_tools()} & set(canonical)

    assert {"code_edit", "code_search", "code_tree", "file_ops"} <= shared
    for name in shared:
        assert _semantic(local[name]) == _semantic(canonical[name])


@pytest.mark.asyncio
async def test_each_tools_list_call_rebuilds_workspace_contract(monkeypatch):
    first = await handle_tools_list({}, request=FakeRequest("public_guest", False, "schema-one"))
    first_tool = {tool["name"]: tool for tool in first["tools"]}["aihelper_pair"]

    original = workspace_tool_contract.WORKSPACE_CONTROL_TOOLS[0]["inputSchema"]["properties"]
    changed = dict(original)
    changed["contract_refresh_probe"] = {"type": "string"}
    monkeypatch.setitem(workspace_tool_contract.WORKSPACE_CONTROL_TOOLS[0]["inputSchema"], "properties", changed)

    second = await handle_tools_list({}, request=FakeRequest("public_guest", False, "schema-two"))
    second_tool = {tool["name"]: tool for tool in second["tools"]}["aihelper_pair"]

    assert "contract_refresh_probe" not in first_tool["inputSchema"]["properties"]
    assert "contract_refresh_probe" in second_tool["inputSchema"]["properties"]
    assert first_tool["x_schema_fingerprint"] != second_tool["x_schema_fingerprint"]
    assert first_tool["x_workspace_contract_fingerprint"] != second_tool["x_workspace_contract_fingerprint"]


def test_workspace_node_supports_secret_free_native_handshake_and_hashed_identity():
    source = (Path(__file__).resolve().parents[1] / "app/routes/mcp_node.py").read_text(encoding="utf-8")
    assert 'getattr(websocket, "headers", {})' in source
    assert 'request_headers.get("x-ailinux-pair-code")' in source
    assert 'request_headers.get("x-ailinux-handoff-code")' in source
    assert 'request_headers.get("x-ailinux-machine-id")' in source
    assert 'hashlib.sha256(workspace_credential_id.encode("utf-8"))' in source
    assert "workspace_credential_id.replace('-', '')[:12]" not in source
