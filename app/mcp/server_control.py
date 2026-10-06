from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import pwd
import re
import signal as signal_module
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.parse import urlparse


_SERVICE_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_DESKTOP_ID_RE = re.compile(r"^[A-Za-z0-9_.@+-]+$")
_MAX_OUTPUT = 200_000
_MAX_SCREENSHOT = 8 * 1024 * 1024
_DEFAULT_TIMEOUT = 30
_MAX_TIMEOUT = 300
_YDOTOOL_SOCKET = os.getenv("TRIFORCE_YDOTOOL_SOCKET", "/run/ydotoold-zombie/socket")
_KEY_CODES = {
    "ESC": 1, "ESCAPE": 1, "TAB": 15, "ENTER": 28, "RETURN": 28,
    "CTRL": 29, "CONTROL": 29, "SHIFT": 42, "ALT": 56, "SPACE": 57,
    "F5": 63, "HOME": 102, "UP": 103, "PAGEUP": 104, "LEFT": 105,
    "RIGHT": 106, "END": 107, "DOWN": 108, "PAGEDOWN": 109, "DELETE": 111,
    "META": 125, "SUPER": 125,
    "BACKSPACE": 14,
    "A": 30, "B": 48, "C": 46, "D": 32, "E": 18, "F": 33, "G": 34,
    "H": 35, "I": 23, "J": 36, "K": 37, "L": 38, "M": 50, "N": 49,
    "O": 24, "P": 25, "Q": 16, "R": 19, "S": 31, "T": 20, "U": 22,
    "V": 47, "W": 17, "X": 45, "Y": 21, "Z": 44,
}


SERVER_CONTROL_TOOLS = [
    {
        "name": "server_control",
        "description": (
            "Directly control the TriForce host without AILinux Helper pairing. "
            "Wayland-native server operations: host/session status, bounded shell execution, "
            "systemd service management, process inspection/signalling, desktop app/URL launch "
            "in the active Wayland session, and KDE Spectacle screenshots. "
            "This is a privileged TriForce-admin capability and never falls back to xdotool/X11 input."
        ),
        "inputSchema": {
            "type": "object",
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "exec", "service", "process", "app", "screenshot", "input"],
                },
                "detail": {
                    "type": "string",
                    "enum": ["summary", "desktop", "all"],
                    "default": "summary",
                },
                "command": {"type": "string", "description": "Shell command for action=exec"},
                "cwd": {"type": "string", "description": "Working directory for action=exec"},
                "timeout": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_TIMEOUT,
                    "default": _DEFAULT_TIMEOUT,
                },
                "sudo": {"type": "boolean", "default": False},
                "operation": {
                    "type": "string",
                    "description": (
                        "Sub-action. service: list/status/logs/start/stop/restart/enable/disable; "
                        "process: list/find/signal; app: list/open/open_url; "
                        "input: move/click/type/key."
                    ),
                },
                "service": {"type": "string"},
                "lines": {"type": "integer", "minimum": 1, "maximum": 500, "default": 80},
                "pid": {"type": "integer", "minimum": 1},
                "signal": {
                    "type": "string",
                    "description": "Process signal name, e.g. SIGTERM, SIGKILL, SIGHUP",
                },
                "query": {"type": "string", "description": "Process search substring"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 40},
                "app": {
                    "type": "string",
                    "description": "Desktop application ID for app/open, e.g. org.kde.konsole",
                },
                "url": {
                    "type": "string",
                    "description": "http/https URL for app/open_url",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional desktop-app arguments when supported",
                },
                "include_image": {
                    "type": "boolean",
                    "default": True,
                    "description": "For screenshot, include PNG as base64 in the result",
                },
                "x": {"type": "integer", "minimum": 0},
                "y": {"type": "integer", "minimum": 0},
                "text": {"type": "string", "description": "Text for input/type"},
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Keys for input/key, e.g. [CTRL, SHIFT, R]",
                },
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "default": "left",
                },
            },
        },
        "annotations": {
            "title": "TriForce Server Control",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "x_inventory": "admin",
    }
]


def _bounded_text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        text = data.decode("utf-8", errors="replace")
    else:
        text = str(data)
    if len(text) <= _MAX_OUTPUT:
        return text
    return text[:_MAX_OUTPUT] + "\n...[truncated]"


async def _run_exec(
    argv: Iterable[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), _MAX_TIMEOUT))
    try:
        proc = await asyncio.create_subprocess_exec(
            *[str(x) for x in argv],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or None,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": _bounded_text(stdout),
            "stderr": _bounded_text(stderr),
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return {"ok": False, "error": f"command timed out after {timeout}s", "returncode": -1}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "returncode": -1}


