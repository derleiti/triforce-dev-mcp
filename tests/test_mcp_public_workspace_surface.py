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


def test_core_profile_contains_workspace_bootstrap_without_overlay():
    """Pairing discovery must survive workspace-overlay/cache timing failures."""
    from app.mcp.tool_registry_unified import get_canonical_all_tools, filter_tools_for_profile

    names = {tool['name'] for tool in filter_tools_for_profile(get_canonical_all_tools(), 'core')}
    assert {'aihelper_pair'} <= names


@pytest.mark.asyncio
async def test_public_guest_defaults_local_capabilities_off_until_share_exists():
    result = await handle_tools_list({}, request=FakeRequest('public_guest', False))
    by_name = {tool['name']: tool for tool in result['tools']}
    names = set(by_name)

    # Global AI discovery plus AILinux Helper pairing/control bootstrap remains available.
    assert {'chat', 'search', 'models', 'memory_search', 'aihelper_pair'} <= names
    assert {'agent_start', 'memory_clear', 'service_control', 'group_chat_create'}.isdisjoint(names)

    # Workspace/file resources stay hidden until explicitly shared.
    assert {'shell', 'git', 'file_ops', 'code_edit', 'code_search', 'code_tree',
            'workspace_info', 'file_read', 'file_tree', 'file_edit',
            'directory_create', 'workspace_clear'}.isdisjoint(names)

    # Static connectors discover canonical Helper vision/input schemas before pairing,
    # but execution remains locked until a share lease exists.
    for name in {'aihelper_observe', 'aihelper_screenshot', 'aihelper_input'}:
        assert name in names
        assert by_name[name]['x_execution'] == 'local_workspace'
        assert by_name[name]['x_requires_workspace'] is True

    # Shared-service administration is intentionally absent from Local MCP.
    assert not any(name.startswith('mail_') for name in names)
    assert not any(name.startswith('wp_') for name in names)
    assert not any(name.startswith('flarum_') for name in names)

    # Bootstrap tools remain canonical local contracts; server tools do not
    # silently become host-local execution.
    from app.mcp.tool_registry_unified import get_canonical_all_tools
    canonical = {tool['name']: tool for tool in get_canonical_all_tools()}
    assert by_name['aihelper_pair']['inputSchema'] == canonical['aihelper_pair']['inputSchema']
    assert by_name['aihelper_pair']['x_execution'] == 'local_workspace'
    assert by_name['models']['x_execution'] == 'triforce_server'


@pytest.mark.asyncio
async def test_internal_admin_catalog_keeps_server_tools_but_local_overlay_defaults_off():
    core = await handle_tools_list({}, request=FakeRequest('bearer', True))
    core_names = {tool['name'] for tool in core['tools']}
    assert {'chat', 'aihelper_pair', 'git', 'code_edit'} <= core_names
    assert 'shell' not in core_names
    full = await handle_tools_list({'inventory': 'all'}, request=FakeRequest('bearer', True))
    full_names = {tool['name'] for tool in full['tools']}
    assert {'shell', 'group_chat_create', 'mail_inbox', 'aihelper_pair'} <= full_names


