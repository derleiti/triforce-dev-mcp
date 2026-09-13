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
async def test_public_guest_gets_synced_non_admin_local_catalog():
    result = await handle_tools_list({}, request=FakeRequest('public_guest', False))
    by_name = {tool['name']: tool for tool in result['tools']}
    names = set(by_name)

    # Canonical TriForce tools are mirrored automatically, plus workspace helpers.
    assert {'search', 'models', 'agent_start', 'memory_clear', 'service_control',
            'shell', 'git', 'file_ops', 'code_edit', 'code_search', 'code_tree',
            'workspace_status', 'workspace_pair', 'workspace_info', 'file_read',
            'file_tree', 'file_edit', 'directory_create', 'workspace_clear'} <= names

    # Shared-service administration is intentionally absent from Local MCP.
    assert not any(name.startswith('mail_') for name in names)
    assert not any(name.startswith('wp_') for name in names)
    assert not any(name.startswith('flarum_') for name in names)

    # Execution routing is explicit and cannot silently fall through to Hetzner.
    assert by_name['shell']['x_execution'] == 'local_workspace'
    assert by_name['file_ops']['x_execution'] == 'local_workspace'
    file_ops_schema = by_name['file_ops']['inputSchema']['properties']
    assert {'delete', 'remove'} <= set(file_ops_schema['action']['enum'])
    assert file_ops_schema['recursive']['type'] == 'boolean'
    assert by_name['file_ops']['annotations']['destructiveHint'] is True
    assert by_name['code_edit']['x_execution'] == 'local_workspace'
    assert by_name['git']['x_execution'] == 'local_workspace'
    assert by_name['models']['x_execution'] == 'triforce_server'
    assert by_name['service_control']['x_execution'] == 'triforce_server'
    assert by_name['service_control']['x_requires_admin'] is True


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
    assert 'connectPromise=null' in html
    assert 'if(connectPromise)return connectPromise' in html
    assert 'finally{connectPromise=null}' in html
    assert "if(urlPair){resumeToken='';pairCode=urlPair}" in html
    assert 'savedResumeToken' in html
    assert 'savedJoinCode' in html
    assert 'workspace/paired' in html
    assert 'workspace/detached' in html
    assert 'Workspace connected and AI-reachable.' in html
    assert 'openChromeBtn' in html
    assert 'com.android.chrome' in html
    assert 'readwrite' in html
    assert 'createWritable' in html
    assert 'async function fileOps(args)' in html
    assert 'toolQueue=Promise.resolve()' in html
    assert 'function queueToolCall(msg)' in html
    assert 'async function removeEntryCompat(parent,name,recursive=false)' in html
    assert 'await parent.removeEntry(name);' in html
    assert "action==='delete'||action==='remove'" in html
    assert "removeEntry(name,{recursive:true})" in html
    assert "ws.close(4000,'heartbeat timeout')" not in html
    assert "ws.close(4000,'stale after background')" not in html
    assert 'async function codeEdit(args)' in html
    assert "'file_ops'" in html
    assert "'code_edit'" in html
    assert 'Selecting a folder does not enumerate or analyze it' in html
    assert "let lastStatusText=''" in html
    assert 'aria-live="polite"' in html
    assert 'AILinux Helper 2.90.2' in html
    assert "execCommand('copy')" in html
    assert "Copy failed: " in html
    assert '/v1/mcp/helper/android' in html
    assert '/v1/mcp/helper/icon.png?v=2902' in html
    assert '/v1/mcp/helper/linux-appimage' in html
    assert '/v1/mcp/helper/linux-deb' in html
    assert '/v1/mcp/helper/windows' in html
    assert '/v1/mcp/helper/macos' in html
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
    assert 'Folder shared. Starting sandbox executor automatically' in html
    assert 'await connect();' in html
    assert 'id="connectBtn" class="secondary hidden"' in html
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
    assert 'handoffBtn' in html
    assert '/v1/mcp/workspace/handoff-ticket' in html
    assert 'workspace/handoff_complete' in html
    assert "EXECUTOR_VERSION='2.90.2-browser'" in html
    assert "document.addEventListener('freeze'" in html
    assert "document.addEventListener('resume'" in html
    assert "method:'workspace/lifecycle'" in html
    assert "ws.close(4005,'page freeze')" in html
    assert "if(pairCode&&!urlPair)" in html
    assert '__PAIR_CODE__' not in html
    assert 'persistent workspace session' in html
    assert "saveWorkspaceState('resumeToken'" in html
    assert "saveWorkspaceState('joinCode'" in html
    assert "loadWorkspaceState('joinCode')" in html
    assert "deleteWorkspaceState('joinCode')" in html
    assert 'this Join ID remains valid until disconnect or workspace replacement.' in html
    assert "resume_token" in html
    assert "resumeToken" in html


def test_browser_workspace_clear_uses_native_recursive_remove_fast_path():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert "removeEntry(name,{recursive:true})" in html
    assert "NotSupportedError" in html
    assert "workspace_clear" in html
    assert "createBrowserBackup" in html
    assert "recovery store is protected" in html
    assert "name!=='.workspacebackup'" in html
    assert "recovery_store_preserved:true" in html


def test_mobile_workspace_install_surface_and_pwa_contract():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'id="helperPanel"' in html
    assert '/v1/mcp/workspace/android.apk' in html
    assert 'package=me.ailinux.workspace' in html
    assert 'intent://pair' in html
    assert 'scheme=ailinux-workspace' in html
    assert '/v1/mcp/manifest.webmanifest?v=2902' in html
    assert "/v1/mcp/sw.js?v=2902" in html
    assert "beforeinstallprompt" in html
    assert 'Add to Home Screen' in html
    assert 'native foreground executor' in html


@pytest.mark.asyncio
async def test_workspace_pwa_routes_have_installable_metadata_and_offline_shell():
    from app.routes.mcp import workspace_pwa_manifest, workspace_pwa_service_worker
    manifest_response = await workspace_pwa_manifest()
    manifest = __import__('json').loads(manifest_response.body)
    assert manifest['name'] == 'AILinux Helper'
    assert manifest['start_url'] == '/v1/mcp'
    assert manifest['display'] == 'standalone'
    worker = await workspace_pwa_service_worker()
    assert b"ailinux-helper-v2902" in worker.body
    assert b"caches.match('/v1/mcp?app=2.90.2')" in worker.body
    assert b'ailinux-helper-v2902' in worker.body
    assert b'caches.delete' in worker.body



def test_helper_surface_is_unified_and_branded():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert '<h1>AILinux Helper</h1>' in html
    assert 'AILinux Helper 2.90.2' in html
    assert 'Mobile Workspace' not in html
    assert '/v1/mcp/helper/icon.png?v=2902' in html
