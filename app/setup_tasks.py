"""Shared, declarative setup task catalog for CLI and Control Center.

The catalog contains only fixed operations. It intentionally does not expose an
arbitrary script path or shell command. Mutating system operations are delegated
to the packaged admin helper and require explicit authorization.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from typing import Callable


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    ok: bool
    status: str
    message: str
    details: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SetupTask:
    task_id: str
    title: str
    description: str
    mutating: bool
    requires_admin: bool
    dependencies: tuple[str, ...]
    changes: tuple[str, ...]
    repeat_behavior: str
    checker: Callable[[], TaskResult]
    helper_action: str | None = None

    def plan(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "title": self.title,
            "description": self.description,
            "mutating": self.mutating,
            "requires_admin": self.requires_admin,
            "dependencies": list(self.dependencies),
            "changes": list(self.changes),
            "repeat_behavior": self.repeat_behavior,
            "helper_action": self.helper_action,
        }


def _command(args: list[str], timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)


def check_system() -> TaskResult:
    problems: list[str] = []
    if sys.version_info < (3, 11):
        problems.append(f"Python {sys.version_info.major}.{sys.version_info.minor} < 3.11")
    if not shutil.which("systemctl"):
        problems.append("systemctl fehlt")
    # The Debian package deliberately ships an isolated Python runtime. Do not
    # require an unrelated global `python3` shim when this checker itself is
    # already executing on a supported interpreter.
    if not Path(sys.executable).is_file():
        problems.append("aktive Python-Laufzeit fehlt")
    free = shutil.disk_usage("/").free
    if free < 1_000_000_000:
        problems.append("weniger als 1 GB freier Speicher")
    return TaskResult("system-check", not problems, "ready" if not problems else "blocked",
                      "Systemvoraussetzungen erfüllt" if not problems else "; ".join(problems),
                      {"python": sys.version.split()[0], "free_bytes": free})


def check_runtime() -> TaskResult:
    root = Path(os.environ.get("TRIFORCE_INSTALL_ROOT", "/opt/triforce"))
    runtime = Path(os.environ.get("TRIFORCE_RUNTIME_DIR", "/var/lib/triforce/runtime"))
    ok = root.is_dir() and runtime.is_dir()
    return TaskResult("runtime-init", ok, "installed" if ok else "missing",
                      "Runtime-Verzeichnisse vorhanden" if ok else "Runtime muss eingerichtet werden",
                      {"program_root": str(root), "runtime_dir": str(runtime)})


def check_config() -> TaskResult:
    from .settings_store import load_snapshot, resolve_config_path
    path = resolve_config_path()
    snap = load_snapshot(path)
    ok = path.is_file()
    return TaskResult("config-init", ok, "configured" if ok else "missing",
                      "Konfigurationsdatei vorhanden" if ok else "Konfiguration muss initialisiert werden",
                      {"path": str(path), "digest": snap.digest if ok else None})


def check_service() -> TaskResult:
    if not shutil.which("systemctl"):
        return TaskResult("service-install", False, "blocked", "systemctl fehlt", {})
    cp = _command(["systemctl", "show", "triforce.service", "--property=LoadState,ActiveState,UnitFileState", "--no-pager"])
    loaded = cp.returncode == 0 and "LoadState=loaded" in cp.stdout
    return TaskResult("service-install", loaded, "installed" if loaded else "missing",
                      "TriForce-Unit installiert" if loaded else "TriForce-Unit fehlt",
                      {"systemctl": cp.stdout.strip()})


def check_redis() -> TaskResult:
    try:
        with socket.create_connection(("127.0.0.1", 6379), timeout=0.5):
            pass
        ok = True
    except OSError:
        ok = False
    return TaskResult("redis-check", ok, "reachable" if ok else "unavailable",
                      "Redis ist lokal erreichbar" if ok else "Redis ist lokal nicht erreichbar",
                      {"host": "127.0.0.1", "port": 6379})


def check_memory_worker() -> TaskResult:
    binary = shutil.which("claude-mem")
    return TaskResult("memory-worker", bool(binary), "available" if binary else "optional-missing",
                      "Claude-Mem Worker gefunden" if binary else "Optionaler Claude-Mem Worker nicht gefunden",
                      {"binary": binary})


def check_agents() -> TaskResult:
    agents = {name: shutil.which(name) for name in ("claude", "codex", "gemini", "opencode")}
    return TaskResult("agents-check", True, "checked", "CLI-Agenten geprüft", agents)


def diagnose() -> TaskResult:
    checks = [check_system(), check_runtime(), check_config(), check_service(), check_redis(), check_memory_worker(), check_agents()]
    # Optional components do not make the base installation unhealthy.
    required = [x for x in checks if x.task_id in {"system-check", "runtime-init", "config-init", "service-install"}]
    return TaskResult("diagnose", all(x.ok for x in required), "healthy" if all(x.ok for x in required) else "attention",
                      "Installationsdiagnose abgeschlossen", {x.task_id: x.to_dict() for x in checks})


TASKS: dict[str, SetupTask] = {
    "system-check": SetupTask("system-check", "System prüfen", "Prüft Laufzeit, systemd und Speicherplatz.", False, False, (), (), "Nur lesend.", check_system),
    "runtime-init": SetupTask("runtime-init", "TriForce-Laufzeit einrichten", "Legt feste Programm-/Datenverzeichnisse und den Dienstbenutzer an.", True, True, ("system-check",), ("Benutzer triforce", "/var/lib/triforce", "/var/log/triforce"), "Idempotent; vorhandene passende Objekte bleiben erhalten.", check_runtime, "runtime-init"),
    "config-init": SetupTask("config-init", "Konfiguration initialisieren", "Erstellt /etc/triforce/triforce.env nur wenn sie fehlt.", True, True, ("runtime-init",), ("/etc/triforce/triforce.env"), "Vorhandene Konfiguration und Secrets werden nicht überschrieben.", check_config, "config-init"),
    "service-install": SetupTask("service-install", "Dienst installieren/reparieren", "Installiert ausschließlich die paketierte triforce.service-Unit; startet oder aktiviert sie nicht automatisch.", True, True, ("runtime-init", "config-init"), ("/etc/systemd/system/triforce.service", "systemctl daemon-reload"), "Unit kann sicher erneut installiert werden; Enable/Start bleiben separat.", check_service, "service-install"),
    "redis-check": SetupTask("redis-check", "Redis prüfen", "Prüft lokalen Redis ohne ihn automatisch zu installieren.", False, False, (), (), "Nur lesend.", check_redis),
    "memory-worker": SetupTask("memory-worker", "Memory-Worker prüfen", "Prüft optionalen Claude-Mem Worker; keine automatische Aktivierung von Recall/Recording/Promotion.", False, False, (), (), "Nur lesend; Memory-Flags bleiben unverändert.", check_memory_worker),
    "agents-check": SetupTask("agents-check", "CLI-Agenten prüfen", "Prüft Claude, Codex, Gemini und OpenCode ohne Installationen zu verändern.", False, False, (), (), "Nur lesend; keine Deinstallation oder globale Bereinigung.", check_agents),
    "diagnose": SetupTask("diagnose", "Installation diagnostizieren", "Führt die sicheren Basisprüfungen zusammen.", False, False, (), (), "Nur lesend.", diagnose),
}


def get_task(task_id: str) -> SetupTask:
    try:
        return TASKS[task_id]
    except KeyError as exc:
        raise KeyError(f"unknown setup task: {task_id}") from exc


def run_check(task_id: str) -> TaskResult:
    return get_task(task_id).checker()


def catalog_json() -> str:
    return json.dumps([task.plan() for task in TASKS.values()], ensure_ascii=False, indent=2)
