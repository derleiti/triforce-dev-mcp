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
async def test_public_guest_defaults_local_capabilities_off_until_share_exists():
    result = await handle_tools_list({}, request=FakeRequest('public_guest', False))
    by_name = {tool['name']: tool for tool in result['tools']}
    names = set(by_name)

    # Safe/cloud discovery plus pairing bootstrap remains available.
    assert {'search', 'models', 'agent_start', 'memory_clear', 'service_control',
            'workspace_status', 'workspace_pair'} <= names

    # No local resource is advertised before the client explicitly shared it.
    assert {'shell', 'git', 'file_ops', 'code_edit', 'code_search', 'code_tree',
            'workspace_info', 'file_read', 'file_tree', 'file_edit',
            'directory_create', 'workspace_clear', 'computer_observe',
            'computer_screenshot', 'clipboard_read', 'clipboard_write'}.isdisjoint(names)

    # Shared-service administration is intentionally absent from Local MCP.
    assert not any(name.startswith('mail_') for name in names)
    assert not any(name.startswith('wp_') for name in names)
    assert not any(name.startswith('flarum_') for name in names)

    # Bootstrap tools remain canonical local contracts; server tools do not
    # silently become host-local execution.
    from app.mcp.tool_registry_unified import get_canonical_all_tools
    canonical = {tool['name']: tool for tool in get_canonical_all_tools()}
    assert by_name['workspace_pair']['inputSchema'] == canonical['workspace_pair']['inputSchema']
    assert by_name['workspace_pair']['x_execution'] == 'local_workspace'
    assert by_name['models']['x_execution'] == 'triforce_server'
    assert by_name['service_control']['x_execution'] == 'triforce_server'
    assert by_name['service_control']['x_requires_admin'] is True


@pytest.mark.asyncio
async def test_internal_admin_catalog_keeps_server_tools_but_local_overlay_defaults_off():
    result = await handle_tools_list({}, request=FakeRequest('bearer', True))
    names = {tool['name'] for tool in result['tools']}
    assert {'workspace_status', 'workspace_pair', 'shell', 'git', 'code_edit'} <= names
    assert {'workspace_info', 'file_read', 'file_tree', 'file_edit'}.isdisjoint(names)


def test_browser_workspace_page_contains_direct_folder_runtime_without_helper_uri():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'showDirectoryPicker' in html
    assert 'heartbeatTimer=null' in html
    assert "new URLSearchParams(location.search).get('pair_code')" in html
    assert 'history.replaceState' in html
    assert 'manualDisconnect||(!resumeToken&&!pairCode)||reconnectTimer||!navigator.onLine' in html
    # Share fabric: a resume no longer requires a workspace. A native-only
    # capability share (clipboard/display/compute) must resume as well.
    assert 'manualDisconnect||(!resumeToken&&!pairCode)||!navigator.onLine' in html
    assert 'if(!rootHandle&&!rootEntry&&!(await nativeHelperTools()).length)return;' in html
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
    assert 'AILinux Helper 2.90.15' in html
    assert 'id="terminalBackend"' in html
    assert 'native.setShellBackend' in html
    assert "$('terminalBackend').onchange" in html
    # Share fabric: the capability panel and the legacy terminal panel coexist.
    assert 'id="nativeSharePanel"' in html
    assert 'id="shareCompute"' in html
    assert 'id="shareResources"' in html
    assert 'Choose device capabilities' in html
    # P5: WebApp binds only to the typed native Helper broker for Docker/service control.
    assert 'id="dockerPanel"' in html
    assert 'id="dockerInstallBtn"' in html
    assert 'id="dockerStartBtn"' in html
    assert 'id="dockerStopBtn"' in html
    assert 'id="dockerRestartBtn"' in html
    assert 'id="dockerTestBtn"' in html
    assert "nativeHelper.dockerStatus()" in html
    assert "nativeHelper.dockerService(action)" in html
    assert "nativeHelper.dockerInstall()" in html
    assert "nativeHelper.dockerTest()" in html
    assert 'docker.sock' in html
    assert 'page never talks to docker.sock or systemd directly' in html
    assert 'id="shareBuilderPanel"' in html
    assert 'id="shareVisibility"' in html
    assert '<option value="private" selected>Private</option>' in html
    assert '<option value="unlisted">Unlisted</option>' in html
    assert '<option value="public">Public</option>' in html
    assert 'id="activeGrants"' in html
    assert 'id="revokeShareBtn"' in html
    assert "visibility:$('shareVisibility').value" in html
    assert 'async function revokeAllShare()' in html
    assert 'id="serviceControlPanel"' in html
    assert 'id="serviceList"' in html
    assert 'id="serviceRefreshBtn"' in html
    assert "nativeHelper.serviceList()" in html
    assert "nativeHelper.serviceAction(id,action)" in html
    assert "for(const action of ['start','stop','restart'])" in html
    assert 'There is no free-form root shell' in html
    # The Android download is an ordinary href; stale element IDs must not abort
    # the page script before P5 button handlers are registered.
    assert "$('downloadApkBtn')" not in html
    assert "$('mobileNote')" not in html
    # Compute prefers the hardened helper broker but keeps the native fallback.
    assert "tool==='compute_execute'" in html
    assert "typeof nativeHelper.runCompute!=='function'" in html
    assert 'if(native)return result(await runNativeShell(args))' in html
    assert "execCommand('copy')" in html
    assert "Copy failed: " in html
    assert '/v1/mcp/helper/android' in html
    assert '/v1/mcp/helper/icon.png?v=29015' in html
    assert '/v1/mcp/helper/linux-appimage' in html
    assert '/v1/mcp/helper/linux-deb' in html
    assert '/v1/mcp/helper/windows' in html
    assert '/v1/mcp/helper/macos' in html
    assert "function detectedHelperOs()" in html
    assert "return 'unknown'" in html
    assert "unknown:HELPER_DOWNLOAD_IDS" in html
    assert "showHelperDownloads(downloads[os]||HELPER_DOWNLOAD_IDS)" in html
    assert "Operating system not recognized reliably" in html
    assert "Android APK, Linux AppImage/.deb, Windows and macOS downloads are all available" in html
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
    assert "EXECUTOR_VERSION='2.90.15-browser'" in html
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
    assert '/v1/mcp/manifest.webmanifest?v=29015' in html
    assert "/v1/mcp/sw.js?v=29015" in html
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
    assert b"ailinux-helper-v29015" in worker.body
    assert b"caches.match('/v1/mcp?app=2.90.15')" in worker.body
    assert b'ailinux-helper-v29015' in worker.body
    assert b'caches.delete' in worker.body



