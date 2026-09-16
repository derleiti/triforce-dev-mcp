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
    sessions._HANDOFF_TICKETS.clear()
    monkeypatch.setattr(sessions, '_redis_client', lambda: None)
    yield
    sessions._SESSION_PAIR.clear()
    sessions._PAIR_INDEX.clear()
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()
    sessions._RESUME_TICKETS.clear()
    sessions._HANDOFF_TICKETS.clear()


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


def test_web_pair_codes_are_unique_and_each_code_is_claimed_once():
    first = sessions.create_web_pair_code()
    second = sessions.create_web_pair_code()
    assert first != second
    conn = DummyConnection()
    waiting = sessions.register_waiting_workspace(first, conn, mode='write', task='web flow', capabilities=['file_read', 'file_edit'])
    assert waiting['waiting_for_session'] is True
    assert waiting['resume_token']
    bound = sessions.claim_waiting_workspace(first, 'session-A')
    assert bound['mode'] == 'write'
    assert bound['task'] == 'web flow'
    assert bound['capabilities'] == ['file_edit', 'file_read']
    assert sessions.get_workspace('session-A')['client_id'] == conn.client_id
    with pytest.raises(ValueError, match='already been used'):
        sessions.claim_waiting_workspace(first, 'session-B')
    assert sessions.get_workspace('session-B') is None


def test_browser_socket_ticket_is_one_shot_and_bound_to_join_code():
    code = sessions.create_web_pair_code()
    ticket = sessions.create_workspace_socket_ticket(code)
    assert ticket != code
    assert sessions.consume_workspace_socket_ticket(ticket) == code
    assert sessions.consume_workspace_socket_ticket(ticket) == ''


def test_waiting_helper_can_resume_before_ai_claim_and_code_remains_claimable(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    first = DummyConnection('waiting-1')
    waiting = sessions.register_waiting_workspace(code, first, mode='write', capabilities=['file_read'])
    token = waiting['resume_token']
    sessions.suspend_connection(first)
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()
    second = DummyConnection('waiting-2')
    resumed = sessions.reconnect_workspace_with_resume_token(token, second, mode='write', capabilities=['file_read'])
    assert resumed['waiting_for_session'] is True
    claimed = sessions.claim_waiting_workspace(code, 'chatgpt-after-drop')
    assert claimed['lease_id'] == resumed['lease_id']
    assert sessions.get_workspace('chatgpt-after-drop')['client_id'] == 'waiting-2'
    assert sessions.resolve_web_pair_code(code) is None



@pytest.mark.asyncio
async def test_workspace_status_tells_user_to_create_helper_share_id():
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'workspace_status', {})
    structured = result['structuredContent']
    assert structured['code'] == 'WORKSPACE_REQUIRED'
    assert structured['setup_url'] == 'https://api.ailinux.me/v1/mcp'
    assert structured['pair_direction'] == 'helper_to_ai'
    assert 'pair_code' not in structured


@pytest.mark.asyncio
async def test_workspace_status_setup_url_never_carries_credentials():
    req = DummyRequest('session-url-leak')
    result = await call_public_local_tool(req, 'workspace_status', {})
    structured = result['structuredContent']
    setup_url = str(structured['setup_url'])
    assert '?' not in setup_url and '#' not in setup_url
    assert 'pair_code' not in structured
    for key in ('pair_code', 'resume_token', 'workspace_token', 'handoff'):
        assert key not in setup_url


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
async def test_aihelper_pair_canonical_lifecycle_supports_pair_status_reconnect_disconnect():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('canary-helper')
    sessions.register_waiting_workspace(code, conn, mode='read_only', task='canary', capabilities=['computer_observe'])
    req = DummyRequest('session-A')

    paired = await call_public_local_tool(req, 'aihelper_pair', {'action': 'pair', 'code': code})
    assert paired['structuredContent']['ok'] is True
    assert sessions.get_workspace('session-A')['client_id'] == 'canary-helper'

    status = await call_public_local_tool(req, 'aihelper_pair', {'action': 'status'})
    assert status['structuredContent']['connected'] is True

    reconnected = await call_public_local_tool(req, 'aihelper_pair', {'action': 'reconnect', 'wait_seconds': 0})
    assert reconnected['structuredContent']['connected'] is True

    disconnected = await call_public_local_tool(req, 'aihelper_pair', {'action': 'disconnect'})
    assert disconnected['structuredContent']['ok'] is True
    assert disconnected['structuredContent']['revoked'] is True
    assert sessions.get_workspace('session-A') is None


