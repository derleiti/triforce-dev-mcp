"""Fixed TriForce Docker blueprint operations for MCP/CLI integration.

This module never accepts an arbitrary compose path or shell command. Docker
socket authorization remains an operator decision; TriForce does not add its
service account to the docker group automatically.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

PROFILES = ("all", "redis", "wordpress", "flarum", "searxng", "n8n", "repository", "mailserver")
ACTIONS = ("validate", "status", "up", "down", "restart", "pull", "logs")


def blueprint_root() -> Path:
    root = Path(__file__).resolve().parents[1]
    packaged = root / "docker" / "blueprint"
    return packaged


def command(action: str, profile: str = "all") -> list[str]:
    if action not in ACTIONS:
        raise ValueError(f"unsupported docker action: {action}")
    if profile not in PROFILES:
        raise ValueError(f"unsupported docker profile: {profile}")
    script = blueprint_root() / "maintenance" / "stack.sh"
    return [str(script), action, profile]


async def run(action: str, profile: str = "all", timeout: float = 120.0) -> dict[str, Any]:
    cmd = command(action, profile)
    if not Path(cmd[0]).is_file():
        return {"success": False, "action": action, "profile": profile, "error": "Docker blueprint maintenance script missing"}
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return {"success": False, "action": action, "profile": profile, "error": "Docker operation timed out"}
    except OSError as exc:
        return {"success": False, "action": action, "profile": profile, "error": str(exc)}
    return {
        "success": proc.returncode == 0,
        "action": action,
        "profile": profile,
        "exit_code": proc.returncode,
        "output": out.decode(errors="replace").strip(),
        "error": err.decode(errors="replace").strip() or None,
    }
