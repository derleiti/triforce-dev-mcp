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
    def __init__(self, session_id: str, auth_method: str = 'public_guest'):
        self.state = SimpleNamespace(mcp_auth_method=auth_method, mcp_session_id=session_id)


@pytest.fixture(autouse=True)
def clear_state(monkeypatch):
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()
    sessions._RESUME_TICKETS.clear()
    monkeypatch.setattr(sessions, '_redis_client', lambda: None)
    yield
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()
    sessions._RESUME_TICKETS.clear()


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


def test_web_pair_codes_are_unique_and_authorize_multiple_aliases():
    first = sessions.create_web_pair_code()
    second = sessions.create_web_pair_code()
    assert first != second
    conn = DummyConnection()
    waiting = sessions.register_waiting_workspace(first, conn, mode='write', task='web flow', capabilities=['file_read', 'file_edit'])
    assert waiting['waiting_for_session'] is True
    bound_a = sessions.claim_waiting_workspace(first, 'session-A')
    bound_b = sessions.claim_waiting_workspace(first, 'session-B')
    assert bound_a['mode'] == 'write'
    assert bound_a['task'] == 'web flow'
    assert bound_a['capabilities'] == ['file_edit', 'file_read']
    assert bound_a['lease_id'] == bound_b['lease_id']
    assert sessions.get_workspace('session-A')['client_id'] == conn.client_id
    assert sessions.get_workspace('session-B')['client_id'] == conn.client_id


@pytest.mark.asyncio
async def test_workspace_status_tells_user_to_use_web_setup_page():
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'workspace_status', {})
    assert result['structuredContent']['code'] == 'WORKSPACE_REQUIRED'
    assert result['structuredContent']['setup_url'].startswith('https://api.ailinux.me/v1/mcp?pair_code=')
    assert result['structuredContent']['pair_code']


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
    assert status['connected'] is True
    assert status['suspended'] is False
    assert sessions.pair_code_kind(code) == ('reconnect', 'session-A')
    second = DummyConnection('mobile-2')
    resumed = sessions.reconnect_web_workspace(code, second, mode='write', task='mobile', capabilities=['file_read', 'file_edit'])
    assert resumed['session_id'] == 'session-A'
    bound = sessions.get_workspace('session-A')
    assert bound is not None
    assert bound['client_id'] == 'mobile-2'


def test_same_pairing_id_can_be_shared_by_authorized_sessions():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-1')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'session-A')
    second = sessions.claim_waiting_workspace(code, 'session-B')
    assert first['lease_id'] == second['lease_id']
    assert sessions.get_workspace('session-A') is not None
    assert sessions.get_workspace('session-B') is not None


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


def test_detached_transport_can_rebind_same_lease_to_new_mcp_session():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('lease-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read', 'file_edit'])
    first = sessions.claim_waiting_workspace(code, 'transport-A')
    lease_id = first['lease_id']
    sessions.mark_transport_detached('transport-A')

    rebound = sessions.claim_waiting_workspace(code, 'transport-B')
    assert rebound['lease_id'] == lease_id
    assert sessions.get_workspace('transport-A') is not None
    assert sessions.get_workspace('transport-B')['client_id'] == 'lease-browser'
    assert sessions.workspace_status('transport-B')['transport_active'] is True