def test_browser_workspace_page_prefers_native_pair_handover_over_the_url():
    """P0: the pair code must not depend on a URL query to reach the page.

    A query parameter is already in the request target by the time the page
    runs, so it has passed through Apache and Cloudflare access logs and sits in
    the Referer of every subresource. Native Helpers therefore hand the code
    over through the preload contract. The URL branch survives only as a
    deprecated fallback for shipped Helper builds <= 2.90.29 and must keep
    stripping itself from history.
    """
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()

    # Out-of-band handover exists, is awaited before the restore path runs, and
    # is one-shot on the Electron side.
    assert 'const nativePairReady=' in html
    assert 'window.ailinuxHelper?.consumePairCode?.()' in html
    assert 'async function restoreSavedWorkspace(){await nativePairReady;' in html

    # The deprecated URL fallback must never be the only source, and must never
    # be written back into a URL.
    assert 'history.replaceState' in html
    assert "location.pathname" in html
    for leak in ("searchParams.set('pair_code'", "?pair_code='+", '?pair_code="+'):
        assert leak not in html


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
    assert 'const nativeTools=await effectiveHelperTools();' in html
    assert 'if(!rootHandle&&!rootEntry&&!nativeTools.length)return;' in html
    assert 'function browserHelperTools()' in html
    assert 'function effectiveHelperTools()' in html
    assert "capabilities=[...(rootHandle||rootEntry?READ_TOOLS:[]),...(workspaceMode==='write'&&rootHandle?WRITE_TOOLS:[]),...nativeTools];renderShareSummary();" in html
    assert "pairing code|workspace credential|resume token" in html
    assert 'Workspace pairing expired. Creating a fresh pairing ID' not in html
    assert 'Pairing ID invalid or expired. The ID was kept; generate a new ID explicitly before retrying.' in html
    error_branch = html.split("if(msg.error&&/(pairing code|workspace credential|resume token)/i.test(String(msg.error)))",1)[1].split("if(msg.method==='connected')",1)[0]
    assert "sessionStorage.removeItem('tf_pair_code')" not in error_branch
    assert "setTimeout(()=>connect(),250)" not in error_branch
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
    assert 'Your local workspace stays on this device' in html
    assert "let lastStatusText=''" in html
    assert 'aria-live="polite"' in html
    assert 'id="helperTitle"' in html
    assert 'id="terminalBackend"' in html
    assert 'native.setShellBackend' in html
    assert "$('terminalBackend').onchange" in html
    # Share fabric: the capability panel and the legacy terminal panel coexist.
    assert 'id="nativeSharePanel"' in html
    assert 'id="shareCompute"' in html
    assert 'id="shareResources"' in html
    assert 'Choose capabilities' in html
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
    assert 'The browser never receives docker.sock' in html
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
    assert "typeof nativeHelper.runCompute==='function'" in html
    assert "TriForce remote compute must be intercepted server-side" in html
    assert 'if(native)return result(await runNativeShell(args))' in html
    assert "execCommand('copy')" in html
    assert "Copy failed: " in html
    assert '/v1/mcp/helper/android' in html
    assert "Android APK · v" in html
    assert "/v1/mcp/helper/android?v=" in html
    assert '/v1/mcp/helper/icon.png' in html
    assert '/v1/mcp/helper/linux-appimage' in html
    assert '/v1/mcp/helper/linux-deb' in html
    assert '/v1/mcp/helper/windows' in html
    assert '/v1/mcp/helper/macos' in html
    assert '/v1/mcp/helper/releases' in html
    assert 'id="advancedToggle"' in html
    assert 'advanced-hidden' in html
    assert 'Latest desktop v' in html
    assert "function detectedHelperOs()" in html
    assert "return 'unknown'" in html
    assert "unknown:HELPER_DOWNLOAD_IDS" in html
    assert "showHelperDownloads(HELPER_DOWNLOAD_IDS)" in html
    assert "System could not be identified reliably" in html
    assert "Available packages shown for all supported desktop/mobile systems" in html
    assert "mode:workspaceMode==='write'?'readwrite':'read'" in html
    assert 'Your local workspace stays on this device' in html
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
    assert "EXECUTOR_VERSION='2.90.29-browser'" in html
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



def test_browser_helper_uses_runtime_capabilities_when_the_browser_exposes_them():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert "navigator.mediaDevices.getDisplayMedia" in html
    assert "browserHelperTools()" in html
    assert "function browserHelperTools(){if(nativeHelper)return []" in html
    assert "browserClipboardRead()" in html
    assert "browserClipboardWrite" in html
    assert "browserDeviceInfo()" in html
    assert "source:'browser-getDisplayMedia'" in html
    assert "source:'browser-clipboard'" in html
    assert "effectiveShareProfile()" in html
    assert "display:{observe:s.display&&webShareProfile.screenObserve===true,control:false}" in html
    assert "device:{observe:false,control:false}" in html
    assert "runtime:'triforce_docker'" in html
    assert "webShareProfile.remoteCompute" in html
    assert "OS input injection is not exposed by this browser" in html


def test_browser_python_runtime_is_isolated_and_does_not_impersonate_docker_compute():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'id="webRuntimePanel"' in html
    assert 'Pyodide 314.0.6' in html
    assert "new Worker(url)" in html
    assert "cdn.jsdelivr.net/pyodide/v314.0.6/full/" in html
    assert "runPythonAsync" in html
    assert "TriForce remote compute must be intercepted server-side" in html
    assert "runtime:'triforce_docker'" in html
    assert "browser Python is local to the page" not in html


def test_browser_remote_compute_and_opfs_workspace_are_explicit_capabilities():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert 'TriForce cloud sandbox · internet + shell + ~/workspace' in html
    assert "if(webShareProfile.remoteCompute&&s.remoteCompute)out.push('compute_execute')" in html
    assert "remote_requested:remote" in html
    assert "internet:'public_only'" in html
    assert "workspace_path:'~/workspace'" in html
    assert 'id="opfsBtn"' in html
    assert 'navigator.storage.getDirectory()' in html
    assert "workspaceBackend='opfs'" in html
    assert "access:workspaceBackend==='opfs'?'opfs'" in html
    assert 'Browser hardware counters are not advertised as compute capacity' in html


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
    assert '/v1/mcp/manifest.webmanifest?v=29029' in html
    assert "/v1/mcp/sw.js?v=29029" in html
    assert "beforeinstallprompt" in html
    assert 'Add to Home Screen' in html
    assert 'native APK selected for foreground workspace' in html


