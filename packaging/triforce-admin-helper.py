#!/opt/triforce/runtime/bin/python
"""Narrow privileged helper for TriForce Control Center.

Only fixed actions are accepted. There is no arbitrary command, unit name,
script path, environment override, or user-provided destination path.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import secrets
import subprocess
import sys

INSTALL_ROOT = Path("/opt/triforce")
ETC_DIR = Path("/etc/triforce")
CONFIG = ETC_DIR / "triforce.env"
STATE_DIR = Path("/var/lib/triforce")
LOG_DIR = Path("/var/log/triforce")
UNIT_SOURCE = INSTALL_ROOT / "packaging/systemd/triforce.service"
UNIT_DEST = Path("/etc/systemd/system/triforce.service")
SERVICE = "triforce.service"


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("admin helper must run as root")


def run_fixed(*args: str) -> None:
    subprocess.run(list(args), check=True, timeout=60)


def runtime_init() -> None:
    try:
        pwd.getpwnam("triforce")
    except KeyError:
        run_fixed("/usr/sbin/useradd", "--system", "--home", str(STATE_DIR), "--shell", "/usr/sbin/nologin", "triforce")
    for path, mode in ((STATE_DIR, 0o750), (STATE_DIR / "runtime", 0o750), (STATE_DIR / "crawler_spool", 0o750), (STATE_DIR / "crawler_spool/train", 0o750), (STATE_DIR / "tristar", 0o750), (LOG_DIR, 0o750)):
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, mode)
        shutil.chown(path, user="triforce", group="triforce")
    ETC_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(ETC_DIR, 0o750)
    shutil.chown(ETC_DIR, user="root", group="triforce")
    legacy_tristar = Path("/var/tristar")
    if not legacy_tristar.exists() and not legacy_tristar.is_symlink():
        legacy_tristar.symlink_to(STATE_DIR / "tristar", target_is_directory=True)


def config_init() -> None:
    runtime_init()
    if CONFIG.exists():
        return
    template = INSTALL_ROOT / "config" / "triforce.env.example"
    if not template.is_file():
        template = INSTALL_ROOT / ".env.example"
    if not template.is_file():
        raise SystemExit("packaged config template missing")
    # Example files may intentionally contain recognizable placeholders. Never
    # turn any of them into active credentials on a fresh installation.
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import SECRET_ENV_KEYS

    raw = template.read_text()
    sanitized: list[str] = []
    for line in raw.splitlines():
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in SECRET_ENV_KEYS:
                sanitized.append(f"{key}=")
                continue
        sanitized.append(line)
    text = "\n".join(sanitized).rstrip() + "\n"
    # Local authentication secrets are generated here and never emitted to
    # stdout, package metadata, process arguments, or build logs.
    text += f"MCP_OAUTH_PASS={secrets.token_urlsafe(32)}\n"
    text += f"JWT_SECRET={secrets.token_urlsafe(48)}\n"
    text += f"TRIFORCE_ADMIN_SECRET={secrets.token_urlsafe(32)}\n"
    text += "TRIFORCE_BIND_HOST=127.0.0.1\nTRIFORCE_API_PORT=9100\nCRAWLER_SPOOL_DIR=/var/lib/triforce/crawler_spool\nCRAWLER_TRAIN_DIR=/var/lib/triforce/crawler_spool/train\nMCP_WS_ENABLED=false\nMCP_ALLOW_UNAUTHENTICATED_LOCAL=false\n"
    fd = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    shutil.chown(CONFIG, user="root", group="triforce")


def service_install() -> None:
    if not UNIT_SOURCE.is_file():
        raise SystemExit("packaged service unit missing")
    data = UNIT_SOURCE.read_bytes()
    tmp = UNIT_DEST.with_suffix(".service.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data); fh.flush(); os.fsync(fh.fileno())
    os.replace(tmp, UNIT_DEST)
    run_fixed("/bin/systemctl", "daemon-reload")


def service_action(action: str) -> None:
    mapping = {
        "service-start": ("start",), "service-stop": ("stop",),
        "service-restart": ("restart",), "service-enable": ("enable",),
        "service-disable": ("disable",),
    }
    run_fixed("/bin/systemctl", *mapping[action], SERVICE)


def config_update() -> None:
    # JSON comes through stdin so secrets never appear in process arguments.
    payload = json.load(sys.stdin)
    if set(payload) != {"expected_digest", "updates"} or not isinstance(payload["updates"], dict):
        raise SystemExit("invalid config update payload")
    # Installed helper imports only root-owned packaged code.
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import save_updates
    save_updates(payload["updates"], path=CONFIG, expected_digest=str(payload["expected_digest"]), environ={})
    os.chmod(CONFIG, 0o640)
    shutil.chown(CONFIG, user="root", group="triforce")


def config_restore() -> None:
    payload = json.load(sys.stdin)
    if set(payload) != {"expected_digest"}:
        raise SystemExit("invalid config restore payload")
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import restore_last_good
    restore_last_good(path=CONFIG, expected_digest=str(payload["expected_digest"]), environ={})
    os.chmod(CONFIG, 0o640)
    shutil.chown(CONFIG, user="root", group="triforce")


def main() -> int:
    require_root()
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=[
        "runtime-init", "config-init", "service-install",
        "service-start", "service-stop", "service-restart",
        "service-enable", "service-disable", "config-update", "config-restore",
    ])
    action = parser.parse_args().action
    if action == "runtime-init": runtime_init()
    elif action == "config-init": config_init()
    elif action == "service-install": service_install()
    elif action in {"service-start", "service-stop", "service-restart", "service-enable", "service-disable"}: service_action(action)
    elif action == "config-update": config_update()
    elif action == "config-restore": config_restore()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
