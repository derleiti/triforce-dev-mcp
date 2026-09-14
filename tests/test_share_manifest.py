"""Tests for the generic share manifest normalizer.

These pin the security-relevant invariants of the AILinux share fabric:
default off, capabilities are authoritative, and no implicit escalation.
"""
from __future__ import annotations

from app.services.share_manifest import (
    RESOURCE_CLIPBOARD,
    RESOURCE_COMPUTE,
    RESOURCE_DEVICE,
    RESOURCE_DISPLAY,
    RESOURCE_MCP,
    RESOURCE_WORKSPACE,
    build_share_manifest,
    bump_revision,
    legacy_workspace_mode,
    legacy_workspace_view,
    manifest_has_grant,
    manifest_resource,
    normalize_lifetime,
    normalize_visibility,
    normalize_workspace_mode,
)

READ_CAPS = ["workspace_info", "file_read", "file_tree", "code_read", "code_tree", "code_search", "code_grep", "file_ops"]
WRITE_CAPS = ["file_edit", "directory_create", "workspace_clear", "code_edit"]


def test_empty_payload_shares_nothing():
    manifest = build_share_manifest({})
    assert [r["type"] for r in manifest["resources"] if r["enabled"]] == []
    assert manifest["grants"] == []
    assert manifest["visibility"] == "private"
    assert manifest["lifetime"] == "session"
    assert manifest["verified"] is False


def test_garbage_payload_is_safe():
    for payload in (None, "nope", 42, [], {"capabilities": "file_edit"}):
        manifest = build_share_manifest(payload)
        assert manifest["grants"] == []
        assert manifest["capabilities"] == []


def test_browser_read_only_workspace():
    manifest = build_share_manifest({"access_mode": "read_only", "capabilities": READ_CAPS, "task": "review"})
    workspace = manifest_resource(manifest, RESOURCE_WORKSPACE)
    assert workspace["enabled"] is True
    assert workspace["mode"] == "read_only"
    assert manifest_has_grant(manifest, RESOURCE_WORKSPACE, "read") is True
    assert manifest_has_grant(manifest, RESOURCE_WORKSPACE, "write") is False
    assert legacy_workspace_view(manifest) == {
        "mode": "read_only",
        "task": "review",
        "capabilities": sorted(READ_CAPS),
    }


def test_write_requires_advertised_write_tools():
    """A client asking for write without write tools stays read-only."""
    manifest = build_share_manifest({"access_mode": "write", "capabilities": READ_CAPS})
    assert manifest_resource(manifest, RESOURCE_WORKSPACE)["mode"] == "read_only"
    assert manifest_has_grant(manifest, RESOURCE_WORKSPACE, "write") is False
    assert legacy_workspace_view(manifest)["mode"] == "read_only"


def test_write_granted_with_write_tools():
    manifest = build_share_manifest({"access_mode": "write", "capabilities": READ_CAPS + WRITE_CAPS})
    assert manifest_resource(manifest, RESOURCE_WORKSPACE)["mode"] == "read_write"
    assert manifest_has_grant(manifest, RESOURCE_WORKSPACE, "write") is True
    # legacy session layer still expects the old wire value
    assert legacy_workspace_view(manifest)["mode"] == "write"


def test_native_only_share_without_workspace():
    manifest = build_share_manifest({
        "access_mode": "off",
        "capabilities": ["clipboard_read", "computer_observe", "compute_execute"],
        "resources": {"native": {
            "compute": {"available": True, "runtime": "docker"},
            "mcp": {"advertise": True},
            "resources": {"advertise": True},
        }},
    })
    assert manifest_resource(manifest, RESOURCE_WORKSPACE)["enabled"] is False
    assert manifest_resource(manifest, RESOURCE_CLIPBOARD) == {
        "type": RESOURCE_CLIPBOARD, "enabled": True, "read": True, "write": False,
    }
    assert manifest_resource(manifest, RESOURCE_COMPUTE)["enabled"] is True
    assert manifest_resource(manifest, RESOURCE_MCP)["enabled"] is True
    assert manifest_resource(manifest, RESOURCE_DEVICE)["enabled"] is True
    assert manifest_has_grant(manifest, RESOURCE_COMPUTE, "execute") is True


def test_display_observe_never_implies_control():
    manifest = build_share_manifest({"capabilities": ["computer_observe"]})
    display = manifest_resource(manifest, RESOURCE_DISPLAY)
    assert display["observe"] is True
    assert display["control"] is False
    assert manifest_has_grant(manifest, RESOURCE_DISPLAY, "control") is False


def test_native_profile_alone_cannot_enable_compute():
    """Advertising docker in the native profile is not a capability grant."""
    manifest = build_share_manifest({
        "capabilities": [],
        "resources": {"native": {"compute": {"advertise": True, "available": True}}},
    })
    assert manifest_resource(manifest, RESOURCE_COMPUTE)["enabled"] is False
    assert manifest_has_grant(manifest, RESOURCE_COMPUTE, "execute") is False


def test_public_visibility_does_not_grant_write_or_execute():
    manifest = build_share_manifest(
        {"access_mode": "read_only", "capabilities": READ_CAPS},
        visibility="public",
    )
    assert manifest["visibility"] == "public"
    assert manifest_has_grant(manifest, RESOURCE_WORKSPACE, "write") is False
    assert manifest_has_grant(manifest, RESOURCE_COMPUTE, "execute") is False


