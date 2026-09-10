"""Structured headless runner for AICoder agent instances.

This module is intentionally independent from the legacy CLI-wrapper agent paths.
It launches the installed AICoder package in NDJSON mode, keeps per-instance HOME
state isolated, and returns a small normalized result for TriForce callers.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

DEFAULT_AICODER = "/usr/bin/aicoder"
DEFAULT_INSTANCE_ROOT = Path("/var/tristar/agents/instances")
PROVIDER_HOME_DIRS = (".codex", ".claude", ".vibe", ".gemini", ".antigravity")
PROVIDER_EXEC_LINKS = (
    (".npm-global", ".npm-global"),
    (".local/bin", ".local/bin"),
    (".local/share/uv/tools", ".local/share/uv/tools"),
)


@dataclass(slots=True)
class AICoderRunResult:
    profile_id: str
    status: str
    response: str = ""
    error: str = ""
    exit_code: int | None = None
    run_id: str = ""
    plan_id: str = ""
    model: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)

    def event_summary(self) -> dict[str, Any]:
        tools = [str(e.get("name") or "") for e in self.events if e.get("type") == "tool_call"]
        terminal = next((e for e in reversed(self.events) if e.get("type") == "run_terminal"), {})
        perf = next((e for e in reversed(self.events) if e.get("type") == "performance_summary"), {})
        return {
            "event_count": len(self.events),
            "tool_calls": tools,
            "tool_call_count": len(tools),
            "tool_error_count": sum(1 for e in self.events if e.get("type") == "tool_result" and e.get("is_error")),
            "elapsed_ms": int(terminal.get("elapsed_ms") or perf.get("wall_ms") or 0),
            "model_requests": int(perf.get("model_requests") or 0),
        }

    def to_dict(self, *, include_events: bool = False) -> dict[str, Any]:
        payload = {
            "agent_id": self.profile_id,
            "status": self.status,
            "response": self.response,
            "error": self.error,
            "exit_code": self.exit_code,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "model": self.model,
            "event_summary": self.event_summary(),
        }
        if include_events:
            payload["events"] = self.events
        return payload


def _safe_profile_id(profile_id: str) -> str:
    value = str(profile_id or "").strip()
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for ch in value):
        raise ValueError(f"invalid AICoder profile id: {profile_id!r}")
    return value


def _copy_private_file(source: Path, target: Path) -> None:
    data = source.read_bytes()
    target.write_bytes(data)
    target.chmod(0o600)


def prepare_instance_home(
    profile_id: str,
    *,
    source_home: str | Path = "/home/zombie",
    instance_root: str | Path = DEFAULT_INSTANCE_ROOT,
) -> Path:
    """Create isolated AICoder state while reusing provider-owned login stores.

    AICoder's own state/session files are copied into the instance HOME. Provider
    login directories are symlinked so account refresh remains owned by the
    official provider clients. Existing files are never overwritten here.
    """
    profile_id = _safe_profile_id(profile_id)
    source_home = Path(source_home)
    home = Path(instance_root) / profile_id / "home"
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)

    for name in PROVIDER_HOME_DIRS:
        source = source_home / name
        target = home / name
        if source.exists() and not target.exists():
            target.symlink_to(source, target_is_directory=True)

    # Official account transports discover provider CLIs relative to Path.home().
    # Keep the instance HOME isolated while exposing only the user's executable
    # install trees needed by those official clients.
    for source_rel, target_rel in PROVIDER_EXEC_LINKS:
        source = source_home / source_rel
        target = home / target_rel
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)

    source_cfg = source_home / ".config" / "ai-coder"
    target_cfg = home / ".config" / "ai-coder"
    target_cfg.mkdir(parents=True, exist_ok=True)
    target_cfg.chmod(0o700)
    for name in ("session.json", "state.json", "mcp_servers.json"):
        source = source_cfg / name
        target = target_cfg / name
        if source.is_file() and not target.exists():
            _copy_private_file(source, target)

    # Some older AICoder paths use XDG_CONFIG_HOME/aicoder instead of ai-coder.
    # Keep the namespace available without sharing mutable state between agents.
    (home / ".config" / "aicoder").mkdir(parents=True, exist_ok=True)
    return home


def parse_ndjson(lines: Iterable[str]) -> tuple[list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    raw: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        try:
            item = json.loads(text)
        except json.JSONDecodeError:
            raw.append(text)
            continue
        if isinstance(item, dict):
            events.append(item)
        else:
            raw.append(text)
    return events, raw


class AICoderRunner:
    def __init__(self, executable: str = DEFAULT_AICODER):
        self.executable = executable

    async def _terminate_process_group(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
            return
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        await process.wait()

    async def run(
        self,
        *,
        profile_id: str,
        prompt: str,
        model: str | None = None,
        workspace: str | Path,
        home: str | Path,
        timeout: int = 120,
        team_mode: str | None = None,
        system_prompt: str = "",
        extra_env: dict[str, str] | None = None,
    ) -> AICoderRunResult:
        profile_id = _safe_profile_id(profile_id)
        workspace = Path(workspace).resolve()
        home = Path(home).resolve()
        if not workspace.is_dir():
            raise ValueError(f"AICoder workspace does not exist: {workspace}")
        if not home.is_dir():
            raise ValueError(f"AICoder instance HOME does not exist: {home}")
        if not Path(self.executable).is_file():
            raise FileNotFoundError(self.executable)

        effective_prompt = prompt
        if system_prompt.strip():
            effective_prompt = (
                "[TriForce agent profile instructions]\n"
                f"{system_prompt.strip()}\n"
                "[Task]\n"
                f"{prompt}"
            )

        command = [self.executable, "agent", effective_prompt, "--json-events"]
        if model:
            command += ["--model", model]
        if team_mode:
            command += ["--team-mode", team_mode]

        env = os.environ.copy()
        env.update(extra_env or {})
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(home / ".config")
        if str(model or "").startswith("account:claude/"):
            for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
                env.pop(name, None)

        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(workspace),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=max(1, int(timeout)))
        except asyncio.TimeoutError:
            await self._terminate_process_group(process)
            return AICoderRunResult(
                profile_id=profile_id,
                status="timeout",
                error=f"AICoder did not finish within {int(timeout)}s",
                exit_code=process.returncode,
            )

        lines = stdout.decode("utf-8", errors="replace").splitlines()
        events, raw = parse_ndjson(lines)
        result_event = next((e for e in reversed(events) if e.get("type") == "result"), {})
        terminal = next((e for e in reversed(events) if e.get("type") == "run_terminal"), {})
        final = next((e for e in reversed(events) if e.get("type") == "final"), {})
        start = next((e for e in events if e.get("type") == "run_start"), {})

        native_status = str(result_event.get("status") or terminal.get("status") or "").lower()
        if native_status == "completed" and process.returncode == 0:
            status = "success"
        elif native_status == "paused":
            status = "paused"
        else:
            status = "error"

        error = str(result_event.get("error") or terminal.get("error") or "")
        if not error and status == "error":
            error_event = next((e for e in reversed(events) if e.get("type") == "error"), {})
            error = str(error_event.get("message") or "")
        if not error and status == "error":
            error = "AICoder exited without a successful terminal event"
        if raw and status == "error":
            error = f"{error}: {raw[-1][:500]}" if error else raw[-1][:500]

        return AICoderRunResult(
            profile_id=profile_id,
            status=status,
            response=str(result_event.get("response") or final.get("response") or ""),
            error=error,
            exit_code=process.returncode,
            run_id=str(start.get("run_id") or terminal.get("run_id") or ""),
            plan_id=str(result_event.get("plan_id") or terminal.get("plan_id") or ""),
            model=str(result_event.get("model") or final.get("model") or start.get("model") or model or ""),
            events=events[-500:],
            raw_lines=raw[-100:],
        )

DEFAULT_PROFILE_ROOT = Path("/var/tristar/agents/profiles")


def load_profile(profile_id: str, profile_root: str | Path = DEFAULT_PROFILE_ROOT) -> dict[str, Any]:
    profile_id = _safe_profile_id(profile_id)
    path = Path(profile_root) / f"{profile_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"AICoder profile not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"AICoder profile must be a JSON object: {path}")
    if data.get("id") not in (None, profile_id):
        raise ValueError(f"AICoder profile id mismatch: {path}")
    target = str(data.get("target") or "local")
    if target != "local":
        raise ValueError(f"unsupported AICoder target for local runner: {target}")
    return data


def apply_profile_state(home: str | Path, profile: dict[str, Any]) -> None:
    """Apply only profile-owned AICoder settings to an isolated instance state."""
    home = Path(home)
    state_path = home / ".config" / "ai-coder" / "state.json"
    state: dict[str, Any] = {}
    if state_path.is_file():
        loaded = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            state = loaded
    mapping = {
        "model": "selected_model",
        "approval_mode": "approval_mode",
        "enabled_tools": "enabled_tools",
        "workspace": "workspace_root",
        "timeout": "request_timeout",
        "team_mode": "team_runtime_mode",
    }
    for source_key, state_key in mapping.items():
        if source_key in profile and profile[source_key] is not None:
            state[state_key] = profile[source_key]
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    os.replace(tmp, state_path)


async def run_profile(
    profile_id: str,
    prompt: str,
    *,
    timeout_override: int | None = None,
    runner: AICoderRunner | None = None,
    source_home: str | Path = "/home/zombie",
    instance_root: str | Path = DEFAULT_INSTANCE_ROOT,
    profile_root: str | Path = DEFAULT_PROFILE_ROOT,
) -> AICoderRunResult:
    profile = load_profile(profile_id, profile_root)
    home = prepare_instance_home(profile_id, source_home=source_home, instance_root=instance_root)
    apply_profile_state(home, profile)
    workspace = Path(str(profile.get("workspace") or "/home/zombie/triforce")).resolve()
    effective_timeout = int(timeout_override or profile.get("timeout") or 120)
    return await (runner or AICoderRunner()).run(
        profile_id=profile_id,
        prompt=prompt,
        model=str(profile.get("model") or "") or None,
        workspace=workspace,
        home=home,
        timeout=effective_timeout,
        team_mode=str(profile.get("team_mode") or "") or None,
        system_prompt=str(profile.get("system_prompt") or ""),
    )
