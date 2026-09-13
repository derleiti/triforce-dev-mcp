"""Platform-aware shell backends for the TriForce / AILinux Loom workspace executor.

One canonical contract for every helper target. A backend turns a command into
an argv that runs against the shared workspace directory; Android via Termux,
Windows via PowerShell, Linux via bubblewrap or the native console, macOS via
zsh/bash and a Docker environment are backends -- not separate code paths and
not separate tool definitions.

Two independent gates decide whether the ``shell`` capability is offered at all:

1. availability -- does this machine actually provide the backend?
2. release     -- did the user explicitly hand the terminal to the AI?

Unsandboxed backends are never released implicitly. ``TRIFORCE_SHELL_RELEASE``
accepts ``auto`` (default: sandboxed backends only), ``off``, ``on`` (release
whatever is available) or an explicit backend id. ``TRIFORCE_SHELL_BACKEND``
pins one backend and disables auto-detection.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Backends that confine the command to the workspace by construction. Only
# these may be released without an explicit opt-in from the user.
SANDBOXED_BACKENDS = frozenset({"bubblewrap", "docker"})

ENV_BACKEND = "TRIFORCE_SHELL_BACKEND"
ENV_RELEASE = "TRIFORCE_SHELL_RELEASE"
ENV_DOCKER_IMAGE = "TRIFORCE_SHELL_DOCKER_IMAGE"
ENV_DOCKER_NETWORK = "TRIFORCE_SHELL_DOCKER_NETWORK"

TERMUX_PREFIX = Path("/data/data/com.termux/files/usr")


class ShellUnavailable(RuntimeError):
    """No shell backend is available, or the terminal was not released."""


@dataclass(frozen=True)
class ShellPlan:
    backend: str
    argv: list[str]
    sandboxed: bool
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def is_termux() -> bool:
    """Android/Termux, detected without assuming the app is the caller."""
    if _env("TERMUX_VERSION"):
        return True
    if "com.termux" in _env("PREFIX"):
        return True
    return (TERMUX_PREFIX / "bin").is_dir()


def is_android() -> bool:
    if is_termux():
        return True
    return "android" in platform.platform().lower() or Path("/system/build.prop").exists()


def _which(*names: str) -> str:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return ""


def _termux_bin(name: str) -> str:
    candidate = TERMUX_PREFIX / "bin" / name
    return str(candidate) if candidate.exists() else ""


def _docker_image() -> str:
    return _env(ENV_DOCKER_IMAGE)


def detect_backend() -> tuple[str, str]:
    """Return ``(backend_id, reason)``; backend_id is empty when unavailable."""
    pinned = _env(ENV_BACKEND).lower()
    if pinned:
        if pinned not in BACKEND_LABELS:
            return "", f"unknown backend pinned via {ENV_BACKEND}: {pinned}"
        ok, reason = _backend_available(pinned)
        return (pinned, reason) if ok else ("", reason)

    order: list[str] = []
    if _docker_image():
        order.append("docker")
    if is_android():
        order.append("termux")
    elif sys.platform.startswith("win"):
        order += ["powershell", "cmd"]
    elif sys.platform == "darwin":
        order.append("macos")
    else:
        order += ["bubblewrap", "linux"]

    last = "no shell backend available on this platform"
    for candidate in order:
        ok, reason = _backend_available(candidate)
        if ok:
            return candidate, reason
        last = reason
    return "", last


def _backend_available(backend: str) -> tuple[bool, str]:
    if backend == "docker":
        if not _docker_image():
            return False, f"docker backend requires {ENV_DOCKER_IMAGE}"
        if not _which("docker"):
            return False, "docker executable not found"
        return True, f"docker image {_docker_image()}"
    if backend == "bubblewrap":
        if not _which("bwrap"):
            return False, "bubblewrap (bwrap) not installed"
        return True, "bubblewrap sandbox"
    if backend == "termux":
        if _termux_bin("bash") or _termux_bin("sh"):
            return True, "termux shell"
        if Path("/system/bin/sh").exists():
            return True, "android system shell (termux not installed)"
        return False, "no Termux prefix and no /system/bin/sh"
    if backend == "powershell":
        found = _which("pwsh", "powershell", "powershell.exe")
        return (True, f"powershell at {found}") if found else (False, "powershell not found")
    if backend == "cmd":
        found = _env("COMSPEC") or _which("cmd.exe")
        return (True, f"cmd at {found}") if found else (False, "cmd.exe not found")
    if backend == "macos":
        found = _which("zsh", "bash", "sh")
        return (True, f"macOS shell at {found}") if found else (False, "no zsh/bash/sh found")
    if backend == "linux":
        found = _which("bash", "sh")
        return (True, f"native shell at {found}") if found else (False, "no bash/sh found")
    return False, f"unknown backend: {backend}"


BACKEND_LABELS = {
    "bubblewrap": "Linux console (bubblewrap sandbox)",
    "linux": "Linux console (native)",
    "termux": "Android (Termux)",
    "powershell": "Windows PowerShell",
    "cmd": "Windows cmd.exe",
    "macos": "macOS console",
    "docker": "Docker environment",
}


def release_mode() -> str:
    value = _env(ENV_RELEASE, "auto").lower()
    return value if value in {"auto", "on", "off"} or value in BACKEND_LABELS else "auto"


def is_released(backend: str) -> tuple[bool, str]:
    """Has the user handed this backend's terminal to the AI?"""
    if not backend:
        return False, "no backend"
    mode = release_mode()
    if mode == "off":
        return False, f"terminal access disabled ({ENV_RELEASE}=off)"
    if mode == "on":
        return True, "released for all available backends"
    if mode in BACKEND_LABELS:
        if mode == backend:
            return True, f"released explicitly for {backend}"
        return False, f"{ENV_RELEASE} released '{mode}', not '{backend}'"
    # auto
    if backend in SANDBOXED_BACKENDS:
        return True, "sandboxed backend, released automatically"
    return False, (
        f"'{backend}' runs unsandboxed; set {ENV_RELEASE}=on or {ENV_RELEASE}={backend} "
        "to hand the terminal to the AI"
    )


