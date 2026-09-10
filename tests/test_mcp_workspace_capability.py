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
    sessions._WEB_PAIR.clear()
    yield
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()


def test_pairing_code_is_session_scoped_and_consumed_on_bind():
    code = sessions.get_or_create_pair_code('session-A')
    assert sessions.resolve_pair_code(code) == 'session-A'
    conn = DummyConnection()
    sessions.bind_workspace('session-A', conn, mode='write', task='fix tests', capabilities=['file_read', 'file_edit'])
    assert sessions.resolve_pair_code(code) is None
    bound = sessions.get_workspace('session-A')
    assert bound['client_id'] == 'local-1'
    assert bound['mode'] == 'write'
    assert sessions.get_workspace('session-B') is None


def test_web_pair_codes_are_unique_and_claim_waiting_helper_once():
    first = sessions.create_web_pair_code()
    second = sessions.create_web_pair_code()
    assert first != second
    conn = DummyConnection()
    waiting = sessions.register_waiting_workspace(first, conn, mode='write', task='web flow', capabilities=['file_read', 'file_edit'])
    assert waiting['waiting_for_session'] is True
    bound = sessions.claim_waiting_workspace(first, 'session-A')
    assert bound['mode'] == 'write'
    assert bound['task'] == 'web flow'
    assert bound['capabilities'] == ['file_edit', 'file_read']
    assert sessions.get_workspace('session-A')['client_id'] == conn.client_id
    with pytest.raises(ValueError):
        sessions.claim_waiting_workspace(first, 'session-B')


@pytest.mark.asyncio
async def test_workspace_status_tells_user_to_use_web_setup_page():
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'workspace_status', {})
    assert result['structuredContent']['code'] == 'WORKSPACE_REQUIRED'
    assert result['structuredContent']['setup_url'] == 'https://api.ailinux.me/v1/mcp'
    assert 'pair_code' not in result['structuredContent']


@pytest.mark.asyncio
async def test_workspace_pair_claims_waiting_web_helper_for_current_session():
    code = sessions.create_web_pair_code()
    conn = DummyConnection()
    sessions.register_waiting_workspace(code, conn, mode='read_only', task='inspect web', capabilities=['workspace_info', 'file_read'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'workspace_pair', {'code': code})
    assert result['structuredContent']['ok'] is True
    assert result['structuredContent']['mode'] == 'read_only'
    assert result['structuredContent']['capabilities'] == ['file_read', 'workspace_info']
    assert sessions.get_workspace('session-A')['client_id'] == conn.client_id


@pytest.mark.asyncio
async def test_read_only_binding_blocks_write_and_allows_advertised_read():
    conn = DummyConnection()
    sessions.bind_workspace('session-A', conn, mode='read_only', task='inspect', capabilities=['file_read'])
    req = DummyRequest('session-A')
    blocked = await call_public_local_tool(req, 'file_edit', {'path': 'x.txt', 'operation': 'write', 'content': 'x'})
    assert blocked['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    read = await call_public_local_tool(req, 'file_read', {'path': 'README.md'})
    assert read['isError'] is False
    assert conn.calls[0][0] == 'client_workspace_tool'
    assert conn.calls[0][1]['tool'] == 'file_read'


@pytest.mark.asyncio
async def test_browser_capability_gate_rejects_unadvertised_tool():
    conn = DummyConnection()
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['file_read'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'file_edit', {'path': 'x.txt', 'operation': 'write', 'content': 'x'})
    assert result['structuredContent']['code'] == 'WORKSPACE_TOOL_UNAVAILABLE'
    assert conn.calls == []


def test_same_pairing_id_reconnects_same_session_after_suspend():
    code = sessions.create_web_pair_code()
    first = DummyConnection('mobile-1')
    sessions.register_waiting_workspace(code, first, mode='write', task='mobile', capabilities=['file_read', 'file_edit'])
    sessions.claim_waiting_workspace(code, 'session-A')
    sessions.suspend_connection(first)
    status = sessions.workspace_status('session-A')
    assert status['connected'] is False
    assert status['suspended'] is True
    assert sessions.pair_code_kind(code) == ('reconnect', 'session-A')
    second = DummyConnection('mobile-2')
    resumed = sessions.reconnect_web_workspace(code, second, mode='write', task='mobile', capabilities=['file_read', 'file_edit'])
    assert resumed['session_id'] == 'session-A'
    bound = sessions.get_workspace('session-A')
    assert bound is not None
    assert bound['client_id'] == 'mobile-2'


def test_same_pairing_id_cannot_be_claimed_by_another_session():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-1')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    sessions.claim_waiting_workspace(code, 'session-A')
    with pytest.raises(ValueError):
        sessions.claim_waiting_workspace(code, 'session-B')


def test_transport_session_cleanup_does_not_revoke_workspace():
    from app.routes import mcp as mcp_route
    conn = DummyConnection('browser-lifecycle')
    mcp_route._mcp_sessions['session-A'] = {
        'created': mcp_route.dt_datetime.now(),
        'queue': None,
        'initialized': True,
    }
    sessions.bind_workspace('session-A', conn, mode='read_only', capabilities=['file_read'])
    mcp_route._clear_mcp_session('session-A', clear_workspace=False)
    assert 'session-A' not in mcp_route._mcp_sessions
    assert sessions.get_workspace('session-A') is not None


def test_explicit_workspace_cleanup_still_revokes_binding():
    from app.routes import mcp as mcp_route
    conn = DummyConnection('browser-lifecycle')
    sessions.bind_workspace('session-A', conn, mode='read_only', capabilities=['file_read'])
    mcp_route._clear_mcp_session('session-A', clear_workspace=True)
    assert sessions.get_workspace('session-A') is None
