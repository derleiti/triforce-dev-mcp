"""Safe, read-only background analysis for TriForce/AICoder agents."""
from __future__ import annotations

import asyncio
import hashlib
import fcntl
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from ..aicoder_runner import AICoderRunner, apply_profile_state, load_profile, prepare_instance_home
from .idle_prompt import WORK_CATALOGUE, build_idle_prompt

logger = logging.getLogger("ailinux.tristar.idle_worker")
DEFAULT_WORKSPACE = Path("/home/zombie/triforce")
STATE_FILE = Path(os.environ.get("TRISTAR_IDLE_STATE_FILE", "/var/tristar/agents/idle-state.json"))
STATE_LOCK_FILE = Path(os.environ.get("TRISTAR_IDLE_STATE_LOCK_FILE", "/var/tristar/agents/idle-state.lock"))
WORKER_LOCK_FILE = Path(os.environ.get("TRISTAR_IDLE_WORKER_LOCK_FILE", "/var/tristar/agents/idle-worker.lock"))
SNAPSHOT_ROOT = Path(os.environ.get("TRISTAR_IDLE_SNAPSHOT_ROOT", "/var/tristar/agents/idle-snapshots"))
READ_ONLY_TOOLS = ["file_read", "file_tree", "code_read", "code_tree", "code_search", "code_grep"]
DEFAULT_PROFILES = ("codex-mcp", "claude-mcp", "gemini-mcp", "opencode-mcp")


class IdleLeaseCoordinator:
    """Priority gate: foreground activity cancels idle readers of the same workspace."""
    def __init__(self, max_idle: int = 2):
        self.max_idle = max(1, int(max_idle))
        self._cond = asyncio.Condition()
        self._idle_tasks: dict[asyncio.Task, str] = {}
        self._foreground_waiters = 0
        self._foreground_active = 0
        self._provider_locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def idle(self, workspace: str, provider: str):
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("idle lease requires an asyncio task")
        async with self._cond:
            while self._foreground_waiters or self._foreground_active or len(self._idle_tasks) >= self.max_idle:
                await self._cond.wait()
            self._idle_tasks[task] = workspace
        provider_lock = self._provider_locks.setdefault(provider or "unknown", asyncio.Lock())
        try:
            async with provider_lock:
                yield
        finally:
            async with self._cond:
                self._idle_tasks.pop(task, None)
                self._cond.notify_all()

    @asynccontextmanager
    async def foreground(self, workspace: str):
        current = asyncio.current_task()
        async with self._cond:
            self._foreground_waiters += 1
            for task, ws in list(self._idle_tasks.items()):
                if ws == workspace and task is not current:
                    task.cancel()
            try:
                while any(ws == workspace for task, ws in self._idle_tasks.items() if task is not current):
                    await self._cond.wait()
                self._foreground_active += 1
            finally:
                self._foreground_waiters -= 1
        try:
            yield
        finally:
            async with self._cond:
                self._foreground_active = max(0, self._foreground_active - 1)
                self._cond.notify_all()

    def stats(self) -> dict[str, int]:
        return {"idle_active": len(self._idle_tasks), "foreground_active": self._foreground_active,
                "foreground_waiters": self._foreground_waiters, "provider_locks": len(self._provider_locks)}


lease_coordinator = IdleLeaseCoordinator(int(os.environ.get("TRISTAR_IDLE_MAX_CONCURRENT", "2")))


def _git(workspace: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(workspace), *args], text=True, stderr=subprocess.DEVNULL).strip()


def workspace_fingerprint(workspace: str | Path) -> str:
    root = Path(workspace).resolve()
    head = _git(root, "rev-parse", "HEAD")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=normal")
    return f"{head}:{hashlib.sha256(status.encode()).hexdigest()[:16]}"


def create_snapshot(workspace: str | Path, *, snapshot_root: str | Path = SNAPSHOT_ROOT) -> Path:
    """Copy tracked plus non-ignored untracked files; never share live .git metadata."""
    root = Path(workspace).resolve()
    target_root = Path(snapshot_root)
    target_root.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="idle-", dir=target_root))
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"])
    try:
        for item in raw.split(b"\0"):
            if not item:
                continue
            rel = item.decode("utf-8", errors="surrogateescape")
            src, dst = root / rel, target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_symlink():
                dst.symlink_to(os.readlink(src))
            elif src.is_file():
                shutil.copy2(src, dst)
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