def shell_backend_status(root: Path | None = None) -> dict[str, object]:
    """Machine-readable state for workspace_info, the helper UI and logs."""
    backend, reason = detect_backend()
    released, release_reason = (is_released(backend) if backend else (False, reason))
    return {
        "backend": backend,
        "label": BACKEND_LABELS.get(backend, ""),
        "available": bool(backend),
        "sandboxed": backend in SANDBOXED_BACKENDS,
        "released": bool(backend) and released,
        "release_mode": release_mode(),
        "detail": reason,
        "release_detail": release_reason,
        "platform": "android" if is_android() else sys.platform,
        "workspace": str(root) if root else "",
    }


def _posix_argv(shell: str, command: str) -> list[str]:
    # pipefail is a bash/zsh feature; plain sh (dash) would abort on it.
    name = Path(shell).name
    if name in {"bash", "zsh"}:
        return [shell, "-o", "pipefail", "-c", command]
    return [shell, "-c", command]


def build_shell_plan(command: str, *, root: Path, work: Path) -> ShellPlan:
    """Build the concrete invocation, or raise ``ShellUnavailable``."""
    command = str(command or "").strip()
    if not command:
        raise ShellUnavailable("command is required")

    backend, reason = detect_backend()
    if not backend:
        raise ShellUnavailable(f"no shell backend available: {reason}")
    released, release_reason = is_released(backend)
    if not released:
        raise ShellUnavailable(f"shell not released: {release_reason}")

    root = Path(root).resolve()
    work = Path(work).resolve()
    rel = work.relative_to(root)
    rel_posix = rel.as_posix() if rel.parts else ""
    sandboxed = backend in SANDBOXED_BACKENDS

    if backend == "bubblewrap":
        bwrap = _which("bwrap")
        argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin",
                "--ro-bind", "/lib", "/lib"]
        if Path("/lib64").exists():
            argv += ["--ro-bind", "/lib64", "/lib64"]
        argv += ["--bind", str(root), "/workspace",
                 "--chdir", "/workspace" + (("/" + rel_posix) if rel_posix else ""),
                 "--setenv", "HOME", "/workspace", "--setenv", "TMPDIR", "/tmp",
                 "/bin/bash", "-o", "pipefail", "-c", command]
        return ShellPlan(backend, argv, True, cwd=None, env=dict(os.environ))

    if backend == "docker":
        image = _docker_image()
        argv = ["docker", "run", "--rm", "-i",
                "--network", _env(ENV_DOCKER_NETWORK, "none"),
                "-v", f"{root}:/workspace",
                "-w", "/workspace" + (("/" + rel_posix) if rel_posix else ""),
                image, "/bin/sh", "-c", command]
        return ShellPlan(backend, argv, True, cwd=None, env=dict(os.environ))

    env = dict(os.environ)

    if backend == "termux":
        shell = _termux_bin("bash") or _termux_bin("sh") or "/system/bin/sh"
        if str(TERMUX_PREFIX) not in env.get("PATH", ""):
            env["PATH"] = f"{TERMUX_PREFIX / 'bin'}:{env.get('PATH', '')}".rstrip(":")
        env.setdefault("PREFIX", str(TERMUX_PREFIX))
        return ShellPlan(backend, _posix_argv(shell, command), False, cwd=str(work), env=env)

    if backend == "powershell":
        shell = _which("pwsh", "powershell", "powershell.exe")
        argv = [shell, "-NoLogo", "-NonInteractive", "-NoProfile",
                "-ExecutionPolicy", "Bypass", "-Command", command]
        return ShellPlan(backend, argv, False, cwd=str(work), env=env)

    if backend == "cmd":
        shell = _env("COMSPEC") or _which("cmd.exe")
        return ShellPlan(backend, [shell, "/d", "/c", command], False, cwd=str(work), env=env)

    shell = _which("zsh", "bash", "sh") if backend == "macos" else _which("bash", "sh")
    return ShellPlan(backend, _posix_argv(shell, command), False, cwd=str(work), env=env)
