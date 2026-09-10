from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import mcp_workspace_sessions as sessions
from app.services.mcp_workspace_bridge import call_public_local_tool


class DummyConnection:
    def __init__(self, client_id: str = 'local-1'):
        self.client_id = client_id
        self.closed = False
        self.supported_tools = ['client_workspace_tool']
        self.calls = []

    async def send_tool_call(self, name, args, timeout=0):
        self.calls.append((name, args, timeout))
        return {'content': [{'type': 'text', 'text': 'ok-local'}], 'isError': False}


class DummyRequest:
    def __init__(self, session_id: str):
        self.state = SimpleNamespace(mcp_auth_method='public_guest', mcp_session_id=session_id)


@pytest.fixture(autouse=True)
def clear_state():
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()
    yield
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()


def test_pairing_code_is_session_scoped_and_consumed_on_bind():
    code = sessions.get_or_create_pair_code('session-A')
    assert sessions.resolve_pair_code(code) == 'session-A'
    conn = DummyConnection()
    sessions.bind_workspace('session-A', conn, mode='write', task='fix tests')
    assert sessions.resolve_pair_code(code) is None
    bound = sessions.get_workspace('session-A')
    assert bound['client_id'] == 'local-1'
    assert bound['mode'] == 'write'
    assert sessions.get_workspace('session-B') is None


@pytest.mark.asyncio
async def test_workspace_status_returns_pair_code_before_local_node_connects():
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'workspace_status', {})
    assert result['structuredContent']['code'] == 'WORKSPACE_REQUIRED'
    assert result['structuredContent']['pair_code']


@pytest.mark.asyncio
async def test_read_only_binding_blocks_local_write_but_allows_read():
    conn = DummyConnection()
    sessions.bind_workspace('session-A', conn, mode='read_only', task='inspect')
    req = DummyRequest('session-A')
    blocked = await call_public_local_tool(req, 'shell', {'command': 'echo no'})
    assert blocked['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    read = await call_public_local_tool(req, 'file_read', {'path': 'README.md'})
    assert read['isError'] is False
    assert conn.calls[0][0] == 'client_workspace_tool'
    assert conn.calls[0][1]['tool'] == 'file_read'
