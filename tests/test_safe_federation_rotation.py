"""Regression tests for the fail-closed federation rotation workflow."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/federation/safe_token_rotation.py"
spec = importlib.util.spec_from_file_location("safe_token_rotation", SCRIPT)
rotation = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(rotation)


def digest(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def remote_test(program: str, message: dict):
    result = subprocess.run(
        [sys.executable, "-c", program],
        input=json.dumps(message), text=True, capture_output=True, timeout=5
    )
    return result, json.loads(result.stdout) if result.returncode == 0 else None


def test_patch_env_preserves_unrelated_configuration():
    src = "# keep original\nJWT_SECRET=persist\nNODE_ID=zombie-pc\nFEDERATION_TOKEN=old\n"
    result = rotation.patch_env(src, {
        "FEDERATION_SECRET": "a" * 64,
        "FEDERATION_TOKEN": "b" * 43,
        "NODE_ID": "zombie-pc",
    })
    assert result.count("JWT_SECRET=persist") == 1
    assert result.count("NODE_ID=zombie-pc") == 1
    assert "FEDERATION_TOKEN=old" not in result
    assert result.count("FEDERATION_TOKEN=") == 1
    assert "# keep original\n" in result


def test_duplicate_federation_key_rejected():
    src = "FEDERATION_SECRET=x\nFEDERATION_SECRET=y\n"
    with pytest.raises(rotation.RotationError, match="Duplicate"):
        rotation.patch_env(src, {
            "FEDERATION_SECRET": "a" * 64,
            "FEDERATION_TOKEN": "b" * 43,
            "NODE_ID": "zombie-pc"
        })


def test_remote_backup_stage_and_restore_without_secret_output(tmp_path):
    p = tmp_path / "triforce.env"
    original = "# test\nNODE_ID=zombie-pc\nJWT_SECRET=keep-this\n"
    p.write_text(original)
    p.chmod(0o600)
    code = rotation.REMOTE_HELPER.replace(rotation.ENV_PATH, str(p))
    before, data = remote_test(code, {"mode": "check"})
    assert before.returncode == 0
    assert data["jwt_present"]
    secret, token, txn = "a" * 64, "b" * 43, "a" * 16
    payload = {"mode": "stage", "txn": txn, "node": "zombie-pc",
               "expected_file_digest": data["file_digest"],
               "FEDERATION_SECRET": secret, "FEDERATION_TOKEN": token}
    staged, staged_result = remote_test(code, payload)
    assert staged.returncode == 0, staged.stderr
    assert staged_result["secret_matches"] is True
    assert staged_result["token_matches"] is True
    assert "JWT_SECRET=keep-this" in p.read_text()
    assert p.stat().st_mode & 0o777 == 0o600
    assert secret not in staged.stdout and token not in staged.stdout
    backup = tmp_path / ("triforce.env.pre-federation-" + txn)
    assert backup.read_text() == original
    assert backup.stat().st_mode & 0o777 == 0o600
    restored, result = remote_test(code, {
        "mode": "restore", "txn": txn, "node": "zombie-pc",
        "expected_old_digest": data["file_digest"],
        "FEDERATION_SECRET": secret, "FEDERATION_TOKEN": token
    })
    assert restored.returncode == 0, restored.stderr
    assert result["restored"]
    assert p.read_text() == original


def test_remote_refuses_concurrent_config_change(tmp_path):
    p = tmp_path / "triforce.env"
    p.write_text("NODE_ID=zombie-pc\n")
    p.chmod(0o600)
    code = rotation.REMOTE_HELPER.replace(rotation.ENV_PATH, str(p))
    pre, d = remote_test(code, {"mode": "check"})
    assert pre.returncode == 0
    msg = {"mode": "stage", "txn": "b" * 16, "node": "zombie-pc",
           "expected_file_digest": d["file_digest"],
           "FEDERATION_SECRET": "s" * 64, "FEDERATION_TOKEN": "t" * 43}
    staged, _ = remote_test(code, msg)
    assert staged.returncode == 0, staged.stderr
    with p.open("a") as f:
        f.write("ADDED_BY_OTHER_OPERATOR=1\n")
    roll, _ = remote_test(code, {
        "mode": "restore", "txn": "b" * 16, "node": "zombie-pc",
        "expected_old_digest": d["file_digest"],
        "FEDERATION_SECRET": "s" * 64, "FEDERATION_TOKEN": "t" * 43
    })
    assert roll.returncode != 0
    assert "ADDED_BY_OTHER_OPERATOR=1" in p.read_text()


def test_node_id_mismatch_refused_before_write(tmp_path):
    p = tmp_path / "triforce.env"
    p.write_text("NODE_ID=backup\n")
    p.chmod(0o600)
    code = rotation.REMOTE_HELPER.replace(rotation.ENV_PATH, str(p))
    checked, d = remote_test(code, {"mode": "check"})
    assert checked.returncode == 0
    ret, _ = remote_test(code, {
        "mode": "stage", "txn": "c" * 16, "node": "zombie-pc",
        "expected_file_digest": d["file_digest"],
        "FEDERATION_SECRET": "s" * 64, "FEDERATION_TOKEN": "t" * 43
    })
    assert ret.returncode != 0
    assert p.read_text() == "NODE_ID=backup\n"
    assert not list(tmp_path.glob("*.pre-federation-*"))


def test_vault_change_preserves_other_nodes_and_rejects_conflict(tmp_path, monkeypatch):
    vault_path = tmp_path / "federation_tokens.json"
    old = "a" * 64
    initial = {"nodes": [
        {"node_id": "zombie-pc", "token_hash": old, "active": True},
        {"node_id": "backup", "token_hash": "b" * 64, "active": True},
    ]}
    vault_path.write_text(json.dumps(initial))
    monkeypatch.setattr(rotation, "VAULT", vault_path)
    monkeypatch.setattr(rotation, "VAULT_LOCK", tmp_path / ".lock")
    rotation.replace_vault_hash("zombie-pc", old, "c" * 64)
    result = json.loads(vault_path.read_text())
    assert result["nodes"][0]["token_hash"] == "c" * 64
    assert result["nodes"][1]["token_hash"] == "b" * 64
    assert vault_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(rotation.RotationError, match="concurrently"):
        rotation.replace_vault_hash("zombie-pc", old, "d" * 64)
    assert json.loads(vault_path.read_text())["nodes"][0]["token_hash"] == "c" * 64
    rotation.replace_vault_hash("zombie-pc", "c" * 64, old)
    assert json.loads(vault_path.read_text())["nodes"][0]["token_hash"] == old


def test_transport_uses_stdin_not_arguments(monkeypatch):
    secret = "S" * 64
    token = "T" * 43
    seen = {}
    def fake_ssh(node, command, stdin, timeout=25):
        seen.update(node=node, command=command, stdin=stdin)
        return subprocess.CompletedProcess([], 0, '{"ok":true}', "")
    monkeypatch.setattr(rotation, "ssh_run", fake_ssh)
    assert rotation.remote("zombie-pc", {
        "mode": "stage", "node": "zombie-pc",
        "FEDERATION_SECRET": secret, "FEDERATION_TOKEN": token
    })["ok"]
    assert secret not in seen["command"]
    assert token not in seen["command"]
    assert secret in seen["stdin"] and token in seen["stdin"]
    assert "StrictHostKeyChecking=yes" in rotation.ssh_args("zombie-pc")


def test_apply_without_target_is_rejected_before_any_network():
    result = subprocess.run([sys.executable, str(SCRIPT), "--apply"],
                            text=True, capture_output=True, timeout=5)
    assert result.returncode != 0
    assert "--apply requires --node" in result.stderr


def test_partial_failure_restores_node_and_vault(tmp_path, monkeypatch):
    old_hash = "a" * 64
    current = {"nodes": [{"node_id": "zombie-pc", "active": True,
                          "token_hash": old_hash, "allowed_ips": ["10.10.0.2"]}]}
    vault_path = tmp_path / "tokens.json"
    vault_path.write_text(json.dumps(current))
    monkeypatch.setattr(rotation, "VAULT", vault_path)
    monkeypatch.setattr(rotation, "health", lambda *a, **k: None)
    monkeypatch.setattr(rotation, "preflight_restart_permissions", lambda *a, **k: None)
    monkeypatch.setattr(rotation, "ensure_external_execution", lambda *a, **k: None)
    restored = []
    restarts = []
    def fake_remote(node, data):
        if data["mode"] == "check":
            return {"node_id": "zombie-pc", "file_digest": "f" * 64}
        if data["mode"] == "stage":
            return {"written_digest": "z" * 64,
                    "secret_matches": True, "token_matches": True}
        if data["mode"] == "restore":
            restored.append(node)
            return {"restored": True}
        raise AssertionError("Unexpected remote operation")
    def fake_replace(node, expected, new):
        assert current["nodes"][0]["token_hash"] == expected
        current["nodes"][0]["token_hash"] = new
    def fake_restart(node):
        restarts.append(node)
        if node is None and restarts.count(None) == 1:
            raise rotation.RotationError("Injected hub restart failure")
    monkeypatch.setattr(rotation, "remote", fake_remote)
    monkeypatch.setattr(rotation, "replace_vault_hash", fake_replace)
    monkeypatch.setattr(rotation, "get_vault", lambda: current)
    monkeypatch.setattr(rotation, "restart", fake_restart)
    monkeypatch.setattr(rotation, "keep_vault_backup", lambda txn: tmp_path / "backup.json")
    with pytest.raises(rotation.RotationError, match="Injected"):
        rotation.rotate("zombie-pc", {"FEDERATION_SECRET": "s" * 64}, current)
    assert restored == ["zombie-pc"]
    assert current["nodes"][0]["token_hash"] == old_hash
    assert restarts == ["zombie-pc", None, "zombie-pc", None]


@pytest.mark.parametrize("valid_ack", [True, False])
def test_signed_websocket_probe_checks_peer_identity(tmp_path, valid_ack):
    """Protocol-level test: a valid HELLO gets a signed ACK; forged peer is rejected."""
    import hmac
    import threading
    import time
    from websockets.sync.server import serve

    fake_secret = "s" * 64
    fake_token = "t" * 43
    p = tmp_path / "triforce.env"
    p.write_text("FEDERATION_SECRET=" + fake_secret +
                 "\nFEDERATION_TOKEN=" + fake_token + "\n")
    p.chmod(0o600)

    def sign(data):
        timestamp = str(int(time.time()))
        canonical = timestamp + ":" + json.dumps(data, sort_keys=True)
        signature = hmac.new(fake_secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        return {"data": data, "timestamp": timestamp, "signature": signature}

    server_errors = []

    def on_peer(ws):
        try:
            incoming = json.loads(ws.recv(timeout=4))
            body = incoming["data"]
            assert body["node_id"] == "zombie-pc"
            assert body["type"] == "hello"
            assert body["token"] == fake_token
            signed = sign(body)
            assert hmac.compare_digest(signed["signature"], incoming["signature"]) or (
                # The timestamp can change at a second boundary.
                hmac.compare_digest(
                    incoming["signature"],
                    hmac.new(fake_secret.encode(),
                             (incoming["timestamp"] + ":" + json.dumps(body, sort_keys=True)).encode(),
                             hashlib.sha256).hexdigest()
                )
            )
            response = {"type": "hello_ack", "node_id": "hetzner" if valid_ack else "impostor",
                        "status": "connected"}
            ws.send(json.dumps(sign(response)))
        except Exception as err:
            server_errors.append(type(err).__name__)

    script = rotation.PROBE.replace(
        "/home/zombie/workspace/triforce/config/triforce.env", str(p))
    with serve(on_peer, "127.0.0.1", 0) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = "ws://127.0.0.1:" + str(server.socket.getsockname()[1])
        res = subprocess.run(
            [sys.executable, "-I", "-c", script, "zombie-pc", endpoint, "hetzner"],
            capture_output=True, text=True, timeout=8
        )
        server.shutdown()
        worker.join(timeout=2)
    assert not server_errors, server_errors
    if valid_ack:
        assert res.returncode == 0, res.stderr
        assert "HANDSHAKE_OK" in res.stdout
    else:
        assert res.returncode != 0
        assert "HANDSHAKE_OK" not in res.stdout


def test_rollback_remote_operation_is_idempotent(tmp_path):
    p = tmp_path / "triforce.env"
    src = "NODE_ID=zombie-pc\nJWT_SECRET=unchanged\n"
    p.write_text(src)
    p.chmod(0o600)
    code = rotation.REMOTE_HELPER.replace(rotation.ENV_PATH, str(p))
    msg = {"mode": "restore", "txn": "d" * 16, "node": "zombie-pc",
           "expected_old_digest": digest(src),
           "FEDERATION_SECRET": "s" * 64, "FEDERATION_TOKEN": "t" * 43}
    ret, output = remote_test(code, msg)
    assert ret.returncode == 0
    assert output["already_original"] is True
    assert p.read_text() == src


def test_live_apply_refuses_triforce_service_cgroup(tmp_path):
    p = tmp_path / "cgroup"
    p.write_text("0::/system.slice/triforce.service\n")
    with pytest.raises(rotation.RotationError, match="Unsafe apply context"):
        rotation.ensure_external_execution(p)
    p.write_text("0::/user.slice/user-1000.slice/session-25.scope\n")
    rotation.ensure_external_execution(p)


def test_restart_permission_preflight_runs_without_restart(monkeypatch):
    calls = []
    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "allowed", "")
    monkeypatch.setattr(rotation.subprocess, "run", fake_run)
    rotation.preflight_restart_permissions(None)
    assert calls[0][:3] == ["sudo", "-n", "-l"]
    assert calls[0][3:] == ["/usr/bin/systemctl", "restart", "triforce.service"]
