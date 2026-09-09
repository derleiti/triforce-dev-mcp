#!/opt/triforce/runtime/bin/python
"""Root-owned runner for one fixed TriForce setup task.

Started only by the privileged helper through a transient systemd service. It
never accepts shell commands or arbitrary paths. Progress/status contains no
configuration values or secrets and is safe for the desktop user to read.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
INSTALL_ROOT = Path("/opt/triforce")
HELPER = Path("/usr/lib/triforce/triforce-admin-helper")
STATUS_DIR = Path("/run/triforce-control-center")
STATUS_FILE = STATUS_DIR / "setup-job.json"
LOCK_FILE = Path("/run/triforce-setup-job.lock")
TASK_ACTIONS = {
    "runtime-init": "runtime-init",
    "config-init": "config-init",
    "service-install": "service-install",
    "docker-install": "docker-install",
    "profile-server": "profile-server",
    "profile-node": "profile-node",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def write_status(payload: dict) -> None:
    STATUS_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATUS_DIR, 0o755)
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode()
    fd, name = tempfile.mkstemp(prefix=".setup-job.", dir=STATUS_DIR)
    tmp = Path(name)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data); fh.flush(); os.fsync(fh.fileno())
        os.replace(tmp, STATUS_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("setup runner must run as root")
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=sorted(TASK_ACTIONS))
    task = parser.parse_args().task

    lock_fd = os.open(LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            write_status({"task_id": task, "state": "rejected", "message": "Ein anderer Setup-Auftrag läuft bereits.", "finished_at": now()})
            return 73

        sys.path.insert(0, str(INSTALL_ROOT))
        from app.setup_tasks import get_task, run_check
        dependency_results = [run_check(dep) for dep in get_task(task).dependencies]
        blocked = [result for result in dependency_results if not result.ok]
        if blocked:
            write_status({
                "task_id": task, "state": "rejected", "finished_at": now(),
                "message": "Setup-Abhängigkeiten sind nicht erfüllt.",
                "dependencies": [result.to_dict() for result in dependency_results],
            })
            return 78

        status = {"task_id": task, "state": "running", "started_at": now(), "message": "Setup-Auftrag läuft."}
        write_status(status)
        cp = subprocess.run([str(HELPER), TASK_ACTIONS[task]], text=True, capture_output=True, timeout=900, check=False)

        verification = run_check(task).to_dict()
        ok = cp.returncode == 0 and bool(verification.get("ok"))
        status.update({
            "state": "completed" if ok else "failed",
            "finished_at": now(),
            "exit_code": cp.returncode,
            "message": "Auftrag erfolgreich verifiziert." if ok else "Auftrag oder Ergebnisprüfung fehlgeschlagen.",
            "verification": verification,
        })
        # Never persist helper stdout/stderr: future tasks may handle sensitive systems.
        write_status(status)
        return 0 if ok else (cp.returncode or 4)
    except subprocess.TimeoutExpired:
        write_status({"task_id": task, "state": "failed", "finished_at": now(), "exit_code": 124, "message": "Setup-Auftrag hat das Zeitlimit überschritten."})
        return 124
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
