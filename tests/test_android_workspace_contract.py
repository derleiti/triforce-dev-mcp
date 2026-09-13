from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android_workspace"

def text(rel): return (ANDROID / rel).read_text(encoding="utf-8")

def test_android_manifest_foreground_executor_and_app_links():
    manifest=text("app/src/main/AndroidManifest.xml")
    assert 'android.permission.FOREGROUND_SERVICE_SPECIAL_USE' in manifest
    assert 'android:foregroundServiceType="specialUse"' in manifest
    assert 'android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE' in manifest
    assert 'android:scheme="ailinux-workspace"' in manifest
    assert 'android:host="pair"' in manifest
    assert 'android:host="handoff"' in manifest
    assert 'android:host="api.ailinux.me"' in manifest
    assert 'android:pathPrefix="/v1/mcp"' in manifest

def test_android_uses_saf_persisted_tree_and_not_broad_storage_permission():
    activity=text("app/src/main/java/me/ailinux/workspace/MainActivity.java")
    manifest=text("app/src/main/AndroidManifest.xml")
    assert 'Intent.ACTION_OPEN_DOCUMENT_TREE' in activity
    assert 'takePersistableUriPermission' in activity
    assert 'MANAGE_EXTERNAL_STORAGE' not in manifest
    assert 'READ_EXTERNAL_STORAGE' not in manifest

def test_native_executor_uses_existing_workspace_protocol():
    protocol=text("app/src/main/java/me/ailinux/workspace/ProtocolClient.java")
    for token in ['/v1/mcp/node/connect','workspace/share','client_workspace_tool','workspace/tool_stage','/v1/mcp/workspace/resume-ticket','/v1/mcp/workspace/pair-ticket','handoff_code','2.86.7-android']:
        assert token in protocol
    assert 'createPairTicket()' in protocol
    assert 'listener.onPairCode(code)' in protocol
    assert 'Waiting for AI pairing' in protocol
    assert 'wss://api.ailinux.me' in protocol

def test_native_workspace_has_read_write_and_path_traversal_guards():
    saf=text("app/src/main/java/me/ailinux/workspace/SafWorkspace.java")
    protocol=text("app/src/main/java/me/ailinux/workspace/ProtocolClient.java")
    for tool in ['workspace_info','workspace_clear','code_search']:
        assert tool in protocol
    for implementation in ['codeEdit','fileOps']:
        assert implementation in saf
    assert 'path traversal rejected' in saf
    assert 'file exceeds 2 MiB text limit' in saf
