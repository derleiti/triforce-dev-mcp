from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.routes.mcp import handle_tools_list


class FakeRequest:
    def __init__(self, method: str, full: bool, session_id: str = 'surface-session'):
        self.headers = {}
        self.client = SimpleNamespace(host='203.0.113.10')
        self.state = SimpleNamespace(
            mcp_auth_method=method,
            mcp_auth_user=method,
            mcp_auth_full_access=full,
            mcp_session_id=session_id,
        )


@pytest.mark.asyncio
async def test_public_guest_gets_cloud_surface_plus_local_workspace_tools():
    result = await handle_tools_list({}, request=FakeRequest('public_guest', False))
    names = {tool['name'] for tool in result['tools']}
    assert {'search', 'models', 'workspace_status', 'workspace_pair', 'file_read', 'code_search', 'file_edit', 'shell', 'test'} <= names
    assert 'service_control' not in names
    assert 'restart_backend' not in names
    assert {'mail_inbox', 'mail_read', 'memory_search', 'notify_list', 'group_chat_read', 'system_info', 'logs'}.isdisjoint(names)


@pytest.mark.asyncio
async def test_internal_admin_catalog_is_not_replaced_by_public_workspace_overlay():
    result = await handle_tools_list({}, request=FakeRequest('bearer', True))
    names = {tool['name'] for tool in result['tools']}
    assert 'workspace_status' not in names
    assert 'shell' in names
