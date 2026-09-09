from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_packaged_service_uses_isolated_runtime_and_var_log():
    text = (ROOT / "packaging/systemd/triforce.service").read_text()
    assert "TRIFORCE_PYTHON=/opt/triforce/runtime/bin/python" in text
    assert "TRIFORCE_LOG_DIR=/var/log/triforce" in text
    assert "ExecStart=/opt/triforce/scripts/start-triforce.sh" in text


def test_redis_is_optional_not_recommended_install():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    assert "Suggests: redis-server" in text
    assert "Recommends: redis-server" not in text


def test_no_online_pip_in_postinst_template():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    postinst = text.split('cat > "$pkg/DEBIAN/postinst"', 1)[1].split("EOF", 2)[1]
    assert "pip install" not in postinst

def test_fresh_package_disables_optional_public_mcp_ws_listener():
    helper = (ROOT / "packaging/triforce-admin-helper.py").read_text()
    assert "MCP_WS_ENABLED=false" in helper