async def _run_shell(
    command: str,
    *,
    cwd: Optional[str] = None,
    timeout: int = _DEFAULT_TIMEOUT,
    sudo: bool = False,
) -> Dict[str, Any]:
    if not str(command or "").strip():
        return {"ok": False, "error": "command is required"}
    workdir = str(cwd or "/home/zombie/workspace/triforce")
    if not os.path.isdir(workdir):
        return {"ok": False, "error": f"cwd does not exist: {workdir}"}
    argv = ["/bin/bash", "-lc", str(command)]
    if sudo and os.geteuid() != 0:
        argv = ["sudo", "-n", *argv]
    result = await _run_exec(argv, cwd=workdir, timeout=timeout)
    result["cwd"] = workdir
    result["sudo_requested"] = bool(sudo)
    return result


def _candidate_graphical_users() -> list[str]:
    users: list[str] = []
    try:
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                raw = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="ignore")
                if not any(
                    token in raw
                    for token in (
                        "kwin_wayland",
                        "kwin_wayland_wrapper",
                        "plasmashell",
                        "gnome-shell",
                        "sway",
                    )
                ):
                    continue
                uid = (proc / "status").read_text(encoding="utf-8", errors="ignore")
                match = re.search(r"^Uid:\s+(\d+)", uid, re.MULTILINE)
                if not match:
                    continue
                name = pwd.getpwuid(int(match.group(1))).pw_name
                if name not in users:
                    users.append(name)
            except Exception:
                continue
    except Exception:
        pass
    configured = os.getenv("TRIFORCE_DESKTOP_USER", "").strip()
    if configured and configured not in users:
        users.insert(0, configured)
    if "zombie" not in users:
        try:
            pwd.getpwnam("zombie")
            users.append("zombie")
        except KeyError:
            pass
    return users


def _desktop_session() -> Dict[str, Any]:
    users = _candidate_graphical_users()
    for username in users:
        try:
            pw = pwd.getpwnam(username)
        except KeyError:
            continue
        runtime = Path(f"/run/user/{pw.pw_uid}")
        if not runtime.is_dir():
            continue
        wayland_sockets = sorted(
            p.name
            for p in runtime.glob("wayland-*")
            if p.is_socket()
        )
        if not wayland_sockets:
            continue
        env = os.environ.copy()
        env.update(
            {
                "HOME": pw.pw_dir,
                "USER": username,
                "LOGNAME": username,
                "XDG_RUNTIME_DIR": str(runtime),
                "XDG_SESSION_TYPE": "wayland",
                "WAYLAND_DISPLAY": wayland_sockets[0],
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
                "XDG_CURRENT_DESKTOP": env.get("XDG_CURRENT_DESKTOP") or "KDE",
                "KDE_FULL_SESSION": "true",
            }
        )
        # DISPLAY is compatibility metadata for apps that still spawn an XWayland
        # child, but input control in this module never uses X11/xdotool.
        if Path("/tmp/.X11-unix/X0").exists():
            env["DISPLAY"] = ":0"
        return {
            "ok": True,
            "user": username,
            "uid": pw.pw_uid,
            "runtime_dir": str(runtime),
            "wayland_display": wayland_sockets[0],
            "env": env,
        }
    return {"ok": False, "error": "no active Wayland desktop session found", "users_checked": users}


def _as_desktop_user(session: Dict[str, Any], argv: list[str]) -> tuple[list[str], Dict[str, str]]:
    env = dict(session["env"])
    user = str(session["user"])
    if os.geteuid() == 0:
        env_args = [f"{key}={value}" for key, value in env.items() if key in {
            "HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE",
            "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_CURRENT_DESKTOP",
            "KDE_FULL_SESSION", "DISPLAY",
        }]
        return ["runuser", "-u", user, "--", "env", *env_args, *argv], os.environ.copy()
    return argv, env


async def _status(detail: str) -> Dict[str, Any]:
    detail = detail if detail in {"summary", "desktop", "all"} else "summary"
    desktop = _desktop_session()
    result: Dict[str, Any] = {
        "ok": True,
        "action": "status",
        "hostname": os.uname().nodename,
        "kernel": os.uname().release,
        "pid": os.getpid(),
        "euid": os.geteuid(),
        "wayland": {
            k: v
            for k, v in desktop.items()
            if k in {"ok", "user", "uid", "runtime_dir", "wayland_display", "error"}
        },
    }
    if detail in {"summary", "all"}:
        probe = await _run_exec(
            ["/bin/bash", "-lc", "uptime -p; free -h | head -2; df -h / | tail -1"],
            timeout=10,
        )
        result["summary"] = probe
    if detail in {"desktop", "all"}:
        desktop_probe = await _run_exec(
            ["/bin/bash", "-lc", "ps -eo user,pid,comm,args | grep -E 'kwin_wayland|plasmashell|gnome-shell|sway' | grep -v grep | head -20"],
            timeout=10,
        )
        result["desktop"] = desktop_probe
    return result


