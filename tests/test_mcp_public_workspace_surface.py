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
async def test_internal_admin_catalog_keeps_admin_tools_and_adds_workspace_overlay():
    result = await handle_tools_list({}, request=FakeRequest('bearer', True))
    names = {tool['name'] for tool in result['tools']}
    assert {'workspace_status', 'workspace_pair', 'workspace_info', 'file_read', 'file_tree', 'file_edit'} <= names
    assert 'shell' in names


def test_browser_workspace_page_contains_direct_folder_runtime_without_helper_uri():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'showDirectoryPicker' in html
    assert 'heartbeatTimer=null' in html
    assert "new URLSearchParams(location.search).get('pair_code')" in html
    assert 'history.replaceState' in html
    assert 'manualDisconnect||(!resumeToken&&!pairCode)||reconnectTimer||!navigator.onLine' in html
    assert 'manualDisconnect||(!resumeToken&&!pairCode)||(!rootHandle&&!rootEntry)||!navigator.onLine' in html
    assert "pairing code|workspace credential|resume token" in html
    assert "sessionStorage.removeItem('tf_pair_code')" in html
    assert 'Workspace pairing expired. Creating a fresh pairing ID' in html
    assert 'function startHeartbeat()' in html
    assert 'watchdogTimer' in html
    assert 'workspace/paired' in html
    assert 'workspace/detached' in html
    assert 'Workspace connected and AI-reachable.' in html
    assert 'openChromeBtn' in html
    assert 'com.android.chrome' in html
    assert 'readwrite' in html
    assert 'createWritable' in html
    assert 'Nothing has been enumerated' in html
    assert "mode:workspaceMode==='write'?'readwrite':'read'" in html
    assert 'Selecting a folder does not enumerate or analyze it' in html
    assert 'legacyEntries' in html
    assert 'stays on this device' in html
    assert 'folderListPicker' not in html
    assert 'webkitdirectory' not in html
    assert 'fallbackLabel' not in html
    assert 'dropZone' in html
    assert 'getAsFileSystemHandle' in html
    assert 'getAsEntry' in html
    assert 'browserCaps' in html
    assert "for(const ev of ['dragenter','dragover'])" in html
    assert 'webkitGetAsEntry' in html
    assert 'new WebSocket' in html
    assert 'visibilitychange' in html
    assert 'pageshow' in html
    assert "window.addEventListener('pagehide'" not in html
    assert 'tf_resume_token' not in html
    assert 'indexedDB' in html
    assert "const persistentHandleStore='indexedDB' in window" in html
    assert 'directory handle did not survive IndexedDB round-trip' in html
    assert 'isSameEntry' in html
    assert 'Folder handle shared and persistence verified.' in html
    assert 'Saved directory handle restored · permission=' in html
    assert '&resume_token=' not in html
    assert 'data-cfasync="false"' in html
    assert 'client_workspace_tool' in html
    assert 'triforce-workspace://' not in html
    assert 'id="pairPanel" class="hidden"' in html
    assert '/v1/mcp/workspace/pair-ticket' in html
    assert "deleteWorkspaceState('resumeToken')" in html
    assert "loadWorkspaceState('workspaceMode')" in html
    assert '/v1/mcp/workspace/resume-ticket' in html
    assert '__PAIR_CODE__' not in html
    assert 'persistent workspace session' in html
    assert "saveWorkspaceState('resumeToken'" in html
    assert "resume_token" in html
    assert "resumeToken" in html
