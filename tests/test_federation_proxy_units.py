from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/federation/install-proxy-units.sh"


def render(ip="10.10.0.2", backend="127.0.0.1:9100") -> str:
    return subprocess.check_output(
        [str(SCRIPT), "--print", "--bind-ip", ip, "--backend", backend],
        text=True,
        cwd=ROOT,
    )


def test_socket_waits_for_wireguard_and_network_online():
    text = render()
    assert "Wants=network-online.target wg-quick@wg0.service" in text
    assert "After=network-online.target wg-quick@wg0.service" in text


def test_socket_binds_only_requested_wireguard_address():
    text = render("10.10.0.3")
    assert "ListenStream=10.10.0.3:9100" in text
    assert "ListenStream=0.0.0.0:9100" not in text


def test_backend_target_is_explicit_and_local():
    text = render(backend="172.17.0.1:9000")
    assert "systemd-socket-proxyd 172.17.0.1:9000" in text


def test_rejects_non_wireguard_bind_address():
    cp = subprocess.run(
        [str(SCRIPT), "--print", "--bind-ip", "0.0.0.0"],
        text=True,
        capture_output=True,
        cwd=ROOT,
    )
    assert cp.returncode != 0