@contextmanager
def _state_guard():
    STATE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = STATE_LOCK_FILE.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _load_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(f"{STATE_FILE.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def assign_operator_work(*, assignment: str, work_type: str = "bug-hunt", profile_id: str = "") -> dict[str, Any]:
    if work_type not in WORK_CATALOGUE:
        raise ValueError(f"unknown idle work type: {work_type}")
    text = str(assignment or "").strip()
    if not text:
        raise ValueError("assignment must not be empty")
    item = {"assignment": text[:4000], "work_type": work_type, "profile_id": str(profile_id or ""), "created_at": int(time.time())}
    with _state_guard():
        state = _load_state()
        queue = list(state.get("operator_queue") or [])
        queue.append(item)
        state["operator_queue"] = queue[-100:]
        _save_state(state)
    wake_idle_worker()
    return item


def _next_assignment(state: dict[str, Any]) -> tuple[str, str, str]:
    queue = list(state.get("operator_queue") or [])
    if queue:
        item = queue.pop(0)
        state["operator_queue"] = queue
        return str(item.get("work_type") or "bug-hunt"), str(item.get("assignment") or ""), str(item.get("profile_id") or "")
    suggestions = list(state.get("suggested_queue") or [])
    if suggestions:
        item = suggestions.pop(0)
        state["suggested_queue"] = suggestions
        return str(item.get("work_type") or "bug-hunt"), str(item.get("assignment") or ""), str(item.get("profile_id") or "")
    keys = list(WORK_CATALOGUE)
    cursor = int(state.get("catalogue_cursor") or 0) % len(keys)
    state["catalogue_cursor"] = cursor + 1
    return keys[cursor], "", ""


def _field(text: str, name: str) -> str:
    prefix = name + ":"
    return next((line[len(prefix):].strip() for line in str(text or "").splitlines() if line.startswith(prefix)), "")


def _parse_next_safe_work(text: str) -> tuple[str, str] | None:
    value = _field(text, "NEXT_SAFE_WORK").strip()
    if not value or value.upper() == "NONE":
        return None
    # Required format: <catalogue-key> | <bounded scope>. Ignore malformed
    # suggestions rather than guessing a task for the next fresh instance.
    key, sep, scope = value.partition("|")
    key = key.strip()
    scope = scope.strip()
    if not sep or key not in WORK_CATALOGUE or not scope:
        return None
    return key, scope[:2000]


async def run_idle_once(*, workspace: str | Path = DEFAULT_WORKSPACE, profile_id: str | None = None,
                        timeout: int | None = None) -> dict[str, Any]:
    root = Path(workspace).resolve()
    with _state_guard():
        state = _load_state()
        work_type, assignment, queued_profile = _next_assignment(state)
        profile_id = queued_profile or profile_id or DEFAULT_PROFILES[int(state.get("profile_cursor") or 0) % len(DEFAULT_PROFILES)]
        state["profile_cursor"] = int(state.get("profile_cursor") or 0) + 1
        _save_state(state)
    fingerprint = workspace_fingerprint(root)
    task_digest = hashlib.sha256(f"{root}:{fingerprint}:{work_type}:{assignment.strip()}".encode()).hexdigest()
    cooldown = max(0, int(os.environ.get("TRISTAR_IDLE_COOLDOWN_SECONDS", "1800")))
    if not assignment and time.time() - float(dict(state.get("recent_tasks") or {}).get(task_digest, 0)) < cooldown:
        return {"status": "deduplicated", "work_type": work_type, "fingerprint": fingerprint}

    profile = load_profile(profile_id)
    model = str(profile.get("model") or "")
    provider = model.split(":", 1)[-1].split("/", 1)[0] if model else profile_id
    findings = list(state.get("findings") or [])[-12:]
    previous = "\n".join(f"- {r.get('dedup_key','?')}: {r.get('title','')}" for r in findings) or "NONE"
    snapshot: Path | None = None
    try:
        async with lease_coordinator.idle(str(root), provider):
            snapshot = create_snapshot(root)
            home = prepare_instance_home(f"idle-{profile_id}")
            isolated = dict(profile)
            isolated.update({"workspace": str(snapshot), "enabled_tools": READ_ONLY_TOOLS, "approval_mode": "never", "team_mode": "off"})
            apply_profile_state(home, isolated)
            prompt = build_idle_prompt(work_type=work_type, assignment=assignment,
                                       snapshot_fingerprint=fingerprint, previous_findings=previous)
            result = await AICoderRunner().run(
                profile_id=f"idle-{profile_id}", prompt=prompt, model=model or None,
                workspace=snapshot, home=home,
                timeout=int(timeout or os.environ.get("TRISTAR_IDLE_TIMEOUT", "300")), team_mode="off")
    except asyncio.CancelledError:
        return {"status": "preempted", "work_type": work_type, "profile_id": profile_id, "fingerprint": fingerprint}
    finally:
        if snapshot is not None:
            shutil.rmtree(snapshot, ignore_errors=True)

    current = workspace_fingerprint(root)
    response = result.response or ""
    status = "stale" if current != fingerprint else result.status
    with _state_guard():
        state = _load_state()
        recent = dict(state.get("recent_tasks") or {})
        recent[task_digest] = int(time.time())
        state["recent_tasks"] = dict(list(recent.items())[-500:])
        if status == "success" and _field(response, "STATUS") == "FINDING":
            dedup_key = _field(response, "DEDUP_KEY") or hashlib.sha256(response.encode()).hexdigest()[:20]
            all_findings = list(state.get("findings") or [])
            if not any(row.get("dedup_key") == dedup_key for row in all_findings):
                all_findings.append({"dedup_key": dedup_key, "title": _field(response, "TITLE"), "work_type": work_type,
                                     "profile_id": profile_id, "fingerprint": fingerprint, "response": response[:12000],
                                     "created_at": int(time.time()), "status": "open"})
                state["findings"] = all_findings[-500:]
        next_safe = _parse_next_safe_work(response) if status == "success" else None
        if next_safe:
            next_type, next_scope = next_safe
            suggestions = list(state.get("suggested_queue") or [])
            signature = hashlib.sha256(f"{next_type}:{next_scope}".encode()).hexdigest()[:20]
            if not any(row.get("signature") == signature for row in suggestions):
                suggestions.append({"work_type": next_type, "assignment": next_scope,
                                    "profile_id": "", "signature": signature, "created_at": int(time.time())})
                state["suggested_queue"] = suggestions[-20:]
        _save_state(state)
    return {"status": status, "analysis_status": _field(response, "STATUS"), "work_type": work_type,
            "profile_id": profile_id, "model": result.model or model, "fingerprint": fingerprint,
            "current_fingerprint": current, "response": response, "error": result.error}


_worker_task: asyncio.Task | None = None
_worker_lock_handle = None
_worker_wakeup: asyncio.Event | None = None

def wake_idle_worker() -> bool:
    """Wake the process-local idle scheduler after an operator assignment."""
    if _worker_wakeup is None:
        return False
    try:
        _worker_wakeup.set()
        return True
    except Exception:
        return False


async def idle_loop() -> None:
    global _worker_wakeup
    _worker_wakeup = asyncio.Event()
    initial = max(5, int(os.environ.get("TRISTAR_IDLE_INITIAL_SECONDS", "60")))
    interval = max(60, int(os.environ.get("TRISTAR_IDLE_INTERVAL_SECONDS", "900")))
    try:
        try:
            await asyncio.wait_for(_worker_wakeup.wait(), timeout=initial)
            _worker_wakeup.clear()
        except asyncio.TimeoutError:
            pass
        while True:
            try:
                outcome = await run_idle_once()
                logger.info("idle analysis: %s", {k: v for k, v in outcome.items() if k != "response"})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("idle analysis cycle failed: %s", exc)
            try:
                await asyncio.wait_for(_worker_wakeup.wait(), timeout=interval)
                _worker_wakeup.clear()
            except asyncio.TimeoutError:
                pass
    finally:
        _worker_wakeup = None

def start_idle_worker() -> asyncio.Task | None:
    global _worker_task, _worker_lock_handle
    if os.environ.get("TRISTAR_IDLE_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        logger.info("Idle worker disabled (TRISTAR_IDLE_ENABLED=false)")
        return None
    if _worker_task and not _worker_task.done():
        return _worker_task
    WORKER_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = WORKER_LOCK_FILE.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        logger.info("Idle worker already owned by another backend process")
        return None
    _worker_lock_handle = handle
    _worker_task = asyncio.create_task(idle_loop(), name="tristar-idle-worker")
    return _worker_task

async def stop_idle_worker() -> None:
    global _worker_task, _worker_lock_handle
    if _worker_task is not None:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None
    if _worker_lock_handle is not None:
        try:
            fcntl.flock(_worker_lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            _worker_lock_handle.close()
            _worker_lock_handle = None
