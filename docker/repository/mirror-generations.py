#!/usr/bin/env python3
"""Prepare and atomically switch AILinux mirror generations on a single FS.

The first migration uses Linux renameat2(RENAME_EXCHANGE): the existing
nonempty mirror directory is replaced atomically with a generation symlink.
Subsequent switches use os.replace on symlinks. Never move/copy package bytes.
"""
import argparse
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


def exchange(a: Path, b: Path) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    fn = libc.renameat2
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(-100, os.fsencode(a), -100, os.fsencode(b), 2) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(a), str(b))


def current(repo: Path) -> str:
    live = repo / "mirror"
    if not live.is_symlink():
        raise RuntimeError(f"live mirror must be generation symlink: {live}")
    rel = os.readlink(live)
    target = (repo / rel).resolve(strict=True)
    generations = (repo / "mirror-generations").resolve(strict=True)
    if not target.is_relative_to(generations) or target == generations:
        raise RuntimeError(f"symlink escapes generation directory: {rel}")
    return rel


def bootstrap(repo: Path) -> str:
    live = repo / "mirror"
    generations = repo / "mirror-generations"
    if live.is_symlink():
        return current(repo)
    if not live.is_dir():
        raise RuntimeError("missing initial mirror directory")
    if live.is_mount():
        raise RuntimeError("live mirror is a mount point, refusing rename")
    generations.mkdir(mode=0o755, exist_ok=True)
    token = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    first = generations / ("base-" + token)
    first.mkdir(mode=0o755)
    try:
        # Hardlinks preserve a multi-hundred-GB tree without duplicating bytes.
        subprocess.run(["cp", "-al", str(live) + "/.", str(first)], check=True)
        tmp = repo / (".mirror-exchange-" + token)
        os.symlink(str(first.relative_to(repo)), tmp)
        try:
            exchange(live, tmp)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        # The old directory is now at tmp; preserve it as the first rollback.
        legacy = generations / ("legacy-" + token)
        try:
            os.replace(tmp, legacy)
        except BaseException:
            # The new symlink still points to a verified hardlink snapshot.
            print(f"WARNING: legacy directory remains at {tmp}", file=sys.stderr)
        return current(repo)
    except BaseException:
        if not live.is_symlink():
            # Preserve any partial snapshot for investigation; no live changes.
            print(f"Bootstrap failed; live untouched; partial copy: {first}", file=sys.stderr)
        raise


def switch(repo: Path, generation: str) -> str:
    old = current(repo)
    if generation in ("", ".", "..") or "/" in generation or "\\" in generation:
        raise ValueError("generation must be a single directory name")
    dest = repo / "mirror-generations" / generation
    if not dest.is_dir() or dest.is_symlink():
        raise RuntimeError(f"generation missing: {dest}")
    next_link = repo / (".mirror-next-" + uuid.uuid4().hex)
    os.symlink(str(Path("mirror-generations") / generation), next_link)
    try:
        os.replace(next_link, repo / "mirror")
    finally:
        next_link.unlink(missing_ok=True)
    assert current(repo).endswith("/" + generation)
    return old


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap", "current", "switch"])
    parser.add_argument("--repo-dir", required=True, type=Path)
    parser.add_argument("--generation", help="name under mirror-generations for switch")
    args = parser.parse_args()
    repo = args.repo_dir.resolve(strict=True)
    if args.action == "bootstrap":
        print(bootstrap(repo))
    elif args.action == "current":
        print(current(repo))
    else:
        if not args.generation:
            parser.error("--generation required for switch")
        print("PREVIOUS=" + switch(repo, args.generation))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"mirror-generations: {exc}", file=sys.stderr)
        sys.exit(1)
