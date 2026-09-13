"""Shared per-user recovery backups for mutating workspace operations."""
from __future__ import annotations

import json
import os
import shutil
import shlex
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX fallback
    fcntl = None

BACKUP_ROOT_ENV = "AILINUX_WORKSPACE_BACKUP_ROOT"
WORKSPACE_ROOT_ENV = "AILINUX_WORKSPACE_ROOT"


def shared_workspace_root() -> Path:
    return Path(os.environ.get(WORKSPACE_ROOT_ENV, str(Path.home() / "workspace"))).expanduser().resolve(strict=False)


def shared_backup_root() -> Path:
    configured = os.environ.get(BACKUP_ROOT_ENV)
    root = Path(configured).expanduser() if configured else shared_workspace_root() / ".workspacebackup"
    root = root.resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)
    except OSError:
        pass
    return root


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:8]


def _action_dir(workspace: Path, tool: str) -> Path:
    key = __import__("hashlib").sha256(str(workspace.resolve(strict=False)).encode("utf-8")).hexdigest()[:16]
    safe_tool = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in str(tool or "mutation"))[:64]
    path = shared_backup_root() / key / f"{_stamp()}-{safe_tool}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def _metadata(action: Path, workspace: Path, tool: str, kind: str, target: str = "") -> dict[str, str | int]:
    data: dict[str, str | int] = {
        "schema": 2,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace.resolve(strict=False)),
        "tool": str(tool),
        "kind": kind,
        "target": target,
    }
    (action / "metadata.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def _write_backup_doc(action: Path, data: dict[str, str | int], recovery: list[str]) -> None:
    lines = [
        "# Workspace backup",
        "",
        f"- Created: {data['created_at']}",
        f"- Source workspace: `{data['workspace']}`",
        f"- Trigger: `{data['tool']}`",
        f"- Backup kind: `{data['kind']}`",
        f"- Original target: `{data.get('target') or '(workspace)'}`",
        f"- Backup directory: `{action}`",
        "",
        "## Recovery",
        "",
        "Compare the current file/workspace first; restore only the state you intend to recover.",
        "",
    ]
    lines.extend(f"- `{command}`" for command in recovery)
    (action / "backup.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _append_index(action: Path, data: dict[str, str | int]) -> None:
    root = shared_backup_root()
    index = root / "INDEX.md"
    lock_path = root / ".index.lock"
    with lock_path.open("a+") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not index.exists():
            index.write_text(
                "# Workspace Backup Index\n\n"
                "Newest backups are appended below. Each entry points to a self-contained `backup.md`.\n\n",
                encoding="utf-8",
            )
        with index.open("a", encoding="utf-8") as handle:
            handle.write(
                f"- {data['created_at']} — `{data['tool']}` — {data['kind']} — "
                f"`{data.get('target') or data['workspace']}` — `{action}`\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _document_backup(action: Path, data: dict[str, str | int], recovery: list[str]) -> None:
    _write_backup_doc(action, data, recovery)
    _append_index(action, data)


def backup_file(workspace: Path, target: Path, *, tool: str) -> Path:
    """Persist the pre-change state of one path; new paths get a creation marker."""
    action = _action_dir(workspace, tool)
    relative = target.resolve(strict=False).relative_to(workspace.resolve(strict=False))
    data = _metadata(action, workspace, tool, "file", relative.as_posix())
    if not target.exists() and not target.is_symlink():
        (action / "target-was-absent").write_text(relative.as_posix() + "\n", encoding="utf-8")
        _document_backup(action, data, [f"rm -f -- {shlex.quote(str(target))}  # recover original absent state"])
        return action
    destination = action / "files" / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        os.symlink(os.readlink(target), destination)
    elif target.is_file():
        shutil.copy2(target, destination, follow_symlinks=False)
    elif target.is_dir():
        shutil.copytree(target, destination, symlinks=True)
    else:
        raise ValueError(f"unsupported backup target: {target}")
    _document_backup(
        action, data,
        [f"cp -a -- {shlex.quote(str(destination))} {shlex.quote(str(target))}"],
    )
    return action


def backup_workspace(workspace: Path, *, tool: str) -> Path:
    """Create a complete pre-change archive for an operation with unknown write scope."""
    workspace = workspace.resolve(strict=True)
    action = _action_dir(workspace, tool)
    data = _metadata(action, workspace, tool, "workspace")
    archive = action / "workspace.tar.gz"
    backup_root = shared_backup_root().resolve(strict=False)
    with tarfile.open(archive, "w:gz") as tar:
        for base, dirnames, filenames in os.walk(workspace, topdown=True, followlinks=False):
            base_path = Path(base)
            kept_dirs = []
            for name in dirnames:
                child = base_path / name
                if child.resolve(strict=False) == backup_root:
                    continue
                kept_dirs.append(name)
            dirnames[:] = kept_dirs
            if base_path != workspace:
                tar.add(base_path, arcname=base_path.relative_to(workspace).as_posix(), recursive=False)
            for name in filenames:
                child = base_path / name
                try:
                    resolved = child.resolve(strict=False)
                except OSError:
                    resolved = child.absolute()
                if resolved == backup_root or backup_root in resolved.parents:
                    continue
                tar.add(child, arcname=child.relative_to(workspace).as_posix(), recursive=False)
    recovery_dir = action / "recovery-preview"
    _document_backup(
        action, data,
        [
            f"mkdir -p -- {shlex.quote(str(recovery_dir))}",
            f"tar -xzf {shlex.quote(str(archive))} -C {shlex.quote(str(recovery_dir))}",
            f"diff -ruN -- {shlex.quote(str(workspace))} {shlex.quote(str(recovery_dir))}  # inspect before restoring",
        ],
    )
    return action


def backup_directory_creation(workspace: Path, target: Path, *, tool: str) -> Path:
    action = _action_dir(workspace, tool)
    relative = target.resolve(strict=False).relative_to(workspace.resolve(strict=False))
    data = _metadata(action, workspace, tool, "directory-create", relative.as_posix())
    (action / "target-was-absent").write_text(relative.as_posix() + "\n", encoding="utf-8")
    _document_backup(action, data, [f"rmdir -- {shlex.quote(str(target))}  # succeeds only if no later files were added"])
    return action
