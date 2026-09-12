#!/usr/bin/env python3
from pathlib import Path
import sys

if len(sys.argv) != 2:
    raise SystemExit("usage: verify-backend-staging.py PACKAGE_ROOT")

root = Path(sys.argv[1]).resolve()
opt = root / "opt" / "triforce"
errors: list[str] = []

for rel in (
    "config/triforce.env",
    "config/federation_psk.key",
    "config/users.json",
    "config/settings_export.json",
    "config/agents",
):
    p = opt / rel
    if p.exists() or p.is_symlink():
        errors.append(f"forbidden payload: /opt/triforce/{rel}")

bad_dirs = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".cache", ".bun", ".local", ".claude",
}

if opt.exists():
    for p in opt.rglob("*"):
        rel = p.relative_to(root)
        if p.is_dir() and p.name in bad_dirs:
            errors.append(f"forbidden runtime/cache directory: {rel}")
        if p.is_file():
            name = p.name
            if (
                name.endswith((".pyc", ".pyo", ".log"))
                or ".bak" in name
                or name.endswith(".last-good")
                or ".before-" in name
            ):
                errors.append(f"forbidden runtime/backup file: {rel}")
            if name in {".env", "triforce.env", "node.env", "federation_psk.key", "users.json"}:
                errors.append(f"forbidden private/runtime file: {rel}")
            if name == "model_registry.py_20260410_fix":
                errors.append(f"forbidden historical repair copy: {rel}")
        if p.is_symlink():
            target = str(p.readlink())
            if target.startswith("/home/") or target.startswith("/root/"):
                errors.append(f"host-specific symlink: {rel} -> {target}")

etc_unit = root / "etc/systemd/system/triforce.service"
usr_unit = root / "usr/lib/systemd/system/triforce.service"
if etc_unit.exists() or etc_unit.is_symlink():
    errors.append("package must not own /etc/systemd/system/triforce.service")
if not usr_unit.exists():
    errors.append("missing /usr/lib/systemd/system/triforce.service")
else:
    text = usr_unit.read_text(errors="replace")
    if "TRIFORCE_CONFIG_DIR=/opt/triforce/config" in text:
        errors.append("package service still uses mutable config under /opt")
    if "TRIFORCE_CONFIG_DIR=/etc/triforce" not in text:
        errors.append("package service does not use /etc/triforce")

if errors:
    for error in sorted(set(errors)):
        print(f"ERROR: {error}", file=sys.stderr)
    raise SystemExit(1)

print("package staging hygiene: OK")
