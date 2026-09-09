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

sys.dont_write_bytecode = True

INSTALL_ROOT = Path("/opt/triforce")
ETC_DIR = Path("/etc/triforce")
CONFIG = ETC_DIR / "triforce.env"
STATE_DIR = Path("/var/lib/triforce")
LOG_DIR = Path("/var/log/triforce")
UNIT_SOURCE = INSTALL_ROOT / "packaging/systemd/triforce.service"
UNIT_DEST = Path("/usr/lib/systemd/system/triforce.service")
SERVICE = "triforce.service"
SETUP_RUNNER = Path("/usr/lib/triforce/triforce-setup-runner")
SETUP_TASKS = frozenset({"runtime-init", "config-init", "service-install", "docker-install", "profile-server", "profile-node", "legacy-import"})


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
    generated = {
        "MCP_OAUTH_PASS": secrets.token_urlsafe(32),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "TRIFORCE_ADMIN_SECRET": secrets.token_urlsafe(32),
        "DOCKER_WORDPRESS_DB_PASSWORD": secrets.token_urlsafe(32),
        "DOCKER_WORDPRESS_DB_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "DOCKER_FLARUM_DB_PASSWORD": secrets.token_urlsafe(32),
        "DOCKER_FLARUM_DB_ROOT_PASSWORD": secrets.token_urlsafe(32),
        "DOCKER_SEARXNG_SECRET": secrets.token_urlsafe(32),
    }
    rendered: list[str] = []
    seen_generated: set[str] = set()
    for line in sanitized:
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in generated:
                rendered.append(f"{key}={generated[key]}")
                seen_generated.add(key)
                continue
        rendered.append(line)
    for key, value in generated.items():
        if key not in seen_generated:
            rendered.append(f"{key}={value}")
    text = "\n".join(rendered).rstrip() + "\n"
    # Local authentication secrets are generated here and never emitted to
    # stdout, package metadata, process arguments, or build logs.
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