def test_mcp_discover_never_implies_invoke():
    manifest = build_share_manifest({"resources": {"native": {"mcp": {"advertise": True}}}})
    mcp = manifest_resource(manifest, RESOURCE_MCP)
    assert mcp["discover"] is True
    assert mcp["invoke"] is False


def test_normalizers():
    assert normalize_visibility("PUBLIC") == "public"
    assert normalize_visibility("link") == "unlisted"
    assert normalize_visibility("weird") == "private"
    assert normalize_visibility(None) == "private"
    assert normalize_lifetime("persistent") == "persistent"
    assert normalize_lifetime("forever") == "session"
    assert normalize_workspace_mode("rw") == "read_write"
    assert normalize_workspace_mode("off") == "off"
    assert normalize_workspace_mode(None) == "read_only"
    assert legacy_workspace_mode("read_write") == "write"
    assert legacy_workspace_mode("off") == "read_only"


def test_bump_revision_is_non_destructive():
    manifest = build_share_manifest({"capabilities": READ_CAPS})
    assert manifest["revision"] == 1
    updated = bump_revision(manifest)
    assert updated["revision"] == 2
    assert manifest["revision"] == 1
    assert updated["capabilities"] == manifest["capabilities"]


def test_generic_shell_never_implies_compute_execute_grant():
    manifest = build_share_manifest({
        "capabilities": ["shell"],
        "resources": {"native": {"compute": {"advertise": True, "available": True}}},
    })
    assert manifest_resource(manifest, RESOURCE_COMPUTE)["enabled"] is False
    assert manifest_has_grant(manifest, RESOURCE_COMPUTE, "execute") is False



def test_compute_metadata_is_normalized_only_with_explicit_compute_capability():
    native_compute = {
        "advertise": True,
        "available": True,
        "runtime": "docker",
        "resource_id": "docker-zombie-pc",
        "node_id": "zombie-pc",
        "cpu_cores": 2,
        "memory_mb": 2048,
        "gpu": "",
        "vram_mb": 0,
        "models": ["qwen3"],
        "capabilities": ["container"],
        "max_concurrent": 1,
        "load": 0.25,
        "healthy": True,
    }
    enabled = build_share_manifest({
        "capabilities": ["compute_execute"],
        "resources": {"native": {"compute": native_compute}},
    })
    compute = manifest_resource(enabled, RESOURCE_COMPUTE)
    assert compute["enabled"] is True
    assert compute["resource_id"] == "docker-zombie-pc"
    assert compute["node_id"] == "zombie-pc"
    assert compute["cpu_cores"] == 2
    assert compute["memory_mb"] == 2048
    assert compute["models"] == ["qwen3"]
    assert compute["capabilities"] == ["container"]
    assert compute["max_concurrent"] == 1
    assert compute["load"] == 0.25
    assert compute["healthy"] is True

    disabled = build_share_manifest({
        "capabilities": [],
        "resources": {"native": {"compute": native_compute}},
    })
    hidden = manifest_resource(disabled, RESOURCE_COMPUTE)
    assert hidden["enabled"] is False
    assert hidden["available"] is False
    assert "node_id" not in hidden
    assert "resource_id" not in hidden
    assert "models" not in hidden


def test_compute_metadata_is_bounded_before_entering_manifest():
    manifest = build_share_manifest({
        "capabilities": ["compute_execute"],
        "resources": {"native": {"compute": {
            "available": True,
            "cpu_cores": 999999,
            "memory_mb": -50,
            "vram_mb": -1,
            "max_concurrent": 99999,
            "load": 99,
            "models": ["x" * 500] * 100,
            "capabilities": ["container"] * 100,
        }}},
    })
    compute = manifest_resource(manifest, RESOURCE_COMPUTE)
    assert compute["cpu_cores"] == 4096.0
    assert compute["memory_mb"] == 1
    assert compute["vram_mb"] == 0
    assert compute["max_concurrent"] == 1024
    assert compute["load"] == 1.0
    assert len(compute["models"]) <= 32
    assert all(len(item) <= 128 for item in compute["models"])


def test_portable_device_read_and_control_are_separate_grants():
    read_only = build_share_manifest({"capabilities": ["device_info", "process_ops", "service_ops"]})
    assert manifest_has_grant(read_only, RESOURCE_DEVICE, "read") is True
    assert manifest_has_grant(read_only, RESOURCE_DEVICE, "control") is False

    controlled = build_share_manifest({"access_mode": "write", "capabilities": ["computer_input", "window_ops", "app_ops"]})
    assert manifest_has_grant(controlled, RESOURCE_DISPLAY, "control") is True
    assert manifest_has_grant(controlled, RESOURCE_DEVICE, "control") is True


def test_display_observe_and_control_are_independent():
    control_only = build_share_manifest({"access_mode": "write", "capabilities": ["computer_input"]})
    display = manifest_resource(control_only, RESOURCE_DISPLAY)
    assert display["enabled"] is True
    assert display["observe"] is False
    assert display["control"] is True