def test_same_pair_code_authorizes_multiple_concurrent_mcp_aliases():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('lease-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'transport-A')
    second = sessions.claim_waiting_workspace(code, 'transport-B')
    assert first['lease_id'] == second['lease_id']
    assert sessions.get_workspace('transport-A')['client_id'] == 'lease-browser'
    assert sessions.get_workspace('transport-B')['client_id'] == 'lease-browser'


def test_browser_reconnect_refreshes_all_mcp_aliases():
    code = sessions.create_web_pair_code()
    first = DummyConnection('browser-1')
    sessions.register_waiting_workspace(code, first, mode='write', capabilities=['file_read'])
    sessions.claim_waiting_workspace(code, 'chatgpt')
    sessions.claim_waiting_workspace(code, 'mistral')
    sessions.suspend_connection(first)
    second = DummyConnection('browser-2')
    sessions.reconnect_web_workspace(code, second, mode='write', capabilities=['file_read'])
    assert sessions.get_workspace('chatgpt')['client_id'] == 'browser-2'
    assert sessions.get_workspace('mistral')['client_id'] == 'browser-2'


@pytest.mark.asyncio
async def test_authenticated_session_can_pair_and_use_browser_workspace():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('authenticated-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    req = DummyRequest('admin-session', auth_method='bearer')
    paired = await call_public_local_tool(req, 'workspace_pair', {'code': code})
    assert paired['structuredContent']['ok'] is True
    read = await call_public_local_tool(req, 'file_read', {'path': 'README.md'})
    assert read['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'file_read'


@pytest.mark.asyncio
async def test_sessionless_pair_uses_pair_code_as_transport_independent_token():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('stateless-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read', 'file_edit'])
    req = DummyRequest('')

    paired = await call_public_local_tool(req, 'workspace_pair', {'code': code})
    assert paired['structuredContent']['ok'] is True
    assert paired['structuredContent']['workspace_token'] != code
    assert len(paired['structuredContent']['workspace_token']) >= 40

    read = await call_public_local_tool(req, 'file_read', {'path': 'README.md', 'workspace_token': paired['structuredContent']['workspace_token']})
    assert read['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'file_read'
    assert 'workspace_token' not in conn.calls[-1][1]['arguments']


@pytest.mark.asyncio
async def test_sessionless_workspace_token_cannot_resolve_unpaired_or_wrong_code():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('stateless-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    req = DummyRequest('')

    before_pair = await call_public_local_tool(req, 'file_read', {'path': 'README.md', 'workspace_token': code})
    assert before_pair['structuredContent']['code'] == 'MCP_SESSION_REQUIRED'

    await call_public_local_tool(req, 'workspace_pair', {'code': code})
    wrong = await call_public_local_tool(req, 'file_read', {'path': 'README.md', 'workspace_token': 'AAAA-BBBB-CCCC-DDDD-EEEE-FFFF'})
    assert wrong['structuredContent']['code'] == 'MCP_SESSION_REQUIRED'


@pytest.mark.asyncio
async def test_real_mcp_session_can_reclaim_stateless_lease_with_same_pair_code():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('stateless-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    sessionless = DummyRequest('')
    paired = await call_public_local_tool(sessionless, 'workspace_pair', {'code': code})
    lease_id = paired['structuredContent']['lease_id']

    sessioned = DummyRequest('real-session')
    rebound = await call_public_local_tool(sessioned, 'workspace_pair', {'code': code})
    assert rebound['structuredContent']['ok'] is True
    assert rebound['structuredContent']['lease_id'] == lease_id
    assert sessions.get_workspace('real-session')['client_id'] == 'stateless-browser'


def test_openai_connector_headers_form_stable_logical_session_without_exposing_raw_values():
    from app.routes.mcp import _logical_transport_session_id
    req = SimpleNamespace(headers={
        'user-agent': 'openai-mcp/1.0.0',
        'x-openai-session': 'session-secret-value',
        'x-openai-subject': 'subject-secret-value',
    })
    first = _logical_transport_session_id(req)
    second = _logical_transport_session_id(req)
    assert first == second
    assert first.startswith('openai-')
    assert 'session-secret-value' not in first
    assert 'subject-secret-value' not in first


def test_explicit_mcp_session_header_wins_over_connector_fallback():
    from app.routes.mcp import _logical_transport_session_id
    req = SimpleNamespace(headers={
        'user-agent': 'openai-mcp/1.0.0',
        'x-openai-session': 's',
        'x-openai-subject': 'u',
    })
    assert _logical_transport_session_id(req, 'protocol-session') == 'protocol-session'


def test_session_pair_promotes_to_shared_web_lease():
    code = sessions.get_or_create_pair_code('chatgpt-session')
    conn = DummyConnection('browser-direct')
    bound = sessions.promote_session_pair_to_web_lease(
        code, 'chatgpt-session', conn, mode='write', capabilities=['file_read', 'file_edit']
    )
    assert bound['session_id'] == 'chatgpt-session'
    assert sessions.resolve_pair_code(code) is None
    assert sessions.pair_code_kind(code) == ('reconnect', 'chatgpt-session')
    second = sessions.claim_waiting_workspace(code, 'mistral-session')
    assert second['lease_id'] == bound['lease_id']
    assert sessions.get_workspace('chatgpt-session')['client_id'] == 'browser-direct'
    assert sessions.get_workspace('mistral-session')['client_id'] == 'browser-direct'


@pytest.mark.asyncio
async def test_workspace_status_can_pair_from_user_supplied_workspace_id():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('auto-pair-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read', 'file_edit'])
    req = DummyRequest('chatgpt-auto')
    result = await call_public_local_tool(req, 'workspace_status', {'workspace_id': code})
    assert result['structuredContent']['ok'] is True
    assert result['structuredContent']['connected'] is True
    assert result['structuredContent']['mode'] == 'write'
    assert sessions.get_workspace('chatgpt-auto')['client_id'] == 'auto-pair-browser'


@pytest.mark.asyncio
async def test_workspace_status_rejects_invalid_workspace_id():
    req = DummyRequest('chatgpt-auto')
    result = await call_public_local_tool(req, 'workspace_status', {'workspace_id': 'AAAA-BBBB-CCCC-DDDD-EEEE-FFFF'})
    assert result['structuredContent']['code'] == 'WORKSPACE_PAIR_FAILED'


@pytest.mark.asyncio
async def test_openai_workspace_affinity_is_stable_but_isolates_transport_contexts():
    from app.services.mcp_workspace_bridge import workspace_affinity_id

    class OpenAIRequest:
        def __init__(self, session_id):
            self.state = SimpleNamespace(mcp_auth_method='public_guest', mcp_session_id=session_id)
            self.headers = {
                'user-agent': 'openai-mcp/1.0.0',
                'x-openai-subject': 'same-subject',
                'x-openai-session': session_id,
            }

    req_a1 = OpenAIRequest('transport-A')
    req_a2 = OpenAIRequest('transport-A')
    req_b = OpenAIRequest('transport-B')
    assert workspace_affinity_id(req_a1) == workspace_affinity_id(req_a2)
    assert workspace_affinity_id(req_a1) != workspace_affinity_id(req_b)

    code = sessions.create_web_pair_code()
    conn = DummyConnection('shared-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['code_tree'])
    paired = await call_public_local_tool(req_a1, 'code_search', {'query': code})
    assert paired['structuredContent']['connected'] is True

    same_transport = await call_public_local_tool(req_a2, 'code_tree', {'path': '.', 'depth': 1, 'max_entries': 10})
    assert same_transport['isError'] is False
    isolated = await call_public_local_tool(req_b, 'code_tree', {'path': '.', 'depth': 1, 'max_entries': 10})
    assert isolated['structuredContent']['code'] == 'WORKSPACE_REQUIRED'


def test_workspace_status_distinguishes_suspended_from_unpaired():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-status')
    sessions.register_waiting_workspace(code, conn, mode='readwrite', capabilities=['file_read', 'file_edit'])
    sessions.claim_waiting_workspace(code, 'session-A')
    sessions.suspend_connection(conn)

    status = sessions.workspace_status('session-A')
    assert status['state'] == 'ready'
    assert status['connected'] is True
    assert status['suspended'] is False
    assert status['reconnectable'] is True
    assert status['access_mode'] == 'write'
    assert status['mode'] == 'write'


def test_suspended_workspace_can_authorize_new_transport_alias_with_same_code():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-alias')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['code_tree'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    sessions.suspend_connection(conn)

    second = sessions.claim_waiting_workspace(code, 'mistral-B')
    assert second['lease_id'] == first['lease_id']
    assert sessions.workspace_status('mistral-B')['state'] == 'ready'
    assert sessions.workspace_status('mistral-B')['transport_state'] == 'offline'

    replacement = DummyConnection('mobile-alias-reconnected')
    sessions.reconnect_web_workspace(code, replacement, mode='write', capabilities=['code_tree'])
    assert sessions.workspace_status('chatgpt-A')['state'] == 'ready'
    assert sessions.workspace_status('chatgpt-A')['transport_state'] == 'online'
    assert sessions.workspace_status('mistral-B')['state'] == 'ready'
    assert sessions.workspace_status('mistral-B')['transport_state'] == 'online'
    assert sessions.get_workspace('chatgpt-A')['client_id'] == 'mobile-alias-reconnected'
    assert sessions.get_workspace('mistral-B')['client_id'] == 'mobile-alias-reconnected'


@pytest.mark.asyncio
async def test_bridge_reports_workspace_suspended_instead_of_required():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-bridge')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['code_tree'])
    sessions.claim_waiting_workspace(code, 'session-A')
    sessions.suspend_connection(conn)

    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'code_tree', {'path': '.', 'depth': 1})
    assert result['structuredContent']['code'] == 'WORKSPACE_EXECUTOR_UNAVAILABLE'
    assert result['structuredContent']['state'] == 'ready'
    assert result['structuredContent']['transport_state'] == 'offline'
    assert result['structuredContent']['reconnectable'] is True
    assert result['structuredContent']['access_mode'] == 'write'


def test_first_claim_succeeds_after_waiting_browser_suspends():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-race')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['code_tree', 'file_read'])
    sessions.suspend_connection(conn)

    bound = sessions.claim_waiting_workspace(code, 'chatgpt-after-switch')
    status = sessions.workspace_status('chatgpt-after-switch')

    assert bound['connection'] is None
    assert status['state'] == 'ready'
    assert status['connected'] is True
    assert status['reconnectable'] is True
    assert status['access_mode'] == 'write'
    assert status['lease_id']

    replacement = DummyConnection('mobile-race-reconnected')
    rebound = sessions.reconnect_web_workspace(
        code, replacement, mode='write', capabilities=['code_tree', 'file_read']
    )
    assert rebound['lease_id'] == status['lease_id']
    assert sessions.workspace_status('chatgpt-after-switch')['state'] == 'ready'
    assert sessions.workspace_status('chatgpt-after-switch')['transport_state'] == 'online'


def test_unseen_browser_ticket_cannot_be_claimed_while_offline():
    code = sessions.create_web_pair_code()
    with pytest.raises(RuntimeError, match='browser has not connected'):
        sessions.claim_waiting_workspace(code, 'chatgpt-too-early')


@pytest.mark.asyncio
async def test_valid_new_workspace_id_rebinds_from_old_suspended_lease():
    old_code = sessions.create_web_pair_code()
    old_conn = DummyConnection('old-browser')
    sessions.register_waiting_workspace(old_code, old_conn, mode='write', capabilities=['code_search'])
    sessions.claim_waiting_workspace(old_code, 'session-A')
    sessions.suspend_connection(old_conn)

    new_code = sessions.create_web_pair_code()
    new_conn = DummyConnection('new-browser')
    sessions.register_waiting_workspace(new_code, new_conn, mode='read_only', capabilities=['code_search'])

    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'code_search', {'query': new_code})
    assert result['structuredContent']['connected'] is True
    assert result['structuredContent']['access_mode'] == 'read_only'
    assert sessions.get_workspace('session-A')['client_id'] == 'new-browser'

    old_pair = sessions.resolve_web_pair_code(old_code)
    assert old_pair is not None
    assert 'session-A' not in old_pair['paired_session_ids']
    assert old_pair['paired_session_id'] != 'session-A'


@pytest.mark.asyncio
async def test_invalid_code_shaped_search_remains_search_on_live_workspace():
    conn = DummyConnection('live-browser')
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['code_search'])
    req = DummyRequest('session-A')
    query = 'AAAA-BBBB-CCCC-DDDD-EEEE-FFFF'

    result = await call_public_local_tool(req, 'code_search', {'query': query})
    assert result['isError'] is False
    assert conn.calls[-1][1]['arguments']['query'] == query


class FakeRedis:
    def __init__(self):
        self.data = {}
    def ping(self):
        return True
    def setex(self, key, ttl, value):
        self.data[key] = value
    def get(self, key):
        return self.data.get(key)
    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)


def test_resume_token_restores_lease_after_process_state_loss(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('persist-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-old')
    token = first['resume_token']
    lease_id = first['lease_id']

    # Simulate a TriForce process restart: only Redis survives.
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()

    rebound = sessions.claim_workspace_with_resume_token(token, 'chatgpt-new')
    assert rebound['lease_id'] == lease_id
    assert sessions.workspace_status('chatgpt-new')['state'] == 'ready'
    assert sessions.workspace_status('chatgpt-new')['transport_state'] == 'offline'
    replacement = DummyConnection('persist-browser-2')
    resumed = sessions.reconnect_workspace_with_resume_token(
        token, replacement, mode='write', capabilities=['file_read']
    )
    assert resumed['lease_id'] == lease_id
    assert sessions.workspace_status('chatgpt-new')['state'] == 'ready'
    assert sessions.workspace_status('chatgpt-new')['transport_state'] == 'online'


def test_pair_code_stays_short_lived_after_durable_lease_created(monkeypatch):
    code = sessions.create_web_pair_code()
    conn = DummyConnection('pair-expiry-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    bound = sessions.claim_waiting_workspace(code, 'session-A')
    token = bound['resume_token']
    item = sessions._WEB_PAIR[sessions._pair_key(code)]
    item['pair_expires_at'] = sessions.time.time() - 1
    assert sessions.resolve_web_pair_code(code) is None
    with pytest.raises(ValueError, match='expired workspace pairing code'):
        sessions.claim_waiting_workspace(code, 'session-B')
    assert sessions.resolve_resume_token(token) is not None


@pytest.mark.asyncio
async def test_workspace_token_rebinds_new_mcp_transport(monkeypatch):
    code = sessions.create_web_pair_code()
    conn = DummyConnection('handoff-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first_req = DummyRequest('transport-old')
    paired = await call_public_local_tool(first_req, 'workspace_pair', {'code': code})
    token = paired['structuredContent']['workspace_token']

    sessions._SESSION_WORKSPACE.pop('transport-old', None)
    new_req = DummyRequest('transport-new')
    read = await call_public_local_tool(new_req, 'file_read', {'path': 'README.md', 'workspace_token': token})
    assert read['isError'] is False
    assert sessions.get_workspace('transport-new')['lease_id'] == paired['structuredContent']['lease_id']


def test_resume_ticket_is_one_shot_and_does_not_expose_resume_token():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('ticket-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    bound = sessions.claim_waiting_workspace(code, 'session-A')
    token = bound['resume_token']
    ticket = sessions.create_workspace_resume_ticket(token)
    assert ticket != token
    assert token not in ticket
    consumed = sessions.consume_workspace_resume_ticket(ticket)
    assert consumed == token
    assert sessions.consume_workspace_resume_ticket(ticket) == ''


def test_persisted_mcp_alias_restores_lease_without_workspace_token(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('alias-persist-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'stable-mcp-affinity')
    lease_id = first['lease_id']

    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()

    restored = sessions.workspace_status('stable-mcp-affinity')
    assert restored['state'] == 'ready'
    assert restored['connected'] is True
    assert restored['transport_state'] == 'offline'
    assert restored['lease_id'] == lease_id
    assert restored['access_mode'] == 'write'


@pytest.mark.asyncio
async def test_public_shell_is_forced_to_local_workspace_and_never_server():
    conn = DummyConnection('local-shell')
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['shell'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'shell', {'command': 'pwd'})
    assert result['isError'] is False
    assert conn.calls[-1][0] == 'client_workspace_tool'
    assert conn.calls[-1][1]['tool'] == 'shell'


@pytest.mark.asyncio
async def test_mixed_local_tools_respect_read_only_mode():
    conn = DummyConnection('mixed-local')
    sessions.bind_workspace(
        'session-A', conn, mode='read_only',
        capabilities=['file_ops', 'git', 'code_edit'],
    )
    req = DummyRequest('session-A')

    read = await call_public_local_tool(req, 'file_ops', {'action': 'read', 'path': 'README.md'})
    assert read['isError'] is False
    status = await call_public_local_tool(req, 'git', {'mode': 'status'})
    assert status['isError'] is False

    write = await call_public_local_tool(req, 'file_ops', {'action': 'write', 'path': 'x.txt', 'content': 'x'})
    assert write['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    edit = await call_public_local_tool(req, 'code_edit', {'path': 'x.py', 'mode': 'append', 'new_text': 'x'})
    assert edit['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    commit = await call_public_local_tool(req, 'git', {'mode': 'commit', 'message': 'x'})
    assert commit['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'


def test_workspace_join_id_persists_for_lease_lifetime(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    assert first['lease_id']
    resolved = sessions.resolve_web_pair_code(code)
    assert resolved is not None
    assert resolved['lease_id'] == first['lease_id']
    assert resolved['pair_expires_at'] > sessions.time.time() + sessions.PAIR_TTL_SECONDS


def test_workspace_join_id_can_add_second_client_alias(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    second = sessions.claim_waiting_workspace(code, 'telegram-B')
    assert second['lease_id'] == first['lease_id']
    assert sessions.workspace_status('telegram-B')['access_mode'] == 'write'


def test_workspace_join_id_restores_from_redis_after_process_loss(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    lease_id = first['lease_id']

    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()

    restored = sessions.resolve_web_pair_code(code)
    assert restored is not None
    assert restored['lease_id'] == lease_id
    second = sessions.claim_waiting_workspace(code, 'telegram-after-restart')
    assert second['lease_id'] == lease_id
    assert sessions.workspace_status('telegram-after-restart')['state'] == 'ready'


def test_active_mcp_session_is_not_expired_by_creation_age():
    from datetime import timedelta
    from app.routes import mcp as mcp_route
    now = mcp_route.dt_datetime.now()
    mcp_route._mcp_sessions['keepalive-session'] = {
        'created': now - timedelta(hours=3),
        'last_seen': now - timedelta(minutes=5),
        'queue': None,
        'initialized': True,
    }
    mcp_route._mcp_sessions['idle-session'] = {
        'created': now - timedelta(hours=3),
        'last_seen': now - timedelta(hours=2),
        'queue': None,
        'initialized': True,
    }
    mcp_route._cleanup_old_sessions()
    assert 'keepalive-session' in mcp_route._mcp_sessions
    assert 'idle-session' not in mcp_route._mcp_sessions
    mcp_route._mcp_sessions.pop('keepalive-session', None)


@pytest.mark.asyncio
async def test_offline_executor_waits_for_same_lease_reconnect(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge
    req = DummyRequest('session-A')
    conn2 = DummyConnection('online-two')
    suspended = {
        'lease_id': 'lease-reconnect',
        'mode': 'write',
        'capabilities': ['file_read'],
        'connection': None,
        'transport_state': 'offline',
    }
    resumed = {
        **suspended,
        'connection': conn2,
        'client_id': conn2.client_id,
        'transport_state': 'online',
        'executor_online': True,
    }

    monkeypatch.setattr(bridge, 'get_workspace_lease', lambda sid: suspended)

    async def fake_wait(request, binding, *, timeout=0):
        assert binding['lease_id'] == 'lease-reconnect'
        return resumed

    monkeypatch.setattr(bridge, 'wait_for_workspace_executor', fake_wait)
    result = await bridge.call_public_local_tool(req, 'file_read', {'path': 'README.md'})
    assert result['isError'] is False
    assert conn2.calls[-1][1]['tool'] == 'file_read'


@pytest.mark.asyncio
async def test_disconnect_during_local_call_is_not_retried(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge

    class DropConnection(DummyConnection):
        async def send_tool_call(self, name, args, timeout=0):
            self.calls.append((name, args, timeout))
            self.closed = True
            raise ConnectionError('dropped')

    conn = DropConnection('dropper')
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['file_edit'])
    req = DummyRequest('session-A')
    result = await bridge.call_public_local_tool(req, 'file_edit', {'path':'x.txt','operation':'write','content':'x'})
    assert result['structuredContent']['code'] == 'WORKSPACE_EXECUTION_UNCERTAIN'
    assert result['structuredContent']['retryable'] is False
    assert len(conn.calls) == 1