def _command_ok(*args: str) -> bool:
    try:
        return subprocess.run(list(args), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker_install() -> None:
    """Install the fixed distro Docker packages only when functionality is missing."""
    docker = shutil.which("docker")
    engine_ok = bool(docker) and _command_ok(docker, "--version")
    compose_ok = bool(docker) and _command_ok(docker, "compose", "version")

    packages: list[str] = []
    if not engine_ok:
        packages.append("docker.io")
    if not compose_ok:
        packages.append("docker-compose-v2")
    if packages:
        subprocess.run(["/usr/bin/apt-get", "update"], check=True, timeout=300)
        subprocess.run(["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *packages], check=True, timeout=900)

    # Starting/enabling Docker is explicit behavior of this setup task. Never
    # grant docker-group membership and never start a Compose profile here.
    run_fixed("/bin/systemctl", "enable", "--now", "docker.service")
    docker = shutil.which("docker")
    if not docker or not _command_ok(docker, "--version") or not _command_ok(docker, "compose", "version"):
        raise SystemExit("Docker Engine oder Docker Compose v2 ist nach der Installation nicht betriebsbereit")

def service_action(action: str) -> None:
    mapping = {
        "service-start": ("start",), "service-stop": ("stop",),
        "service-restart": ("restart",), "service-enable": ("enable",),
        "service-disable": ("disable",),
    }
    run_fixed("/bin/systemctl", *mapping[action], SERVICE)





def legacy_import() -> None:
    config_init()
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.legacy_settings import apply_import
    apply_import(CONFIG)
    os.chmod(CONFIG, 0o640)
    shutil.chown(CONFIG, user="root", group="triforce")

def _set_deployment_mode(mode: str) -> None:
    if mode not in {"server", "node"}:
        raise SystemExit("invalid deployment mode")
    config_init()
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import load_snapshot, save_updates
    snap = load_snapshot(CONFIG)
    save_updates({"TRIFORCE_DEPLOYMENT_MODE": mode}, path=CONFIG, expected_digest=snap.digest, environ={})
    os.chmod(CONFIG, 0o640)
    shutil.chown(CONFIG, user="root", group="triforce")


def deployment_profile(mode: str) -> None:
    """Apply one of the two fixed installation profiles."""
    runtime_init()
    config_init()
    _set_deployment_mode(mode)
    service_install()
    run_fixed("/bin/systemctl", "enable", "--now", SERVICE)
    if mode == "server":
        docker_install()

def setup_start(task: str) -> None:
    if task not in SETUP_TASKS:
        raise SystemExit("invalid setup task")
    if not SETUP_RUNNER.is_file():
        raise SystemExit("packaged setup runner missing")
    # systemd owns the job after this command returns; closing the GUI therefore
    # cannot terminate an in-progress setup operation.
    run_fixed(
        "/usr/bin/systemd-run", "--unit=triforce-setup-job", "--service-type=exec",
        "--property=TimeoutStartSec=15min", "--property=NoNewPrivileges=true",
        str(SETUP_RUNNER), task,
    )



def setup_cancel() -> None:
    """Cancel only the single fixed TriForce setup transient unit."""
    run_fixed("/bin/systemctl", "stop", "triforce-setup-job.service")
    status_file = Path("/run/triforce-control-center/setup-job.json")
    try:
        current = json.loads(status_file.read_text(encoding="utf-8"))
    except Exception:
        current = {}
    if not isinstance(current, dict):
        current = {}
    current.update({"state": "cancelled", "message": "Setup-Auftrag wurde abgebrochen."})
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(status_file, 0o644)

def config_read() -> None:
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import MASKED_SECRET_VALUE, SECRET_ENV_KEYS, load_snapshot, redact_dotenv_text
    snap = load_snapshot(CONFIG)
    values = {k: (MASKED_SECRET_VALUE if k in SECRET_ENV_KEYS and v not in (None, "") else v) for k, v in snap.values.items()}
    payload = {
        "path": str(CONFIG),
        "digest": snap.digest,
        "values": values,
        "raw": redact_dotenv_text(CONFIG.read_text(encoding="utf-8")),
    }
    print(json.dumps(payload, ensure_ascii=False))


def config_raw_update() -> None:
    payload = json.load(sys.stdin)
    if set(payload) != {"expected_digest", "text"} or not isinstance(payload["text"], str):
        raise SystemExit("invalid raw config payload")
    sys.path.insert(0, str(INSTALL_ROOT))
    from app.settings_store import restore_masked_secrets, save_raw_text
    original = CONFIG.read_text(encoding="utf-8")
    restored = restore_masked_secrets(payload["text"], original)
    save_raw_text(restored, path=CONFIG, expected_digest=str(payload["expected_digest"]), environ={})
    os.chmod(CONFIG, 0o640)
    shutil.chown(CONFIG, user="root", group="triforce")

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
        "runtime-init", "config-init", "service-install", "docker-install", "profile-server", "profile-node", "legacy-import",
        "service-start", "service-stop", "service-restart",
        "service-enable", "service-disable", "config-read", "config-update", "config-raw-update", "config-restore",
        "setup-start", "setup-cancel",
    ])
    parser.add_argument("task", nargs="?", choices=sorted(SETUP_TASKS))
    args = parser.parse_args()
    action = args.action
    if action == "setup-start" and args.task is None:
        parser.error("setup-start requires a fixed task id")
    if action != "setup-start" and args.task is not None:
        parser.error("task argument is only valid for setup-start")
    if action == "runtime-init": runtime_init()
    elif action == "config-init": config_init()
    elif action == "service-install": service_install()
    elif action == "docker-install": docker_install()
    elif action == "legacy-import": legacy_import()
    elif action == "profile-server": deployment_profile("server")
    elif action == "profile-node": deployment_profile("node")
    elif action in {"service-start", "service-stop", "service-restart", "service-enable", "service-disable"}: service_action(action)
    elif action == "config-read": config_read()
    elif action == "config-update": config_update()
    elif action == "config-raw-update": config_raw_update()
    elif action == "config-restore": config_restore()
    elif action == "setup-start": setup_start(args.task)
    elif action == "setup-cancel": setup_cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