def test_helper_surface_is_unified_and_branded():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert '<h1>AILinux Helper</h1>' in html
    assert 'AILinux Helper 2.90.15' in html
    assert 'Mobile Workspace' not in html
    assert '/v1/mcp/helper/icon.png?v=29015' in html


@pytest.mark.asyncio
async def test_paired_read_only_discovery_intersects_capabilities_and_grants(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge

    binding = {
        'mode': 'read_only',
        'capabilities': ['file_read', 'file_tree', 'file_edit', 'computer_observe'],
        'connection': None,
    }
    monkeypatch.setattr(bridge, 'get_workspace_lease', lambda session_id: binding)

    result = await handle_tools_list({}, request=FakeRequest('public_guest', False, 'paired-read'))
    by_name = {tool['name']: tool for tool in result['tools']}
    names = set(by_name)

    assert {'workspace_status', 'workspace_pair', 'file_read', 'file_tree', 'computer_observe'} <= names
    assert by_name['file_read']['x_execution'] == 'local_workspace'
    assert by_name['computer_observe']['x_execution'] == 'local_workspace'
    # Announced capability alone cannot bypass the read-only workspace grant.
    assert 'file_edit' not in names
    assert {'computer_screenshot', 'clipboard_read', 'shell'}.isdisjoint(names)


@pytest.mark.asyncio
async def test_native_only_discovery_needs_no_workspace_grant(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge

    binding = {
        'mode': 'read_only',
        'capabilities': ['compute_execute', 'computer_observe', 'clipboard_read'],
        'connection': None,
    }
    monkeypatch.setattr(bridge, 'get_workspace_lease', lambda session_id: binding)

    result = await handle_tools_list({}, request=FakeRequest('public_guest', False, 'native-only'))
    by_name = {tool['name']: tool for tool in result['tools']}
    names = set(by_name)

    assert {'compute_execute', 'computer_observe', 'clipboard_read'} <= names
    assert by_name['compute_execute']['x_execution'] == 'local_workspace'
    assert {'workspace_info', 'file_read', 'file_edit'}.isdisjoint(names)


@pytest.mark.asyncio
async def test_write_workspace_discovery_exposes_only_announced_write_tools(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge

    binding = {
        'mode': 'write',
        'capabilities': ['file_read', 'file_edit'],
        'connection': None,
    }
    monkeypatch.setattr(bridge, 'get_workspace_lease', lambda session_id: binding)

    result = await handle_tools_list({}, request=FakeRequest('public_guest', False, 'paired-write'))
    names = {tool['name'] for tool in result['tools']}
    assert {'file_read', 'file_edit'} <= names
    assert {'directory_create', 'workspace_clear', 'clipboard_write'}.isdisjoint(names)


@pytest.mark.asyncio
async def test_pair_ticket_returns_no_store_qr_for_exact_one_time_code():
    import json
    from starlette.requests import Request
    from app.routes.mcp import create_browser_workspace_pair_ticket
    from app.services.mcp_workspace_sessions import resolve_web_pair_code

    request = Request({
        "type": "http",
        "method": "POST",
        "scheme": "https",
        "server": ("api.ailinux.me", 443),
        "path": "/v1/mcp/workspace/pair-ticket",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"api.ailinux.me")],
    })
    response = await create_browser_workspace_pair_ticket(request)
    payload = json.loads(response.body)
    code = payload["pair_code"]

    assert resolve_web_pair_code(code) is not None
    assert payload["pair_uri"].startswith("ailinux-helper://pair?")
    assert f"pair_code={code}" in payload["pair_uri"]
    assert "url=https%3A%2F%2Fapi.ailinux.me%2Fv1%2Fmcp" in payload["pair_uri"]
    assert payload["qr_data_uri"].startswith("data:image/svg+xml;base64,")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_linux_helper_download_metadata_tracks_published_29015_build():
    import inspect
    from app.routes.mcp import download_ailinux_helper, download_desktop_workspace_helper

    public_source = inspect.getsource(download_ailinux_helper)
    desktop_source = inspect.getsource(download_desktop_workspace_helper)
    for source in (public_source, desktop_source):
        assert "AILinux-Helper-2.90.15-linux-x86_64.AppImage" in source
        assert "AILinux-Helper-2.90.15-linux-amd64.deb" in source


def test_public_tools_list_semantic_inventory_never_reexpands_to_full_catalog():
    import asyncio
    from types import SimpleNamespace
    from app.routes.mcp import handle_tools_list

    request = SimpleNamespace(
        state=SimpleNamespace(mcp_auth_method="public_guest", mcp_auth_user=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={}, cookies={}, query_params={},
    )
    payload = asyncio.run(handle_tools_list({"inventory": "debug"}, request=request))
    names = {tool["name"] for tool in payload["tools"]}
    assert names == {"status", "debug", "log_viewer", "mcp_analytics"}
    assert payload["selected_inventory"] == "debug"


def test_public_semantic_inventory_hints_match_effective_read_policy():
    import asyncio
    from types import SimpleNamespace
    from app.routes.mcp import handle_tools_list

    request = SimpleNamespace(
        state=SimpleNamespace(mcp_auth_method="public_guest", mcp_auth_user=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={}, cookies={}, query_params={},
    )
    payload = asyncio.run(handle_tools_list({"inventory": "debug"}, request=request))
    by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert by_name["status"]["annotations"]["readOnlyHint"] is True
    assert "read-only" in by_name["status"]["x_usage_hint"]
    assert by_name["log_viewer"]["annotations"]["readOnlyHint"] is True


def test_authenticated_bridge_discovers_workspace_schemas_before_pairing_without_granting_execution():
    import asyncio
    from types import SimpleNamespace
    from app.routes.mcp import handle_tools_list
    from app.services.mcp_workspace_bridge import call_public_local_tool

    request = SimpleNamespace(
        state=SimpleNamespace(
            mcp_auth_method="bearer",
            mcp_auth_user="nova-telegram-bridge",
            mcp_auth_client_id="nova-telegram-bridge-mcp",
            mcp_workspace_subject="telegram-bridge-subject-test",
            mcp_session_id="transport-discovery-test",
        ),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={}, cookies={}, query_params={},
    )
    payload = asyncio.run(handle_tools_list({}, request=request))
    by_name = {tool["name"]: tool for tool in payload["tools"]}
    for name in ("workspace_status", "file_read", "file_edit", "code_edit", "workspace_clear", "compute_execute", "computer_observe", "computer_screenshot", "computer_input"):
        assert name in by_name
    assert by_name["file_edit"]["x_requires_workspace"] is True

    denied = asyncio.run(call_public_local_tool(
        request, "file_edit",
        {"workspace_context": "unpaired-chat", "path": "probe.txt", "operation": "create", "content": "x"},
    ))
    assert denied["structuredContent"]["code"] == "WORKSPACE_REQUIRED"