async def _service(params: Dict[str, Any]) -> Dict[str, Any]:
    operation = str(params.get("operation") or "list")
    service = str(params.get("service") or "")
    lines = max(1, min(int(params.get("lines") or 80), 500))
    if operation == "list":
        return {
            "action": "service",
            "operation": operation,
            **await _run_exec(
                ["systemctl", "list-units", "--type=service", "--state=running", "--no-pager", "--plain"],
                timeout=20,
            ),
        }
    if not service or not _SERVICE_RE.fullmatch(service):
        return {"ok": False, "error": "valid service name is required", "action": "service"}
    if operation == "status":
        argv = ["systemctl", "status", service, "--no-pager", "-l"]
    elif operation == "logs":
        argv = ["journalctl", "-u", service, "--no-pager", "-n", str(lines)]
    elif operation in {"start", "stop", "restart", "enable", "disable"}:
        argv = ["systemctl", operation, service]
        if os.geteuid() != 0:
            argv = ["sudo", "-n", *argv]
    else:
        return {"ok": False, "error": f"unsupported service operation: {operation}", "action": "service"}
    return {"action": "service", "operation": operation, "service": service, **await _run_exec(argv, timeout=60)}


async def _process(params: Dict[str, Any]) -> Dict[str, Any]:
    operation = str(params.get("operation") or "list")
    limit = max(1, min(int(params.get("limit") or 40), 200))
    if operation == "list":
        return {
            "action": "process",
            "operation": operation,
            **await _run_exec(
                ["/bin/bash", "-lc", f"ps -eo pid,user,stat,%cpu,%mem,comm,args --sort=-%cpu | head -n {limit + 1}"],
                timeout=15,
            ),
        }
    if operation == "find":
        query = str(params.get("query") or "").strip()
        if not query:
            return {"ok": False, "error": "query is required", "action": "process"}
        result = await _run_exec(["pgrep", "-af", "--", query], timeout=15)
        result.update({"action": "process", "operation": operation, "query": query})
        return result
    if operation == "signal":
        pid = int(params.get("pid") or 0)
        signal_name = str(params.get("signal") or "SIGTERM").upper()
        if pid <= 1:
            return {"ok": False, "error": "pid must be greater than 1", "action": "process"}
        allowed = {"SIGTERM", "SIGKILL", "SIGHUP", "SIGINT", "SIGUSR1", "SIGUSR2"}
        if signal_name not in allowed:
            return {"ok": False, "error": f"signal must be one of {sorted(allowed)}", "action": "process"}
        try:
            os.kill(pid, getattr(signal_module, signal_name))
            return {"ok": True, "action": "process", "operation": operation, "pid": pid, "signal": signal_name}
        except Exception as exc:
            return {"ok": False, "action": "process", "error": f"{type(exc).__name__}: {exc}", "pid": pid}
    return {"ok": False, "error": f"unsupported process operation: {operation}", "action": "process"}