@pytest.mark.asyncio
async def test_workspace_pwa_routes_have_installable_metadata_and_offline_shell():
    from app.routes.mcp import workspace_pwa_manifest, workspace_pwa_service_worker
    manifest_response = await workspace_pwa_manifest()
    manifest = __import__('json').loads(manifest_response.body)
    assert manifest['name'] == 'AILinux Helper'
    assert manifest['start_url'] == '/v1/mcp'
    assert manifest['display'] == 'standalone'
    worker = await workspace_pwa_service_worker()
    assert b"ailinux-helper-v29029" in worker.body
    assert b"caches.match('/v1/mcp?app=2.90.29')" in worker.body
    assert b'ailinux-helper-v29029' in worker.body
    assert b'caches.delete' in worker.body



def test_helper_surface_is_unified_and_branded():
    from app.routes.mcp import _workspace_setup_html
    html = _workspace_setup_html()
    assert '<h1>AILinux Helper</h1>' in html
    assert 'id="helperTitle"' in html
    assert 'Mobile Workspace' not in html
    assert '/v1/mcp/helper/icon.png' in html


def test_helper_release_catalog_selects_newest_per_platform(tmp_path, monkeypatch):
    from app.routes.mcp import _helper_release_catalog

    for name in [
        "AILinux-Helper-2.90.24-android.apk",
        "AILinux-Helper-2.90.25-linux-amd64.deb",
        "AILinux-Helper-2.90.25-linux-x86_64.AppImage",
        "AILinux-Helper-2.90.25-win-x64.exe",
        "AILinux-Helper-2.90.25-mac-arm64.dmg",
    ]:
        (tmp_path / name).write_bytes(b"artifact")
    monkeypatch.setenv("AILINUX_HELPER_RELEASES", str(tmp_path))
    catalog = _helper_release_catalog()
    assert catalog["latest_version"] == "2.90.25"
    assert catalog["android"]["version"] == "2.90.24"
    assert catalog["linux-deb"]["version"] == "2.90.25"
    assert catalog["windows"]["filename"].endswith("win-x64.exe")



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

    assert {'aihelper_pair', 'file_read', 'file_tree', 'aihelper_observe'} <= names
    assert by_name['file_read']['x_execution'] == 'local_workspace'
    assert by_name['aihelper_observe']['x_execution'] == 'local_workspace'
    # Announced capability alone cannot bypass the read-only workspace grant.
    assert 'file_edit' not in names
    assert {'aihelper_clipboard_read', 'shell'}.isdisjoint(names)
    assert 'aihelper_screenshot' in names
    assert by_name['aihelper_screenshot']['x_requires_workspace'] is True


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

    assert {'aihelper_compute_execute', 'aihelper_observe', 'aihelper_clipboard_read'} <= names
    assert by_name['aihelper_compute_execute']['x_execution'] == 'local_workspace'
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
    assert {'directory_create', 'workspace_clear', 'aihelper_clipboard_write'}.isdisjoint(names)


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


def test_linux_helper_download_metadata_uses_dynamic_release_catalog(tmp_path, monkeypatch):
    from app.routes.mcp import _helper_release_catalog

    for name in [
        "AILinux-Helper-2.90.24-android.apk",
        "AILinux-Helper-2.90.25-linux-x86_64.AppImage",
        "AILinux-Helper-2.90.25-linux-amd64.deb",
        "AILinux-Helper-2.90.25-win-x64.exe",
        "AILinux-Helper-2.90.25-mac-arm64.dmg",
    ]:
        (tmp_path / name).write_bytes(b"x")
    monkeypatch.setenv("AILINUX_HELPER_RELEASES", str(tmp_path))
    catalog = _helper_release_catalog()
    assert catalog["linux-appimage"]["filename"] == "AILinux-Helper-2.90.25-linux-x86_64.AppImage"
    assert catalog["linux-deb"]["filename"] == "AILinux-Helper-2.90.25-linux-amd64.deb"
    assert catalog["android"]["version"] == "2.90.24"
    assert catalog["latest_version"] == "2.90.25"



