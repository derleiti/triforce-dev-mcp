#!/usr/bin/env python3
"""Fail closed when published APT indexes reference absent or corrupted payloads."""
import argparse
import hashlib
from pathlib import Path
import sys

def validate(root, repository=None, max_errors=30):
    repo_roots = [repository] if repository else sorted(p for p in root.iterdir() if p.is_dir())
    checked, missing, corrupt, unsafe, indexes = 0, 0, 0, 0, 0
    cache = {}
    for repo in repo_roots:
        if not repo.is_dir():
            continue
        for idx in sorted(repo.glob("**/dists/*/**/Packages")):
            indexes += 1
            # The APT repository root is the directory containing dists/,
            # not necessarily the initial domain directory.
            parts = idx.parts
            repo_root = Path(*parts[:parts.index("dists")])
            fields = {}
            def check():
                nonlocal checked, missing, corrupt, unsafe
                fn = fields.get("Filename")
                if not fn or not fn.endswith((".deb", ".udeb")):
                    return
                relative = Path(fn)
                if relative.is_absolute() or ".." in relative.parts:
                    unsafe += 1
                    print(f"UNSAFE {idx}: {fn}", file=sys.stderr)
                    return
                sha = fields.get("SHA256")
                size = fields.get("Size")
                if not sha or not size or len(sha) != 64:
                    corrupt += 1
                    print(f"BAD_INDEX {idx}: missing SHA256/Size for {fn}", file=sys.stderr)
                    return
                file = repo_root / relative
                checked += 1
                if not file.is_file():
                    missing += 1
                    if missing + corrupt + unsafe <= max_errors:
                        print(f"MISSING {file}", file=sys.stderr)
                    return
                try:
                    key = (str(file), int(size), sha)
                    if key not in cache:
                        st = file.stat()
                        if st.st_size != int(size):
                            cache[key] = False
                        else:
                            h = hashlib.sha256()
                            with file.open("rb") as inp:
                                for block in iter(lambda: inp.read(4 * 1024 * 1024), b""):
                                    h.update(block)
                            cache[key] = h.hexdigest().lower() == sha.lower()
                    if not cache[key]:
                        corrupt += 1
                        if missing + corrupt + unsafe <= max_errors:
                            print(f"CORRUPT {file}", file=sys.stderr)
                except (OSError, ValueError) as exc:
                    corrupt += 1
                    print(f"ERROR {file}: {exc}", file=sys.stderr)
            try:
                with idx.open(encoding="utf-8", errors="replace") as src:
                    for line in src:
                        if line.strip() == "":
                            check()
                            fields = {}
                        elif line[0] not in " \t" and ":" in line:
                            k, v = line.split(":", 1)
                            if k in ("Filename", "SHA256", "Size"):
                                fields[k] = v.strip()
                    check()
            except OSError as exc:
                corrupt += 1
                print(f"INDEX_ERROR {idx}: {exc}", file=sys.stderr)
    print(f"RESULT indexes={indexes} referenced={checked} missing={missing} corrupt={corrupt} unsafe={unsafe}", flush=True)
    if not indexes:
        print("FAIL no uncompressed Packages indexes found", file=sys.stderr)
        return 2
    return 1 if missing or corrupt or unsafe else 0

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mirror-root", type=Path, required=True)
    p.add_argument("--repository", help="single repository path relative to mirror root")
    p.add_argument("--max-errors", type=int, default=30)
    args = p.parse_args()
    root = args.mirror_root.resolve()
    repo = (root / args.repository).resolve() if args.repository else None
    if repo and not repo.is_relative_to(root):
        p.error("repository outside mirror root")
    return validate(root, repo, args.max_errors)

if __name__ == "__main__":
    sys.exit(main())
