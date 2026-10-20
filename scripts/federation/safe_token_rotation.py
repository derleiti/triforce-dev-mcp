#!/usr/bin/env python3
"""Fail-closed TriForce federation credential deployment.

Default / scheduled invocation is read-only. A specific node requires
explicit --apply --node. Never place credentials in argv, URLs or logs.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config/triforce.env"
VAULT = REPO / ".vault/federation_tokens.json"
VAULT_LOCK = REPO / ".vault/.federation_tokens.lock"
ROTATE_LOCK = REPO / ".vault/.safe_federation_rotation.lock"
ENV_PATH = "/home/zombie/workspace/triforce/config/triforce.env"
HUB_HEALTH = "http://10.10.0.1:9100/health"
NODES = {"zombie-pc": ("10.10.0.2", "http://10.10.0.2:9100/health"),
         "backup": ("10.10.0.3", "http://10.10.0.3:9100/health")}
CERTIFIED_KEYS = ("FEDERATION_SECRET", "FEDERATION_TOKEN", "NODE_ID")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./=:+~-]{30,256}$")


class RotationError(RuntimeError):
    pass


def log(message: str) -> None:
    # Only constant or operational metadata; never put tokens into messages.
    print("[federation-rotation] " + message, flush=True)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_env(content: str) -> dict[str, str]:
    result = {}
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[7:].strip()
        result[key] = value.strip().strip("\"'")
    return result


def patch_env(content: str, replacements: dict[str, str]) -> str:
    """Preserve unrelated lines exactly and reject duplicate target definitions."""
    updated, seen = [], set()
    for line in content.splitlines(keepends=True):
        if not line.lstrip().startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in replacements:
                if key in seen:
                    raise RotationError("Duplicate federation config key: " + key)
                seen.add(key)
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                updated.append(key + "=" + replacements[key] + newline)
                continue
        updated.append(line)
    if updated and not updated[-1].endswith("\n"):
        updated[-1] += "\n"
    for key in CERTIFIED_KEYS:
        if key not in seen:
            updated.append(key + "=" + replacements[key] + "\n")
    return "".join(updated)


REMOTE_HELPER = r'''
import hashlib,json,os,pathlib,sys,tempfile
path=pathlib.Path("/home/zombie/workspace/triforce/config/triforce.env")
def digest(v): return hashlib.sha256(v.encode()).hexdigest()
def require(condition,message):
    if not condition: raise RuntimeError(message)
def parse(data):
    d={}
    for ln in data.splitlines():
        if "=" in ln and not ln.lstrip().startswith("#"):
            k,v=ln.split("=",1);d[k.strip()]=v.strip().strip("\"'")
    return d
def patch(src,updates):
    seen=set();out=[]
    for line in src.splitlines(keepends=True):
        if "=" in line and not line.lstrip().startswith("#"):
            key=line.split("=",1)[0].strip()
            if key in updates:
                if key in seen:raise ValueError("duplicate key")
                seen.add(key)
                out.append(key+"="+updates[key]+("\r\n" if line.endswith("\r\n") else "\n"))
                continue
        out.append(line)
    if out and not out[-1].endswith("\n"):out[-1]+="\n"
    for k in ("FEDERATION_SECRET","FEDERATION_TOKEN","NODE_ID"):
        if k not in seen:out.append(k+"="+updates[k]+"\n")
    return "".join(out)
def atomic_write(p,contents,st):
    fd,tmp=tempfile.mkstemp(prefix=".federation-",dir=str(p.parent))
    try:
        os.fchmod(fd,0o600)
        os.fchown(fd,st.st_uid,st.st_gid)
        with os.fdopen(fd,"wb") as f:
            f.write(contents);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
        dfd=os.open(str(p.parent),os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)
def main():
    data=json.load(sys.stdin)
    mode=data.get("mode")
    require(mode in ("check","stage","restore"),"unknown operation")
    require(path.is_file() and not path.is_symlink(),"invalid config path")
    st=path.stat()
    require(st.st_mode & 0o077 == 0, "unsafe config permissions")
    existing=path.read_bytes()
    cfg=parse(existing.decode("utf-8"))
    if mode=="check":
        print(json.dumps({"ok":True,"file_digest":digest(existing.decode("utf-8")),
          "secret_digest":digest(cfg.get("FEDERATION_SECRET","")) if cfg.get("FEDERATION_SECRET") else None,
          "token_digest":digest(cfg.get("FEDERATION_TOKEN","")) if cfg.get("FEDERATION_TOKEN") else None,
          "jwt_present":bool(cfg.get("JWT_SECRET")),"node_id":cfg.get("NODE_ID")}))
        return
    txn=data["txn"]
    require(len(txn)==16 and all(c in "0123456789abcdef" for c in txn),"invalid transaction ID")
    backup=path.with_name(path.name+".pre-federation-"+txn)
    if mode=="stage":
        require(not backup.exists(),"backup already exists")
        require(digest(existing.decode("utf-8"))==data["expected_file_digest"],"concurrent config change")
        require(cfg.get("NODE_ID") in (None,"",data["node"]),"wrong target node")
        vals={k:data[k] for k in ("FEDERATION_SECRET","FEDERATION_TOKEN")}
        for value in vals.values():
            require(value and len(value)>=30 and "\n" not in value and "\r" not in value,"invalid credential")
        vals["NODE_ID"]=data["node"]
        fd=os.open(str(backup),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,"wb") as f:
                f.write(existing);f.flush();os.fsync(f.fileno())
            atomic_write(path,patch(existing.decode("utf-8"),vals).encode("utf-8"),st)
        except BaseException:
            if backup.exists() and digest(path.read_bytes().decode("utf-8"))==data["expected_file_digest"]:
                backup.unlink()
            raise
        now=path.read_text()
        print(json.dumps({"ok":True,"backup_created":True,"written_digest":digest(now),
           "secret_matches":digest(parse(now)["FEDERATION_SECRET"])==digest(vals["FEDERATION_SECRET"]),
           "token_matches":digest(parse(now)["FEDERATION_TOKEN"])==digest(vals["FEDERATION_TOKEN"])}))
    if mode=="restore":
        old_digest=data["expected_old_digest"]
        if not backup.exists():
            require(digest(existing.decode("utf-8"))==old_digest,"missing backup with changed config")
            print(json.dumps({"ok":True,"already_original":True}))
            return
        require(backup.is_file() and not backup.is_symlink(),"invalid rollback backup")
        original=backup.read_bytes()
        require(digest(original.decode("utf-8"))==old_digest,"rollback backup changed")
        updates={"FEDERATION_SECRET":data["FEDERATION_SECRET"],
                 "FEDERATION_TOKEN":data["FEDERATION_TOKEN"],"NODE_ID":data["node"]}
        expected=patch(original.decode("utf-8"),updates)
        if digest(existing.decode("utf-8"))==old_digest:
            print(json.dumps({"ok":True,"already_original":True}))
            return
        require(digest(existing.decode("utf-8"))==digest(expected),"concurrent config change")
        atomic_write(path,original,st)
        print(json.dumps({"ok":True,"restored":True}))
if __name__=="__main__":main()
'''


def ssh_args(node: str) -> list[str]:
    ip, _ = NODES[node]
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
            "-o", "StrictHostKeyChecking=yes", "-o", "NumberOfPasswordPrompts=0",
            "-o", "LogLevel=ERROR", "zombie@" + ip]


def ssh_run(node: str, command: str, stdin: str = "", timeout: int = 25) -> subprocess.CompletedProcess:
    return subprocess.run(ssh_args(node) + [command], input=stdin, text=True,
                          capture_output=True, timeout=timeout, check=False)


def remote(node: str, request: dict) -> dict:
    command = "python3 -I -c " + shlex.quote(REMOTE_HELPER)
    try:
        response = ssh_run(node, command, json.dumps(request), timeout=26)
        if response.returncode != 0:
            raise RotationError("Remote operation failed on " + node)
        doc = json.loads(response.stdout)
        if not doc.get("ok"):
            raise RotationError("Remote operation rejected on " + node)
        return doc
    except (subprocess.TimeoutExpired, ValueError) as exc:
        raise RotationError("Remote operation could not complete on " + node) from None


def preflight_restart_permissions(node: str | None) -> None:
    """Verify unattended restart authorization without restarting services."""
    command = "/usr/bin/systemctl restart triforce.service"
    if node is None:
        outcome = subprocess.run(["sudo", "-n", "-l"] + command.split(),
                                 capture_output=True, text=True, timeout=12)
    else:
        outcome = ssh_run(node, "sudo -n -l " + command, timeout=15)
    if outcome.returncode != 0:
        raise RotationError("Noninteractive restart permission missing on " +
                            (node or "hetzner"))


def restart(node: str | None) -> None:
    if node is None:
        cmd = ["sudo", "-n", "systemctl", "restart", "triforce.service"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    else:
        result = ssh_run(node, "sudo -n systemctl restart triforce.service", timeout=45)
    if result.returncode != 0:
        raise RotationError("TriForce restart failed on " + (node or "hetzner"))


def health(url: str, seconds: int = 55) -> None:
    until = time.monotonic() + seconds
    opener = build_opener(ProxyHandler({}))  # WireGuard checks must bypass HTTP proxies.
    while time.monotonic() < until:
        try:
            with opener.open(url, timeout=4) as reply:
                if reply.status == 200:
                    return
        except (OSError, ValueError):
            pass
        time.sleep(2)
    raise RotationError("Health endpoint did not recover: " + url)


def get_vault() -> dict:
    if not VAULT.is_file() or VAULT.is_symlink():
        raise RotationError("Federation vault missing or invalid")
    return json.loads(VAULT.read_text())


def replace_vault_hash(node: str, expected_old: str, new: str) -> None:
    """Atomic per-node change, preserving unrelated node entries and locking with app."""
    with VAULT_LOCK.open("a+") as lock:
        os.chmod(VAULT_LOCK, 0o600)
        fcntl.flock(lock, fcntl.LOCK_EX)
        content = get_vault()
        candidates = [record for record in content.get("nodes", []) if record.get("node_id") == node]
        if len(candidates) != 1 or candidates[0]["token_hash"] != expected_old:
            raise RotationError("Vault entry changed concurrently: " + node)
        candidates[0]["token_hash"] = new
        content["updated_at"] = datetime.now(timezone.utc).isoformat()
        fd, name = tempfile.mkstemp(prefix=".federation-vault-", dir=str(VAULT.parent))
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(content, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, VAULT)
            directory = os.open(str(VAULT.parent), os.O_DIRECTORY | os.O_RDONLY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)


def keep_vault_backup(txn: str) -> Path:
    backup = VAULT.with_name("federation_tokens.pre-rotation-" + txn + ".json")
    fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(VAULT.read_bytes())
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        backup.unlink(missing_ok=True)
        raise
    return backup


def local_config() -> dict[str, str]:
    if not CONFIG.is_file() or CONFIG.is_symlink():
        raise RotationError("Main triforce.env missing or symlink")
    if CONFIG.stat().st_mode & 0o077:
        raise RotationError("Main triforce.env permissions are too broad")
    d = parse_env(CONFIG.read_text())
    if not SAFE_VALUE.fullmatch(d.get("FEDERATION_SECRET", "")):
        raise RotationError("Invalid/missing hub FEDERATION_SECRET")
    return d


def check_node(node: str, secret: str, vault: dict) -> tuple[dict, str]:
    target = [n for n in vault.get("nodes", []) if n.get("node_id") == node]
    if len(target) != 1 or not target[0].get("active"):
        raise RotationError(node + " is not registered and active in hub vault")
    ip = NODES[node][0]
    if target[0].get("allowed_ips") and ip not in target[0]["allowed_ips"]:
        raise RotationError(node + " VPN address is not allowed by hub vault")
    reply = remote(node, {"mode": "check"})
    match_secret = hmac.compare_digest(reply.get("secret_digest") or "", sha(secret))
    token_match = hmac.compare_digest(reply.get("token_digest") or "", target[0]["token_hash"])
    log(node + ": SSH/config OK, secret=" + ("MATCH" if match_secret else "MISSING/DIFFERENT") +
        ", token=" + ("MATCH" if token_match else "MISSING/DIFFERENT"))
    reply["credentials_match"] = bool(match_secret and token_match)
    return reply, target[0]["token_hash"]


def ensure_external_execution(cgroup_file: Path = Path("/proc/self/cgroup")) -> None:
    """A TriForce service restart kills its child processes (KillMode=control-group).

    Never let a repair launched via the TriForce MCP shell kill its own rollback.
    Run an explicit apply from an independent SSH login or systemd-run unit.
    """
    try:
        memberships = cgroup_file.read_text().splitlines()
    except OSError as exc:
        raise RotationError("Cannot verify execution cgroup") from exc
    if any("/triforce.service" in item for item in memberships):
        raise RotationError(
            "Unsafe apply context: running inside triforce.service. "
            "Use an independent SSH login or a separate systemd unit."
        )


def rotate(node: str, config: dict, vault: dict) -> None:
    ensure_external_execution()
    secret = config["FEDERATION_SECRET"]
    reply, old_hash = check_node(node, secret, vault)
    if reply.get("node_id") not in (None, "", node):
        raise RotationError("Unexpected NODE_ID on " + node)
    # Check health BEFORE modifying anything.
    health(HUB_HEALTH, seconds=5)
    health(NODES[node][1], seconds=5)
    if node == "zombie-pc":
        health(NODES["backup"][1], seconds=5)
    preflight_restart_permissions(node)
    preflight_restart_permissions(None)
    txn = os.urandom(8).hex()
    token = __import__("secrets").token_urlsafe(32)
    new_hash = sha(token)
    backup = keep_vault_backup(txn)
    log("Hub vault backed up (" + backup.name + ")")
    stage_attempted = False
    node_restart_attempted = False
    hub_restart_attempted = False
    try:
        stage_attempted = True
        result = remote(node, {"mode": "stage", "txn": txn, "node": node,
            "expected_file_digest": reply["file_digest"],
            "FEDERATION_SECRET": secret, "FEDERATION_TOKEN": token})
        if not (result.get("secret_matches") and result.get("token_matches")):
            raise RotationError("Remote credential verification failed")
        log(node + ": config staged with 0600 backup")
        replace_vault_hash(node, old_hash, new_hash)
        log(node + ": hub vault entry atomically updated")
        node_restart_attempted = True
        restart(node)
        health(NODES[node][1])
        log(node + ": service healthy after restart")
        # The currently running hub caches vault entries. A hub restart is mandatory.
        hub_restart_attempted = True
        restart(None)
        health(HUB_HEALTH)
        health(NODES["backup"][1])
        log("Hub healthy; backup node still reachable")
        # Validate authentic HMAC HELLOs in both directions, never print tokens.
        verify_federation(node, config)
        log("SUCCESS: both WebSocket directions authenticated for " + node)
    except BaseException:
        log("FAILURE: rolling back " + node + " and the hub vault")
        problems = []
        if stage_attempted:
            try:
                remote(node, {"mode": "restore", "txn": txn, "node": node,
                    "expected_old_digest": reply["file_digest"],
                    "FEDERATION_SECRET": secret, "FEDERATION_TOKEN": token})
            except Exception:
                problems.append("remote config")
        try:
            stored = [item for item in get_vault().get("nodes", [])
                      if item.get("node_id") == node]
            if len(stored) == 1 and stored[0]["token_hash"] == new_hash:
                replace_vault_hash(node, new_hash, old_hash)
            elif len(stored) != 1 or stored[0]["token_hash"] != old_hash:
                problems.append("hub vault conflict")
        except Exception:
            problems.append("hub vault")
        for host, attempted in ((node, node_restart_attempted),
                                (None, hub_restart_attempted)):
            if not attempted:
                continue
            try:
                restart(host)
                health(NODES[host][1] if host else HUB_HEALTH, seconds=35)
            except Exception:
                problems.append((host or "hub") + " restart")
        if problems:
            log("CRITICAL: incomplete rollback (" + ", ".join(problems) +
                "). Inspect backups before further changes.")
        else:
            log("ROLLBACK: previous configuration and services restored")
        raise


PROBE = r'''
import hashlib,hmac,json,pathlib,sys,time
from websockets.sync.client import connect
cfg={}
p=pathlib.Path("/home/zombie/workspace/triforce/config/triforce.env")
for line in p.read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        k,v=line.split("=",1);cfg[k.strip()]=v.strip().strip("\"'")
secret=cfg["FEDERATION_SECRET"]
token=cfg["FEDERATION_TOKEN"]
msg={"type":"hello","node_id":sys.argv[1],"token":token,"capabilities":[]}
ts=str(int(time.time()))
body=ts+":"+json.dumps(msg,sort_keys=True)
sig=hmac.new(secret.encode(),body.encode(),hashlib.sha256).hexdigest()
with connect(sys.argv[2],open_timeout=8,close_timeout=3,proxy=None) as sock:
    sock.send(json.dumps({"data":msg,"timestamp":ts,"signature":sig}))
    reply=json.loads(sock.recv(timeout=8))
payload=reply["data"]
valid=hmac.compare_digest(reply["signature"],hmac.new(secret.encode(),
 (str(reply["timestamp"])+":"+json.dumps(payload,sort_keys=True)).encode(),hashlib.sha256).hexdigest())
if not (valid and abs(int(time.time())-int(reply["timestamp"]))<=30
        and payload.get("type")=="hello_ack"
        and payload.get("node_id")==sys.argv[3]
        and payload.get("status")=="connected"):
    raise SystemExit("HANDSHAKE_INVALID")
print("HANDSHAKE_OK")
'''


def verify_federation(node: str, config: dict) -> None:
    # The PC test uses the node's own new token, without putting it in argv.
    p = ssh_run(node,
        shlex.quote("/home/zombie/workspace/triforce/.venv/bin/python") +
        " -I -c " + shlex.quote(PROBE) + " " + shlex.quote(node) +
        " " + shlex.quote("ws://10.10.0.1:9100/v1/federation/ws") +
        " " + shlex.quote("hetzner"),
        timeout=29)
    if p.returncode != 0 or "HANDSHAKE_OK" not in p.stdout:
        raise RotationError("Node-to-hub WebSocket handshake failed")
    # Hub-to-node handshake is tested with the hub's own token.
    # It is read by the probe from the local config; no credential enters argv.
    caller = subprocess.run([sys.executable, "-I", "-c", PROBE, "hetzner",
                             "ws://" + NODES[node][0] + ":9100/v1/federation/ws", node],
                            cwd=REPO, capture_output=True, text=True, timeout=29)
    if caller.returncode != 0 or "HANDSHAKE_OK" not in caller.stdout:
        raise RotationError("Hub-to-node WebSocket handshake failed")


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Read-only verification (default)")
    modes.add_argument("--apply", action="store_true", help="Rotate one explicit node and verify")
    parser.add_argument("--node", choices=sorted(NODES))
    args = parser.parse_args()
    if args.apply and not args.node:
        parser.error("--apply requires --node, never rotate both implicitly")
    if os.geteuid() == 0:
        raise RotationError("Run as the TriForce user, not root")
    if not ROTATE_LOCK.parent.is_dir():
        raise RotationError("Vault directory missing")
    if ROTATE_LOCK.parent.stat().st_mode & 0o077:
        raise RotationError("Vault directory permissions are too broad")
    if ROTATE_LOCK.is_symlink() or VAULT_LOCK.is_symlink():
        raise RotationError("Unsafe federation lock path")
    with ROTATE_LOCK.open("a+") as lock:
        os.chmod(ROTATE_LOCK, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RotationError("Another rotation is in progress") from None
        if os.uname().nodename != "ailinux":
            raise RotationError("Only run on the Hetzner hub")
        config = local_config()
        vault = get_vault()
        if args.apply:
            log("APPLY requested only for " + args.node)
            rotate(args.node, config, vault)
        else:
            ok = True
            for node in ([args.node] if args.node else sorted(NODES)):
                try:
                    report, _ = check_node(node, config["FEDERATION_SECRET"], vault)
                    if not report["credentials_match"]:
                        ok = False
                except RotationError as e:
                    log("CHECK FAILED for " + node + ": " + str(e))
                    ok = False
            if not ok:
                return 1
            log("CHECK completed (read-only)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RotationError, OSError, subprocess.TimeoutExpired) as ex:
        # These exception paths only include operational metadata, never secrets.
        log("ERROR: " + (str(ex) if isinstance(ex, RotationError) else type(ex).__name__))
        sys.exit(1)