def test_helper_download_routes_support_head_for_health_checks():
    from app.routes.mcp import public_router

    methods_by_path = {}
    for route in public_router.routes:
        methods_by_path.setdefault(route.path, set()).update(route.methods or set())

    assert {"GET", "HEAD"} <= methods_by_path["/mcp/helper/{platform}"]
    assert {"GET", "HEAD"} <= methods_by_path["/mcp/workspace/android.apk"]

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
    assert names == {"status"}
    assert payload["selected_inventory"] == "debug"
    # Generic read-only status stays global; debug/log/analytics remain TriForce administration.


def test_public_semantic_inventory_hints_match_effective_read_policy():
    import asyncio
    from types import SimpleNamespace
    from app.routes.mcp import handle_tools_list

    request = SimpleNamespace(
        state=SimpleNamespace(mcp_auth_method="public_guest", mcp_auth_user=None),
        client=SimpleNamespace(host="127.0.0.1"),
        headers={}, cookies={}, query_params={},
    )
    payload = asyncio.run(handle_tools_list({"inventory": "vision"}, request=request))
    by_name = {tool["name"]: tool for tool in payload["tools"]}
    assert by_name["aihelper_observe"]["annotations"]["readOnlyHint"] is True
    assert "read-only" in by_name["aihelper_observe"]["x_usage_hint"]
    assert by_name["aihelper_screenshot"]["x_scope"] == "aihelper"


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
    for name in ("aihelper_pair", "file_read", "file_edit", "code_edit", "workspace_clear", "aihelper_compute_execute", "aihelper_observe", "aihelper_screenshot", "aihelper_input"):
        assert name in by_name
    assert by_name["file_edit"]["x_requires_workspace"] is True

    denied = asyncio.run(call_public_local_tool(
        request, "file_edit",
        {"workspace_context": "unpaired-chat", "path": "probe.txt", "operation": "create", "content": "x"},
    ))
    assert denied["structuredContent"]["code"] == "WORKSPACE_REQUIRED"


@pytest.mark.asyncio
async def test_paired_device_schema_stays_discoverable_when_runtime_capability_temporarily_drops(monkeypatch):
    from app.services import mcp_workspace_bridge as bridge

    binding = {
        'mode': 'write',
        'capabilities': ['app_ops', 'computer_input'],
        'connection': None,
    }
    monkeypatch.setattr(bridge, 'get_workspace_lease', lambda session_id: binding)

    result = await handle_tools_list({}, request=FakeRequest('public_guest', False, 'paired-device'))
    by_name = {tool['name']: tool for tool in result['tools']}

    # Runtime-sensitive schemas must remain stable even when Android temporarily
    # drops screen capture/accessibility capabilities. Execution still enforces
    # the live capability/grant later in call_public_local_tool().
    for name in {
        'aihelper_app_ops', 'aihelper_input', 'aihelper_observe', 'aihelper_screenshot',
        'aihelper_vision_start', 'aihelper_vision_status', 'aihelper_vision_observe', 'aihelper_vision_stop',
    }:
        assert name in by_name
        assert by_name[name]['x_execution'] == 'local_workspace'
        if name not in {'aihelper_app_ops', 'aihelper_input'}:
            assert by_name[name]['x_requires_workspace'] is True


def test_shared_reflection_protocol_reaches_mcp_and_tristar_model_init():
    from app.mcp.agent_instructions import build_mcp_instructions
    from app.services.tristar.model_init import ModelCapability, ModelConfig, ModelInitService, ModelRole

    mcp_prompt = build_mcp_instructions()
    cfg = ModelConfig(
        model_id='prompt-audit', model_name='prompt-audit', provider='test',
        role=ModelRole.WORKER, capabilities={ModelCapability.CODE},
    )
    model_prompt = ModelInitService()._generate_system_prompt(cfg)
    for prompt in (mcp_prompt, model_prompt):
        assert 'PASS 1 — GROUND + REALITY CHECK' in prompt
        assert 'PASS 2 — DIVERGE + CHALLENGE' in prompt
        assert 'REALITY GATE' in prompt
        assert 'What would prove me wrong?' in prompt
    assert 'transport_state=online' in mcp_prompt
    assert 'AccessibilityService' in mcp_prompt
    assert 'Pair codes are short-lived bootstrap credentials' in mcp_prompt
    assert 'observe -> semantic target/invoke -> observe' in mcp_prompt


@pytest.mark.asyncio
async def test_legacy_triforce_init_inherits_shared_reflection_protocol():
    from app.routes.triforce import InitRequest, triforce_init

    payload = await triforce_init(InitRequest(request='systemprompt', llm_id='prompt-audit'))
    assert 'PASS 1 — GROUND + REALITY CHECK' in payload['systemprompt']
    assert 'PASS 2 — DIVERGE + CHALLENGE' in payload['systemprompt']
