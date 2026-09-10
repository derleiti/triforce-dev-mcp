"""Local execution runtime for the standalone TriForce Workspace Client."""
from __future__ import annotations

import fnmatch
import json
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IGNORE_NAMES = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache"}
READ_TOOLS = {"workspace_info", "file_read", "file_tree", "code_read", "code_tree", "code_search", "code_grep", "git"}
WRITE_TOOLS = READ_TOOLS | {"file_edit", "directory_create", "workspace_clear", "shell", "binary_exec", "task_runner", "lint", "test"}


def _result(text: str, error: bool = False, **structured: Any) -> dict[str, Any]:
    result = {"content": [{"type": "text", "text": str(text)}], "isError": bool(error)}
    if structured:
        result["structuredContent"] = structured
    return result


@dataclass
class WorkspaceRuntime:
    root: Path
    writable: bool = False
    task: str = ""

    def __post_init__(self) -> None:
        self.root = self.root.expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError(f"workspace is not a directory: {self.root}")

    @property
    def mode(self) -> str:
        return "write" if self.writable else "read_only"

    def resolve(self, value: str | None = ".", *, must_exist: bool = False) -> Path:
        raw = str(value or ".").strip()
        if "\x00" in raw:
            raise ValueError("NUL byte is not allowed")
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            # Accept an absolute path only when it resolves within the chosen workspace.
            target = candidate.resolve(strict=must_exist)
        else:
            target = (self.root / candidate).resolve(strict=must_exist)
        try:
            target.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError("path is outside the selected workspace") from exc
        return target

    def display(self, path: Path) -> str:
        rel = path.resolve(strict=False).relative_to(self.root)
        return rel.as_posix() if rel.parts else "."

    def analyze(self) -> dict[str, Any]:
        files = dirs = bytes_total = 0
        extensions: dict[str, int] = {}
        samples: list[str] = []
        for base, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORE_NAMES]
            dirs += len(dirnames)
            base_path = Path(base)
            for name in filenames:
                if name in IGNORE_NAMES:
                    continue
                path = base_path / name
                try:
                    stat = path.stat()
                except OSError:
                    continue
                files += 1
                bytes_total += stat.st_size
                ext = path.suffix.lower() or "[no extension]"
                extensions[ext] = extensions.get(ext, 0) + 1
                if len(samples) < 30:
                    samples.append(self.display(path))
        git = (self.root / ".git").exists()
        return {
            "workspace": str(self.root),
            "mode": self.mode,
            "task": self.task,
            "files": files,
            "directories": dirs,
            "bytes": bytes_total,
            "git_repository": git,
            "top_extensions": sorted(extensions.items(), key=lambda item: (-item[1], item[0]))[:12],
            "sample_files": samples,
        }

    def _read_text(self, path: Path) -> str:
        if path.stat().st_size > 2_000_000:
            raise ValueError("file is larger than the 2 MB text limit")
        return path.read_text(encoding="utf-8", errors="replace")

    def _tree(self, path: Path, depth: int, max_entries: int) -> str:
        rows: list[str] = []
        root_depth = len(path.parts)
        for base, dirnames, filenames in os.walk(path):
            current = Path(base)
            level = len(current.parts) - root_depth
            if level >= depth:
                dirnames[:] = []
            dirnames[:] = [d for d in sorted(dirnames) if d not in IGNORE_NAMES]
            if current != path:
                rows.append("  " * level + current.name + "/")
            for name in sorted(filenames):
                rows.append("  " * (level + 1) + name)
                if len(rows) >= max_entries:
                    return "\n".join(rows) + "\n... result limit reached"
        return "\n".join(rows) or "(empty workspace)"

    def _search(self, query: str, base: Path, pattern: str = "*", regex: bool = False, case_sensitive: bool = False, limit: int = 200) -> str:
        if not query:
            raise ValueError("search query is required")
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(query if regex else re.escape(query), flags)
        matches: list[str] = []
        for path in base.rglob("*"):
            if not path.is_file() or any(part in IGNORE_NAMES for part in path.relative_to(self.root).parts):
                continue
            if not fnmatch.fnmatch(path.name, pattern):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{self.display(path)}:{line_no}:{line[:500]}")
                    if len(matches) >= limit:
                        return "\n".join(matches) + "\n... result limit reached"
        return "\n".join(matches) if matches else "(no matches)"

    def _git(self, args: dict[str, Any]) -> tuple[str, bool]:
        action = str(args.get("action") or "status").lower()
        if action not in {"status", "diff", "log", "show", "branch", "blame"}:
            return "git: only read-only actions are available", True
        cwd = self.resolve(args.get("cwd") or ".", must_exist=True)
        raw_args = args.get("args") or []
        if not isinstance(raw_args, list) or not all(isinstance(x, str) for x in raw_args):
            return "git: args must be a list of strings", True
        command = ["git", "--no-pager", "-c", "core.hooksPath=/dev/null", action, *raw_args[:30]]
        cp = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=60)
        out = (cp.stdout or "") + (cp.stderr or "")
        return (out[:12000] or f"exit_code={cp.returncode}"), cp.returncode != 0

    def _sandbox(self, command: str, cwd: str = ".", timeout: int = 120) -> tuple[str, bool]:
        if not self.writable:
            return "workspace is read-only", True
        work = self.resolve(cwd, must_exist=True)
        rel = work.relative_to(self.root)
        bwrap = shutil.which("bwrap")
        if not bwrap:
            return "bubblewrap (bwrap) is required for local execution", True
        argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
                "--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib"]
        if Path("/lib64").exists():
            argv += ["--ro-bind", "/lib64", "/lib64"]
        argv += ["--bind", str(self.root), "/workspace",
                 "--chdir", "/workspace" + (("/" + rel.as_posix()) if rel.parts else ""),
                 "--setenv", "HOME", "/workspace", "--setenv", "TMPDIR", "/tmp",
                 "/bin/bash", "-o", "pipefail", "-c", command]
        try:
            cp = subprocess.run(argv, text=True, capture_output=True, timeout=max(1, min(int(timeout), 300)))
        except subprocess.TimeoutExpired:
            return f"command timed out after {timeout}s", True
        text = ""
        if cp.stdout:
            text += "stdout:\n" + cp.stdout
        if cp.stderr:
            text += ("\n" if text else "") + "stderr:\n" + cp.stderr
        text += ("\n" if text else "") + f"exit_code={cp.returncode}"
        return text[:12000], cp.returncode != 0

    def execute(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        allowed = WRITE_TOOLS if self.writable else READ_TOOLS
        if tool not in allowed:
            return _result(f"workspace mode {self.mode} blocks tool: {tool}", True)
        try:
            if tool == "workspace_info":
                info = self.analyze()
                return _result(json.dumps(info, ensure_ascii=False, indent=2), False, **info)
            if tool in {"file_read", "code_read"}:
                path = self.resolve(args.get("path"), must_exist=True)
                text = self._read_text(path)
                lines = text.splitlines(keepends=True)
                start = max(1, int(args.get("start_line") or 1))
                end = min(len(lines), int(args.get("end_line") or min(len(lines), start + 499)))
                return _result("".join(lines[start - 1:end]), False, path=self.display(path), total_lines=len(lines))
            if tool in {"file_tree", "code_tree"}:
                path = self.resolve(args.get("path") or ".", must_exist=True)
                depth = max(1, min(int(args.get("depth") or args.get("max_depth") or 4), 8))
                limit = max(1, min(int(args.get("max_entries") or 500), 1000))
                return _result(self._tree(path, depth, limit), False)
            if tool == "code_search":
                path = self.resolve(args.get("path") or ".", must_exist=True)
                return _result(self._search(str(args.get("query") or ""), path, str(args.get("file_pattern") or "*"), bool(args.get("regex")), bool(args.get("case_sensitive")), min(int(args.get("max_results") or 200), 500)), False)
            if tool == "code_grep":
                path = self.resolve(args.get("path") or ".", must_exist=True)
                return _result(self._search(str(args.get("pattern") or ""), path, str(args.get("glob") or "*"), True, True, min(int(args.get("max_results") or 200), 500)), False)
            if tool == "git":
                text, err = self._git(args); return _result(text, err)
            if tool == "directory_create":
                path = self.resolve(args.get("path"), must_exist=False); path.mkdir(parents=True, exist_ok=False); return _result(f"created {self.display(path)}")
            if tool == "workspace_clear":
                if str(args.get("confirm") or "") != "DELETE_ALL":
                    raise ValueError("workspace_clear requires confirm=DELETE_ALL")
                removed = 0
                for child in list(self.root.iterdir()):
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                    removed += 1
                return _result(f"cleared workspace root; removed {removed} top-level entries", False, removed=removed, root_preserved=True)
            if tool == "file_edit":
                path = self.resolve(args.get("path"), must_exist=False)
                operation = str(args.get("operation") or "")
                if operation == "create":
                    if path.exists(): raise FileExistsError(self.display(path))
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(str(args.get("content") or ""), encoding="utf-8")
                elif operation == "write":
                    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(str(args.get("content") or ""), encoding="utf-8")
                elif operation == "append":
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as fh: fh.write(str(args.get("content") or ""))
                elif operation == "replace":
                    text = self._read_text(path)
                    old = str(args.get("old_text") or "")
                    if not old or text.count(old) != 1: raise ValueError("old_text must match exactly once")
                    path.write_text(text.replace(old, str(args.get("new_text") or ""), 1), encoding="utf-8")
                else:
                    raise ValueError("operation must be create, write, append or replace")
                return _result(f"updated {self.display(path)}")
            if tool in {"shell", "task_runner"}:
                text, err = self._sandbox(str(args.get("command") or ""), str(args.get("cwd") or "."), int(args.get("timeout") or (300 if tool == "task_runner" else 120)))
                return _result(text, err)
            if tool == "binary_exec":
                program = str(args.get("program") or "").strip(); arguments = args.get("arguments") or []
                if not program or not isinstance(arguments, list) or not all(isinstance(x, str) for x in arguments): raise ValueError("program and string arguments are required")
                command = " ".join([shlex.quote(program), *(shlex.quote(x) for x in arguments)])
                text, err = self._sandbox(command, str(args.get("work_dir") or "."), int(args.get("timeout") or 120)); return _result(text, err)
            if tool in {"lint", "test"}:
                text, err = self._sandbox(str(args.get("command") or ""), str(args.get("cwd") or "."), 180); return _result(text, err)
        except Exception as exc:
            return _result(f"{tool} error: {exc}", True)
        return _result(f"unsupported workspace tool: {tool}", True)
