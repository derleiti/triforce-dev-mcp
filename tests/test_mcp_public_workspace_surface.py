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
    assert {'search', 'models', 'workspace_status', 'workspace_pair', 'workspace_info', 'file_read', 'file_tree', 'code_search', 'file_edit', 'directory_create'} <= names
    assert {'shell', 'test', 'lint', 'task_runner', 'binary_exec', 'git', 'service_control', 'restart_backend'}.isdisjoint(names)
    assert {'mail_inbox', 'mail_read', 'memory_search', 'notify_list', 'group_chat_read', 'system_info', 'logs'}.isdisjoint(names)


@pytest.mark.asyncio
async def test_internal_admin_catalog_is_not_replaced_by_public_workspace_overlay():
    result = await handle_tools_list({}, request=FakeRequest('bearer', True))
    names = {tool['name'] for tool in result['tools']}
    assert 'workspace_status' not in names
    assert 'shell' in names


def test_browser_workspace_page_contains_direct_folder_runtime_without_helper_uri():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'showDirectoryPicker' in html
    assert 'Nothing is uploaded' in html or 'Nothing was uploaded' in html
    assert 'type="file"' not in html
    assert 'webkitdirectory' not in html
    assert 'fallbackLabel' not in html
    assert 'dropZone' in html
    assert 'getAsFileSystemHandle' in html
    assert 'getAsEntry' in html
    assert 'browserCaps' in html
    assert "for(const ev of ['dragenter','dragover'])" in html
    assert 'webkitGetAsEntry' in html
    assert 'new WebSocket' in html
    assert 'data-cfasync="false"' in html
    assert 'client_workspace_tool' in html
    assert 'triforce-workspace://' not in html
    assert 'id="pairPanel" class="hidden"' in html
    assert '/v1/mcp/workspace/pair-ticket' in html
    assert '__PAIR_CODE__' not in html
    assert 'No app, helper, account or API key' in html