@pytest.mark.asyncio
async def test_aihelper_canonical_device_name_forwards_to_legacy_helper_wire_name():
    conn = DummyConnection('compat-helper')
    sessions.bind_workspace('session-A', conn, mode='read_only', capabilities=['computer_observe'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'aihelper_observe', {})
    assert result['isError'] is False
    assert conn.calls[-1][0] == 'client_workspace_tool'
    assert conn.calls[-1][1]['tool'] == 'computer_observe'


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


def test_resume_token_reconnects_same_session_after_suspend():
    code = sessions.create_web_pair_code()
    first = DummyConnection('mobile-1')
    sessions.register_waiting_workspace(code, first, mode='write', task='mobile', capabilities=['file_read', 'file_edit'])
    claimed = sessions.claim_waiting_workspace(code, 'session-A')
    token = claimed['resume_token']
    sessions.suspend_connection(first)
    assert sessions.pair_code_kind(code) == ('invalid', None)
    second = DummyConnection('mobile-2')
    resumed = sessions.reconnect_workspace_with_resume_token(token, second, mode='write', task='mobile', capabilities=['file_read', 'file_edit'])
    assert resumed['session_id'] == 'session-A'
    assert resumed['resume_token'] == token
    assert sessions.get_workspace('session-A')['client_id'] == 'mobile-2'


def test_resume_token_can_authorize_an_additional_transport_alias():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-1')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'session-A')
    second = sessions.claim_workspace_with_resume_token(first['resume_token'], 'session-B')
    assert first['lease_id'] == second['lease_id']
    with pytest.raises(ValueError, match='already been used'):
        sessions.claim_waiting_workspace(code, 'session-C')
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


def test_detached_transport_can_rebind_same_lease_with_resume_token():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('lease-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read', 'file_edit'])
    first = sessions.claim_waiting_workspace(code, 'transport-A')
    sessions.mark_transport_detached('transport-A')
    rebound = sessions.claim_workspace_with_resume_token(first['resume_token'], 'transport-B')
    assert rebound['lease_id'] == first['lease_id']
    assert sessions.get_workspace('transport-B')['client_id'] == 'lease-browser'


