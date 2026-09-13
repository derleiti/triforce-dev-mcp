from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_recovery_is_independent_from_triforce_python_runtime():
    listener = (ROOT / "packaging/triforce-recovery-listener.py").read_text()
    assert "from app" not in listener
    assert "import app" not in listener
    assert "/usr/bin/systemctl" in listener
    assert "triforce-source-recovery.service" in listener


def test_recovery_has_fixed_authenticated_operation_only():
    listener = (ROOT / "packaging/triforce-recovery-listener.py").read_text()
    assert 'self.path != "/recover"' in listener
    assert 'Authorization' in listener
    assert 'hmac.compare_digest' in listener
    assert 'shell=True' not in listener
    assert 'subprocess.Popen' not in listener


def test_recovery_script_requires_explicit_source_mode_marker():
    script = (ROOT / "scripts/triforce-source-recover.sh").read_text()
    assert '/etc/triforce/source-mode.enabled' in script
    assert 'install-service.sh' in script
    assert '--mode source' in script
    assert 'git reset' not in script
    assert 'apt ' not in script


def test_webhook_orders_after_wireguard_and_binds_private_ip():
    unit = (ROOT / "packaging/systemd/triforce-recovery-webhook.service").read_text()
    assert 'After=network-online.target wg-quick@wg0.service' in unit
    assert 'Wants=network-online.target wg-quick@wg0.service' in unit
    assert 'wg-quick@wg0.service' in unit
    assert '0.0.0.0' not in unit
    listener = (ROOT / 'packaging/triforce-recovery-listener.py').read_text()
    assert '["/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", "wg0"]' in listener
    assert 'wildcard recovery bind is forbidden' in listener
    assert 'ThreadingHTTPServer' not in listener
    assert 'HTTPServer((_listen_host(), PORT), Handler)' in listener
    assert 'IPAddressDeny=any' in unit
    assert 'IPAddressAllow=10.10.0.0/24' in unit
    assert 'CapabilityBoundingSet=' in unit
    assert 'RestrictAddressFamilies=AF_UNIX AF_INET AF_NETLINK' in unit