async def _app(params: Dict[str, Any]) -> Dict[str, Any]:
    operation = str(params.get("operation") or "list")
    session = _desktop_session()
    if not session.get("ok"):
        return {"ok": False, "action": "app", "error": session.get("error")}
    if operation == "list":
        user = str(session["user"])
        return {
            "action": "app",
            "operation": operation,
            "desktop_user": user,
            **await _run_exec(
                ["/bin/bash", "-lc", f"ps -u {user} -o pid,comm,args --sort=comm | head -n 120"],
                timeout=15,
            ),
        }
    if operation == "open_url":
        url = str(params.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {"ok": False, "action": "app", "error": "url must be http/https"}
        argv, env = _as_desktop_user(session, ["xdg-open", url])
        result = await _run_exec(argv, env=env, timeout=30)
        result.update({"action": "app", "operation": operation, "url": url, "desktop_user": session["user"]})
        return result
    if operation == "open":
        app = str(params.get("app") or "").strip()
        if not app or not _DESKTOP_ID_RE.fullmatch(app):
            return {"ok": False, "action": "app", "error": "valid desktop app id is required"}
        extra = [str(x) for x in (params.get("args") or [])][:20]
        argv, env = _as_desktop_user(session, ["gtk-launch", app, *extra])
        result = await _run_exec(argv, env=env, timeout=30)
        result.update({"action": "app", "operation": operation, "app": app, "desktop_user": session["user"]})
        return result
    return {"ok": False, "action": "app", "error": f"unsupported app operation: {operation}"}


def _key_sequence(keys: Iterable[str]) -> list[str]:
    names = [str(k or "").strip().upper() for k in keys]
    if not names or len(names) > 16:
        raise ValueError("keys must contain 1..16 entries")
    codes: list[int] = []
    for name in names:
        code = _KEY_CODES.get(name)
        if code is None:
            raise ValueError(f"unsupported key: {name}")
        codes.append(code)
    # Hold modifiers/keys in declared order, release in reverse order.
    return [*(f"{code}:1" for code in codes), *(f"{code}:0" for code in reversed(codes))]


async def _input(params: Dict[str, Any]) -> Dict[str, Any]:
    operation = str(params.get("operation") or "").strip().lower()
    if operation not in {"move", "click", "type", "key"}:
        return {"ok": False, "action": "input", "error": "operation must be move, click, type, or key"}
    socket_path = Path(_YDOTOOL_SOCKET)
    if not socket_path.exists():
        return {"ok": False, "action": "input", "error": f"ydotool socket unavailable: {socket_path}"}
    env = os.environ.copy()
    env["YDOTOOL_SOCKET"] = str(socket_path)
    argv: list[str]
    if operation == "move":
        x = int(params.get("x") or 0)
        y = int(params.get("y") or 0)
        argv = ["/usr/bin/ydotool", "mousemove", "--absolute", str(x), str(y)]
    elif operation == "click":
        x = params.get("x")
        y = params.get("y")
        if x is not None and y is not None:
            moved = await _run_exec(
                ["/usr/bin/ydotool", "mousemove", "--absolute", str(int(x)), str(int(y))],
                env=env,
                timeout=10,
            )
            if not moved.get("ok"):
                return {"action": "input", "operation": operation, **moved}
        button = str(params.get("button") or "left").lower()
        button_code = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}.get(button)
        if button_code is None:
            return {"ok": False, "action": "input", "error": "button must be left, right, or middle"}
        argv = ["/usr/bin/ydotool", "click", button_code]
    elif operation == "type":
        text = str(params.get("text") or "")
        if not text:
            return {"ok": False, "action": "input", "error": "text is required"}
        if len(text) > 65536:
            return {"ok": False, "action": "input", "error": "text exceeds 65536 characters"}
        argv = ["/usr/bin/ydotool", "type", "--key-delay", "1", text]
    else:
        try:
            sequence = _key_sequence(params.get("keys") or [])
        except ValueError as exc:
            return {"ok": False, "action": "input", "error": str(exc)}
        argv = ["/usr/bin/ydotool", "key", "--key-delay", "12", *sequence]
    result = await _run_exec(argv, env=env, timeout=15)
    result.update({"action": "input", "operation": operation, "adapter": "ydotool-uinput-wayland"})
    return result


async def _screenshot(params: Dict[str, Any]) -> Dict[str, Any]:
    session = _desktop_session()
    if not session.get("ok"):
        return {"ok": False, "action": "screenshot", "error": session.get("error")}
    include_image = params.get("include_image", True) is not False
    fd, tmp_name = tempfile.mkstemp(prefix="triforce-server-", suffix=".png")
    os.close(fd)
    path = Path(tmp_name)
    try:
        argv, env = _as_desktop_user(
            session,
            ["spectacle", "-b", "-n", "-f", "-o", str(path)],
        )
        capture = await _run_exec(argv, env=env, timeout=30)
        if not capture.get("ok") or not path.is_file():
            return {
                "ok": False,
                "action": "screenshot",
                "desktop_user": session["user"],
                "capture": capture,
                "error": "Wayland screenshot capture failed",
            }
        data = path.read_bytes()
        if len(data) > _MAX_SCREENSHOT:
            return {
                "ok": False,
                "action": "screenshot",
                "error": f"screenshot exceeds {_MAX_SCREENSHOT} bytes",
                "size_bytes": len(data),
            }
        result: Dict[str, Any] = {
            "ok": True,
            "action": "screenshot",
            "mime_type": "image/png",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "desktop_user": session["user"],
            "wayland_display": session["wayland_display"],
        }
        if include_image:
            result["image_base64"] = base64.b64encode(data).decode("ascii")
        return result
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


async def handle_server_control(params: Dict[str, Any]) -> Dict[str, Any]:
    action = str(params.get("action") or "").strip().lower()
    if action == "status":
        return await _status(str(params.get("detail") or "summary"))
    if action == "exec":
        result = await _run_shell(
            str(params.get("command") or ""),
            cwd=params.get("cwd"),
            timeout=int(params.get("timeout") or _DEFAULT_TIMEOUT),
            sudo=bool(params.get("sudo", False)),
        )
        result["action"] = "exec"
        return result
    if action == "service":
        return await _service(params)
    if action == "process":
        return await _process(params)
    if action == "app":
        return await _app(params)
    if action == "screenshot":
        return await _screenshot(params)
    if action == "input":
        return await _input(params)
    return {
        "ok": False,
        "error": "action must be one of: status, exec, service, process, app, screenshot, input",
    }


SERVER_CONTROL_HANDLERS = {"server_control": handle_server_control}