def test_pair_code_does_not_authorize_multiple_concurrent_mcp_aliases():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('lease-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'transport-A')
    with pytest.raises(ValueError, match='already been used'):
        sessions.claim_waiting_workspace(code, 'transport-B')
    second = sessions.claim_workspace_with_resume_token(first['resume_token'], 'transport-B')
    assert first['lease_id'] == second['lease_id']


def test_browser_resume_refreshes_all_authorized_mcp_aliases():
    code = sessions.create_web_pair_code()
    first_conn = DummyConnection('browser-1')
    sessions.register_waiting_workspace(code, first_conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt')
    sessions.claim_workspace_with_resume_token(first['resume_token'], 'mistral')
    sessions.suspend_connection(first_conn)
    second_conn = DummyConnection('browser-2')
    sessions.reconnect_workspace_with_resume_token(first['resume_token'], second_conn, mode='write', capabilities=['file_read'])
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
async def test_real_mcp_session_reclaims_stateless_lease_with_resume_token():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('stateless-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    sessionless = DummyRequest('')
    paired = await call_public_local_tool(sessionless, 'workspace_pair', {'code': code})
    token = paired['structuredContent']['workspace_token']
    sessioned = DummyRequest('real-session')
    rebound = await call_public_local_tool(sessioned, 'workspace_status', {'workspace_token': token})
    assert rebound['structuredContent']['ok'] is True
    assert sessions.get_workspace('real-session')['client_id'] == 'stateless-browser'
    reused = await call_public_local_tool(sessioned, 'workspace_pair', {'code': code})
    assert reused['structuredContent']['ok'] is False


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


def test_legacy_session_pair_is_consumed_when_promoted_to_lease():
    code = sessions.get_or_create_pair_code('chatgpt-session')
    conn = DummyConnection('browser-direct')
    bound = sessions.promote_session_pair_to_web_lease(
        code, 'chatgpt-session', conn, mode='write', capabilities=['file_read', 'file_edit']
    )
    assert bound['session_id'] == 'chatgpt-session'
    assert sessions.resolve_pair_code(code) is None
    assert sessions.pair_code_kind(code) == ('invalid', None)
    second = sessions.claim_workspace_with_resume_token(bound['resume_token'], 'mistral-session')
    assert second['lease_id'] == bound['lease_id']


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


def test_suspended_workspace_can_authorize_new_transport_alias_with_resume_token():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('mobile-alias')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['code_tree'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    token = first['resume_token']
    sessions.suspend_connection(conn)
    second = sessions.claim_workspace_with_resume_token(token, 'mistral-B')
    assert second['lease_id'] == first['lease_id']
    replacement = DummyConnection('mobile-alias-reconnected')
    sessions.reconnect_workspace_with_resume_token(token, replacement, mode='write', capabilities=['code_tree'])
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
    assert old_pair is None or 'session-A' not in old_pair.get('paired_session_ids', [])
    if old_pair is not None:
        assert old_pair.get('paired_session_id') != 'session-A'


def test_direct_new_pair_replaces_old_suspended_lease_for_same_session():
    old_code = sessions.create_web_pair_code()
    old_conn = DummyConnection('old-direct-helper')
    sessions.register_waiting_workspace(old_code, old_conn, mode='write', capabilities=['file_read'])
    old_bound = sessions.claim_waiting_workspace(old_code, 'session-replace')
    sessions.suspend_connection(old_conn)

    new_code = sessions.create_web_pair_code()
    new_conn = DummyConnection('new-direct-helper')
    sessions.register_waiting_workspace(new_code, new_conn, mode='write', capabilities=['file_read', 'file_edit'])
    new_bound = sessions.claim_waiting_workspace(new_code, 'session-replace')

    assert new_bound['lease_id'] != old_bound['lease_id']
    assert sessions.get_workspace('session-replace')['client_id'] == 'new-direct-helper'
    old_pair = sessions.resolve_web_pair_code(old_code)
    assert old_pair is None or 'session-replace' not in old_pair.get('paired_session_ids', [])
    if old_pair is not None:
        assert old_pair.get('paired_session_id') != 'session-replace'


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



def test_repeated_pair_claim_is_rejected_but_resume_token_remains_valid():
    code = sessions.create_web_pair_code()
    conn = DummyConnection('idempotent-token-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'same-transport')
    assert first['resume_token']
    with pytest.raises(ValueError, match='already been used'):
        sessions.claim_waiting_workspace(code, 'same-transport')
    assert sessions.resolve_resume_token(first['resume_token']) is not None


def test_resume_token_claim_returns_proven_durable_token(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('token-return-browser')
    sessions.register_waiting_workspace(code, conn, mode='read_only', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'transport-A')
    token = first['resume_token']
    rebound = sessions.claim_workspace_with_resume_token(token, 'transport-B')
    assert rebound['resume_token'] == token

def test_pair_code_stays_short_lived_after_durable_lease_created(monkeypatch):
    code = sessions.create_web_pair_code()
    conn = DummyConnection('pair-expiry-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    bound = sessions.claim_waiting_workspace(code, 'session-A')
    token = bound['resume_token']
    item = sessions._WEB_PAIR[sessions._pair_key(code)]
    item['pair_expires_at'] = sessions.time.time() - 1
    assert sessions.resolve_web_pair_code(code) is None
    with pytest.raises(ValueError, match='workspace pairing code'):
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


def test_app_handoff_is_one_shot_and_rotates_resume_owner():
    code = sessions.create_web_pair_code()
    browser = DummyConnection('browser-owner')
    sessions.register_waiting_workspace(code, browser, mode='write', capabilities=['file_read'])
    bound = sessions.claim_waiting_workspace(code, 'session-A')
    old_token = bound['resume_token']
    handoff_code = sessions.create_workspace_handoff_ticket(old_token, target='android')

    handoff = sessions.consume_workspace_handoff_ticket(handoff_code)
    assert handoff['target'] == 'android'
    assert sessions.consume_workspace_handoff_ticket(handoff_code) == {}

    app = DummyConnection('android-app')
    moved = sessions.complete_workspace_handoff(
        handoff, app, mode='write', capabilities=['file_read']
    )
    new_token = moved['resume_token']
    assert moved['previous_connection'] is browser
    assert new_token and new_token != old_token
    assert sessions.resolve_resume_token(old_token) is None
    assert sessions.resolve_resume_token(new_token) is not None
    assert sessions.get_workspace('session-A')['client_id'] == 'android-app'


def test_uncompleted_handoff_does_not_revoke_browser_resume_token():
    code = sessions.create_web_pair_code()
    browser = DummyConnection('browser-owner')
    sessions.register_waiting_workspace(code, browser, mode='read_only', capabilities=['file_read'])
    bound = sessions.claim_waiting_workspace(code, 'session-A')
    token = bound['resume_token']
    handoff_code = sessions.create_workspace_handoff_ticket(token, target='android')
    assert handoff_code
    assert sessions.resolve_resume_token(token) is not None
    assert sessions.get_workspace('session-A')['client_id'] == 'browser-owner'


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
    delete = await call_public_local_tool(req, 'file_ops', {'action': 'delete', 'path': 'folder', 'recursive': True})
    assert delete['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    edit = await call_public_local_tool(req, 'code_edit', {'path': 'x.py', 'mode': 'append', 'new_text': 'x'})
    assert edit['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'
    commit = await call_public_local_tool(req, 'git', {'mode': 'commit', 'message': 'x'})
    assert commit['structuredContent']['code'] == 'WORKSPACE_READ_ONLY'


def test_workspace_join_id_is_consumed_when_lease_is_claimed(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    assert first['lease_id']
    assert sessions.resolve_web_pair_code(code) is None
    assert sessions.resolve_resume_token(first['resume_token']) is not None


def test_consumed_join_id_cannot_add_second_client_alias(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    with pytest.raises(ValueError, match='already been used'):
        sessions.claim_waiting_workspace(code, 'telegram-B')
    second = sessions.claim_workspace_with_resume_token(first['resume_token'], 'telegram-B')
    assert second['lease_id'] == first['lease_id']


def test_resume_token_restores_lease_from_redis_after_process_loss(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sessions, '_redis_client', lambda: fake)
    code = sessions.create_web_pair_code()
    conn = DummyConnection('join-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    first = sessions.claim_waiting_workspace(code, 'chatgpt-A')
    token = first['resume_token']
    lease_id = first['lease_id']
    sessions._SESSION_WORKSPACE.clear()
    sessions._WEB_PAIR.clear()
    sessions._RESUME_INDEX.clear()
    assert sessions.resolve_web_pair_code(code) is None
    restored = sessions.claim_workspace_with_resume_token(token, 'telegram-after-restart')
    assert restored['lease_id'] == lease_id


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


@pytest.mark.asyncio
async def test_authenticated_bridge_workspace_context_survives_transport_churn_and_isolates_chats():
    from app.services.mcp_workspace_bridge import workspace_affinity_id

    class BridgeRequest:
        def __init__(self, transport: str):
            self.state = SimpleNamespace(
                mcp_auth_method='bearer',
                mcp_session_id=transport,
                mcp_workspace_subject='telegram-bridge-subject-1',
            )
            self.headers = {}

    first_transport = BridgeRequest('mcp-A')
    replacement_transport = BridgeRequest('mcp-B')
    owner_context = 'telegram-chat-owner'
    group_context = 'telegram-chat-group'

    assert workspace_affinity_id(first_transport, owner_context) == workspace_affinity_id(replacement_transport, owner_context)
    assert workspace_affinity_id(first_transport, owner_context) != workspace_affinity_id(first_transport, group_context)

    code = sessions.create_web_pair_code()
    conn = DummyConnection('telegram-browser')
    sessions.register_waiting_workspace(code, conn, mode='write', capabilities=['file_read'])
    paired = await call_public_local_tool(
        first_transport, 'workspace_status',
        {'workspace_id': code, 'workspace_context': owner_context},
    )
    assert paired['structuredContent']['ok'] is True

    resumed = await call_public_local_tool(
        replacement_transport, 'workspace_status',
        {'workspace_context': owner_context},
    )
    assert resumed['structuredContent']['lease_id'] == paired['structuredContent']['lease_id']

    isolated = await call_public_local_tool(
        replacement_transport, 'workspace_status',
        {'workspace_context': group_context},
    )
    assert isolated['structuredContent']['code'] == 'WORKSPACE_REQUIRED'


def test_authenticated_workspace_affinity_does_not_expose_subject_or_context():
    from app.services.mcp_workspace_bridge import workspace_affinity_id
    req = SimpleNamespace(
        state=SimpleNamespace(mcp_workspace_subject='secret-bridge-subject', mcp_auth_method='bearer'),
        headers={},
    )
    value = workspace_affinity_id(req, 'telegram-chat-123')
    assert value.startswith('auth-workspace-')
    assert 'secret-bridge-subject' not in value
    assert 'telegram-chat-123' not in value


@pytest.mark.asyncio
async def test_destructive_tool_timeout_is_reported_as_uncertain():
    """A timed-out destructive call must never surface as a plain transport error.

    ClientConnection.send_tool_call converts asyncio timeouts into
    HTTPException(504), so the bridge has to recognise that shape as well or the
    caller loses the 'a write may already have happened' warning.
    """
    from fastapi import HTTPException

    from app.services import mcp_workspace_bridge as bridge

    class TimingOutConnection(DummyConnection):
        async def send_tool_call(self, name, args, timeout=0):
            self.calls.append((name, args, timeout))
            raise HTTPException(504, f'Client timeout für Tool: {name}')

    conn = TimingOutConnection('slow-executor')
    sessions.bind_workspace('session-T', conn, mode='write', capabilities=['workspace_clear'])
    req = DummyRequest('session-T')
    result = await bridge.call_public_local_tool(req, 'workspace_clear', {'confirm': 'DELETE_ALL'})
    structured = result['structuredContent']
    assert result['isError'] is True
    assert structured['code'] == 'WORKSPACE_EXECUTION_UNCERTAIN'
    assert structured['retryable'] is False
    assert structured['execution_started'] is True
    assert len(conn.calls) == 1


@pytest.mark.asyncio
async def test_plain_timeout_error_is_reported_as_uncertain():
    from app.services import mcp_workspace_bridge as bridge

    class TimeoutErrorConnection(DummyConnection):
        async def send_tool_call(self, name, args, timeout=0):
            self.calls.append((name, args, timeout))
            raise TimeoutError('no result')

    conn = TimeoutErrorConnection('slow-executor-2')
    sessions.bind_workspace('session-T2', conn, mode='write', capabilities=['file_edit'])
    req = DummyRequest('session-T2')
    result = await bridge.call_public_local_tool(
        req, 'file_edit', {'path': 'x.txt', 'operation': 'write', 'content': 'x'}
    )
    structured = result['structuredContent']
    assert structured['code'] == 'WORKSPACE_EXECUTION_UNCERTAIN'
    assert structured['retryable'] is False
    assert structured['execution_started'] is True


def _image_result(*, mime='image/png', data_url=None, data=None, **meta):
    structured = {'mime': mime, **meta}
    if data_url is not None:
        structured['data_url'] = data_url
    if data is not None:
        structured['data'] = data
    return {
        'content': [{'type': 'text', 'text': __import__('json').dumps(structured)}],
        'structuredContent': structured,
        'isError': False,
    }


def test_display_result_normalizes_desktop_data_url_to_native_image_content():
    import base64
    from app.services.mcp_workspace_bridge import _normalize_display_tool_result

    raw = b'\x89PNG\r\n\x1a\nimage-bytes'
    encoded = base64.b64encode(raw).decode('ascii')
    result = _normalize_display_tool_result(
        'computer_screenshot',
        _image_result(
            mime='image/png',
            data_url='data:image/png;base64,' + encoded,
            width=800,
            height=600,
            source='primary-screen',
        ),
    )
    assert result['content'] == [{'type': 'image', 'data': encoded, 'mimeType': 'image/png'}]
    assert result['structuredContent']['mimeType'] == 'image/png'
    assert result['structuredContent']['width'] == 800
    assert result['structuredContent']['height'] == 600
    assert result['structuredContent']['source'] == 'primary-screen'
    assert 'data_url' not in result['structuredContent']


def test_display_result_normalizes_raw_base64_and_preserves_jpeg_mime():
    import base64
    from app.services.mcp_workspace_bridge import _normalize_display_tool_result

    encoded = base64.b64encode(b'\xff\xd8\xff\xd9').decode('ascii')
    result = _normalize_display_tool_result(
        'computer_observe',
        {
            'content': [{'type': 'text', 'text': 'legacy'}],
            'structuredContent': {'mime_type': 'image/jpeg', 'data': encoded, 'source': 'android-screen'},
            'isError': False,
        },
    )
    assert result['content'][0]['type'] == 'image'
    assert result['content'][0]['mimeType'] == 'image/jpeg'
    assert result['content'][0]['data'] == encoded
    assert result['structuredContent']['source'] == 'android-screen'
    assert 'data' not in result['structuredContent']


def test_display_result_rejects_malformed_base64_without_echoing_payload():
    from app.services.mcp_workspace_bridge import _normalize_display_tool_result

    result = _normalize_display_tool_result(
        'computer_screenshot',
        _image_result(mime='image/png', data='not-valid-base64%%%'),
    )
    assert result['isError'] is True
    assert result['structuredContent']['code'] == 'WORKSPACE_IMAGE_INVALID'
    assert 'not-valid-base64' not in result['content'][0]['text']


def test_display_result_leaves_normal_dict_and_non_display_tools_unchanged():
    from app.services.mcp_workspace_bridge import _normalize_display_tool_result

    normal = {'content': [{'type': 'text', 'text': '{"ok":true}'}], 'structuredContent': {'ok': True}, 'isError': False}
    assert _normalize_display_tool_result('computer_screenshot', normal) is normal

    encoded_like = {
        'content': [{'type': 'text', 'text': 'legacy'}],
        'structuredContent': {'mime': 'image/png', 'data': 'YWJj'},
        'isError': False,
    }
    assert _normalize_display_tool_result('file_read', encoded_like) is encoded_like


@pytest.mark.asyncio
async def test_display_execution_requires_active_display_grant():
    from app.services import mcp_workspace_bridge as bridge

    conn = DummyConnection('display-no-grant')
    conn.share_manifest = {
        'version': 1,
        'grants': {
            'resource://workspace': {'read': True, 'write': True},
            'resource://display': {'observe': False},
        },
    }
    sessions.bind_workspace('session-display', conn, mode='write', capabilities=['computer_screenshot'])
    req = DummyRequest('session-display')
    result = await bridge.call_public_local_tool(req, 'computer_screenshot', {})
    assert result['isError'] is True
    assert result['structuredContent']['code'] == 'WORKSPACE_DISPLAY_GRANT_REQUIRED'
    assert conn.calls == []


@pytest.mark.asyncio
async def test_compute_execution_requires_explicit_compute_grant():
    from app.services import mcp_workspace_bridge as bridge

    conn = DummyConnection('compute-no-grant')
    conn.share_manifest = {
        'version': 1,
        'grants': [
            {'resource': 'workspace', 'action': 'read'},
            {'resource': 'workspace', 'action': 'write'},
        ],
    }
    sessions.bind_workspace('session-compute', conn, mode='write', capabilities=['compute_execute'])
    req = DummyRequest('session-compute')
    result = await bridge.call_public_local_tool(req, 'compute_execute', {'command': 'true'})
    assert result['isError'] is True
    assert result['structuredContent']['code'] == 'WORKSPACE_COMPUTE_GRANT_REQUIRED'
    assert conn.calls == []


@pytest.mark.asyncio
async def test_browser_remote_compute_is_intercepted_by_triforce_sandbox(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge
    from app.services import workspace_compute_sandbox as sandbox

    conn = DummyConnection('browser-remote-compute')
    conn.share_manifest = {
        'resources': [
            {'type': 'workspace', 'enabled': True, 'mode': 'read_write'},
            {'type': 'compute', 'enabled': True, 'runtime': 'triforce_docker', 'available': True},
        ],
        'grants': [
            {'resource': 'workspace', 'action': 'read'},
            {'resource': 'workspace', 'action': 'write'},
            {'resource': 'compute', 'action': 'execute'},
        ],
    }
    seen = {}

    async def fake_remote_compute(**kwargs):
        seen.update(kwargs)
        return {'content': [{'type': 'text', 'text': 'sandbox-ok'}], 'structuredContent': {'ok': True, 'backend': 'triforce_docker'}, 'isError': False}

    monkeypatch.setattr(sandbox, 'execute_remote_compute', fake_remote_compute)
    sessions.bind_workspace('session-browser-compute', conn, mode='write', capabilities=['file_read', 'file_edit', 'file_ops', 'compute_execute'])
    req = DummyRequest('session-browser-compute')
    result = await bridge.call_public_local_tool(req, 'compute_execute', {'command': 'python -V', 'cwd': '.'})

    assert result['isError'] is False
    assert result['structuredContent']['backend'] == 'triforce_docker'
    assert seen['connection'] is conn
    assert seen['mode'] == 'write'
    assert seen['arguments']['command'] == 'python -V'
    assert 'compute_execute' in seen['capabilities']
    assert conn.calls == []


@pytest.mark.asyncio
async def test_portable_device_read_operation_allowed_in_read_only_binding():
    conn = DummyConnection()
    conn.share_manifest = {'grants': [{'resource': 'device', 'action': 'read'}]}
    sessions.bind_workspace('session-A', conn, mode='read_only', capabilities=['process_ops'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'process_ops', {'action': 'list'})
    assert result['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'process_ops'


@pytest.mark.asyncio
async def test_portable_device_mutation_blocked_in_read_only_binding():
    conn = DummyConnection()
    conn.share_manifest = {'grants': [{'resource': 'device', 'action': 'read'}]}
    sessions.bind_workspace('session-A', conn, mode='read_only', capabilities=['process_ops', 'service_ops', 'computer_input'])
    req = DummyRequest('session-A')
    blocked_process = await call_public_local_tool(req, 'process_ops', {'action': 'signal', 'pid': 123, 'signal': 'terminate'})
    assert blocked_process['structuredContent']['code'] == 'WORKSPACE_DEVICE_CONTROL_GRANT_REQUIRED'
    blocked_service = await call_public_local_tool(req, 'service_ops', {'action': 'restart', 'service': 'example'})
    assert blocked_service['structuredContent']['code'] == 'WORKSPACE_DEVICE_CONTROL_GRANT_REQUIRED'
    blocked_input = await call_public_local_tool(req, 'computer_input', {'action': 'click', 'x': 1, 'y': 1})
    assert blocked_input['structuredContent']['code'] == 'WORKSPACE_DISPLAY_CONTROL_GRANT_REQUIRED'
    assert conn.calls == []


@pytest.mark.asyncio
async def test_computer_input_uses_display_control_without_extra_device_control_grant():
    conn = DummyConnection('android-control')
    conn.share_manifest = {
        'grants': [{'resource': 'display', 'action': 'control'}],
        'resources': {'display': {'enabled': True, 'control': True}},
    }
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['computer_input'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(req, 'computer_input', {'action': 'home'})
    assert result['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'computer_input'


@pytest.mark.asyncio
async def test_app_ops_always_requires_device_control_grant():
    conn = DummyConnection('android-apps')
    conn.share_manifest = {
        'grants': [],
        'resources': {'device': {'enabled': False, 'control': False}},
    }
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['app_ops'])
    req = DummyRequest('session-A')
    blocked = await call_public_local_tool(req, 'app_ops', {'action': 'list'})
    assert blocked['structuredContent']['code'] == 'WORKSPACE_DEVICE_CONTROL_GRANT_REQUIRED'
    assert conn.calls == []

    conn.share_manifest = {
        'grants': [{'resource': 'device', 'action': 'control'}],
        'resources': {'device': {'enabled': True, 'control': True}},
    }
    allowed = await call_public_local_tool(req, 'app_ops', {'action': 'list'})
    assert allowed['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'app_ops'


def test_live_vision_tools_are_canonical_and_discoverable_when_unpaired():
    from app.mcp.workspace_tool_contract import WORKSPACE_TOOL_NAMES
    from app.services.mcp_workspace_bridge import DISCOVERABLE_LOCKED_LOCAL_TOOLS

    for name in ('aihelper_vision_start', 'aihelper_vision_status', 'aihelper_vision_observe', 'aihelper_vision_stop'):
        assert name in WORKSPACE_TOOL_NAMES
        assert name in DISCOVERABLE_LOCKED_LOCAL_TOOLS


def test_live_vision_result_normalizes_cached_jpeg_to_native_image_content():
    import base64
    from app.services.mcp_workspace_bridge import _normalize_display_tool_result

    encoded = base64.b64encode(b'\xff\xd8cached-live-frame\xff\xd9').decode('ascii')
    result = _normalize_display_tool_result(
        'vision_observe',
        _image_result(
            mime='image/jpeg', data=encoded, frame_id=42, scene_id=9,
            frame_age_ms=17, width=432, height=960,
        ),
    )
    assert result['content'][0] == {'type': 'image', 'data': encoded, 'mimeType': 'image/jpeg'}
    assert result['structuredContent']['frame_id'] == 42
    assert result['structuredContent']['scene_id'] == 9
    assert result['structuredContent']['frame_age_ms'] == 17
    assert 'data' not in result['structuredContent']


@pytest.mark.asyncio
async def test_live_vision_requires_active_display_grant():
    from app.services import mcp_workspace_bridge as bridge

    conn = DummyConnection('vision-no-grant')
    conn.share_manifest = {
        'version': 1,
        'grants': {
            'resource://workspace': {'read': True, 'write': True},
            'resource://display': {'observe': False},
        },
    }
    sessions.bind_workspace('session-vision', conn, mode='write', capabilities=['vision_observe'])
    req = DummyRequest('session-vision')
    result = await bridge.call_public_local_tool(req, 'vision_observe', {'visual': False})
    assert result['isError'] is True
    assert result['structuredContent']['code'] == 'WORKSPACE_DISPLAY_GRANT_REQUIRED'
    assert conn.calls == []


@pytest.mark.asyncio
async def test_cached_shell_schema_can_bridge_to_device_tool_without_shell_capability():
    conn = DummyConnection('android-compat')
    conn.share_manifest = {
        'grants': [{'resource': 'device', 'action': 'control'}],
        'resources': {'device': {'enabled': True, 'control': True}},
    }
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['app_ops'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(
        req,
        'shell',
        {'command': '@device app_ops {"action":"launch","app":"Claude"}'},
    )
    assert result['isError'] is False
    assert conn.calls[-1][1]['tool'] == 'app_ops'
    assert conn.calls[-1][1]['arguments'] == {'action': 'launch', 'app': 'Claude'}


@pytest.mark.asyncio
async def test_cached_shell_device_alias_still_enforces_live_capability_gate():
    conn = DummyConnection('android-compat-no-vision')
    conn.share_manifest = {
        'grants': [{'resource': 'display', 'action': 'observe'}],
        'resources': {'display': {'enabled': True, 'observe': True}},
    }
    sessions.bind_workspace('session-A', conn, mode='write', capabilities=['app_ops'])
    req = DummyRequest('session-A')
    result = await call_public_local_tool(
        req,
        'shell',
        {'command': '@device vision_status {}'},
    )
    assert result['isError'] is True
    assert result['structuredContent']['code'] == 'WORKSPACE_TOOL_UNAVAILABLE'
    assert conn.calls == []


def test_cached_shell_device_info_alias_is_read_only_compatible():
    from app.services.mcp_workspace_bridge import _device_tool_from_shell_alias

    tool, arguments = _device_tool_from_shell_alias('shell', {'command': '@device device_info {}'})
    assert tool == 'device_info'
    assert arguments == {}


def test_cached_shell_device_alias_rejects_unknown_target():
    from app.services.mcp_workspace_bridge import _device_tool_from_shell_alias

    with pytest.raises(ValueError):
        _device_tool_from_shell_alias('shell', {'command': '@device shell {"command":"id"}'})


def test_workspace_socket_ticket_subprotocol_parser_ignores_base_protocol():
    from app.routes.mcp_node import _workspace_ticket_from_subprotocol

    headers = {"sec-websocket-protocol": "ailinux-workspace-v1, ailinux-ticket.ABCD-1234-EF56"}
    assert _workspace_ticket_from_subprotocol(headers) == "ABCD-1234-EF56"
    assert _workspace_ticket_from_subprotocol({"sec-websocket-protocol": "ailinux-workspace-v1"}) == ""



def test_workspace_transport_ticket_accepts_native_header():
    from app.routes.mcp_node import _workspace_transport_ticket

    headers = {"x-ailinux-socket-ticket": "abcd-1234-ef56"}
    assert _workspace_transport_ticket(headers) == "ABCD-1234-EF56"


def test_workspace_transport_ticket_prefers_browser_subprotocol():
    from app.routes.mcp_node import _workspace_transport_ticket

    headers = {
        "sec-websocket-protocol": "ailinux-workspace-v1, ailinux-ticket.BROWSER-1234",
        "x-ailinux-socket-ticket": "native-5678",
    }
    assert _workspace_transport_ticket(headers) == "BROWSER-1234"


def test_webmcp_setup_html_uses_content_fingerprint_for_mutable_assets(tmp_path, monkeypatch):
    from app.routes.mcp import _webmcp_build_key, _workspace_setup_html

    web = tmp_path / "apps" / "web"
    web.mkdir(parents=True)
    (web / "index.html").write_text(
        '<html><head><link rel="stylesheet" href="/v1/mcp/web/styles.css?v=old">'
        '<link rel="manifest" href="/v1/mcp/manifest.webmanifest?v=old"></head>'
        '<body><script src="/v1/mcp/web/app.js?v=old"></script></body></html>',
        encoding="utf-8",
    )
    (web / "styles.css").write_text("body{}", encoding="utf-8")
    (web / "app.js").write_text("console.log('one')", encoding="utf-8")
    (web / "pyodide-worker.js").write_text("self.onmessage=()=>{}", encoding="utf-8")
    (web / "sw.js").write_text("self.addEventListener('fetch',()=>{})", encoding="utf-8")
    monkeypatch.setenv("AILINUX_HELPER_SOURCE", str(tmp_path))

    first = _webmcp_build_key()
    html = _workspace_setup_html()
    assert f'/v1/mcp/web/app.js?v={first}' in html
    assert f'/v1/mcp/web/styles.css?v={first}' in html
    assert f'/v1/mcp/manifest.webmanifest?v={first}' in html
    assert f'<meta name="ailinux-webmcp-build" content="{first}">' in html

    (web / "app.js").write_text("console.log('two')", encoding="utf-8")
    assert _webmcp_build_key() != first


def test_webmcp_mutable_asset_headers_disable_browser_and_cdn_caches():
    from app.routes.mcp import _webmcp_no_store_headers

    headers = _webmcp_no_store_headers()
    assert "no-store" in headers["Cache-Control"]
    assert headers["CDN-Cache-Control"] == "no-store"
    assert headers["Cloudflare-CDN-Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_successful_workspace_pair_queues_tools_list_changed_notification():
    import asyncio
    from app.routes import mcp as mcp_route

    session_id = "tool-refresh-session"
    previous = mcp_route._mcp_sessions.pop(session_id, None)
    try:
        mcp_route._mcp_sessions[session_id] = {
            "created": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            "last_seen": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
            "queue": asyncio.Queue(),
            "initialized": True,
        }
        result = {"structuredContent": {"ok": True, "connected": True}, "isError": False}
        assert mcp_route._workspace_call_changes_tool_inventory("workspace_pair", {"code": "redacted"}, result) is True
        assert await mcp_route._queue_tools_list_changed(session_id) is True
        notice = mcp_route._mcp_sessions[session_id]["queue"].get_nowait()
        assert notice == {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}
    finally:
        mcp_route._mcp_sessions.pop(session_id, None)
        if previous is not None:
            mcp_route._mcp_sessions[session_id] = previous


def test_workspace_inventory_refresh_only_fires_for_successful_state_changes():
    from app.routes import mcp as mcp_route

    ok = {"structuredContent": {"ok": True, "connected": True}, "isError": False}
    error = {"structuredContent": {"ok": False}, "isError": True}
    assert mcp_route._workspace_call_changes_tool_inventory("workspace_status", {"workspace_id": "redacted"}, ok) is True
    assert mcp_route._workspace_call_changes_tool_inventory("workspace_status", {}, ok) is False
    assert mcp_route._workspace_call_changes_tool_inventory("aihelper_pair", {"action": "disconnect"}, ok) is True
    assert mcp_route._workspace_call_changes_tool_inventory("workspace_pair", {}, error) is False


@pytest.mark.asyncio
async def test_workspace_only_tool_without_lease_skips_v4_error_path(monkeypatch):
    from app.routes import mcp as mcp_route

    async def forbidden_v4(*_args, **_kwargs):
        raise AssertionError("workspace-only tool must not enter v4 dispatch")

    monkeypatch.setattr(mcp_route, "call_v4_tool", forbidden_v4)
    result = await mcp_route.handle_tools_call({"name": "file_tree", "arguments": {"path": "."}})

    assert result["isError"] is True
    assert result["structuredContent"]["code"] == "workspace_not_paired"
    assert result["structuredContent"]["source"] == "workspace_bridge"
