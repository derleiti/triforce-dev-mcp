#!/usr/bin/env python3
"""Check every mirrored InRelease signature and Release SHA256-listed file."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import sys


def check(root: Path, key: Path):
    releases = 0
    files = 0
    errors = []
    for signed in sorted(root.glob('**/dists/*/InRelease')):
        releases += 1
        suite = signed.parent
        raw = suite / 'Release'
        sig = suite / 'Release.gpg'
        if not raw.is_file() or not sig.is_file():
            errors.append(f'MISSING_SIGNED_FILES {suite}')
            continue
        for cmd in (['gpgv', '--keyring', str(key), str(signed)],
                    ['gpgv', '--keyring', str(key), str(sig), str(raw)]):
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode:
                errors.append(f'BAD_SIGNATURE {signed}: {result.stderr[-240:]}')
        # The clearsigned InRelease content must be byte-identical to Release:
        # APT consumes InRelease, not the separately signed Release.gpg.
        extracted = subprocess.run(
            ['gpg', '--batch', '--no-default-keyring', '--keyring', str(key),
             '--decrypt', str(signed)], capture_output=True)
        if extracted.returncode or extracted.stdout != raw.read_bytes():
            errors.append(f'INRELEASE_CONTENT_MISMATCH {signed}')
        inside = False
        entries = 0
        for line in raw.read_text(errors='replace').splitlines():
            if line.strip() == 'SHA256:':
                inside = True
                continue
            if inside and line and not line[0].isspace():
                inside = False
            if not inside or not line.strip():
                continue
            bits = line.split()
            if len(bits) != 3 or len(bits[0]) != 64:
                continue
            sha, expected_size, rel = bits
            relative = Path(rel)
            if relative.is_absolute() or '..' in relative.parts:
                errors.append(f'UNSAFE_INDEX {signed}: {rel}')
                continue
            target = suite / relative
            files += 1
            entries += 1
            try:
                if not target.is_file() or target.stat().st_size != int(expected_size):
                    errors.append(f'MISSING_OR_BAD_SIZE {target}')
                    continue
                with target.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != sha.lower():
                    errors.append(f'BAD_HASH {target}')
            except (OSError, ValueError) as exc:
                errors.append(f'INDEX_ERROR {target}: {exc}')
        if not entries:
            errors.append(f'NO_RELEASE_HASHES {raw}')
    if not releases:
        errors.append('NO_SIGNED_REPOSITORIES')
    for err in errors[:30]:
        print(err, file=sys.stderr)
    print(f'SIGNED_METADATA_RESULT releases={releases} indexed_files={files} errors={len(errors)}')
    return 0 if not errors else 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--mirror-root', required=True, type=Path)
    p.add_argument('--keyring', required=True, type=Path)
    args = p.parse_args()
    root = args.mirror_root.resolve(strict=True)
    key = args.keyring.resolve(strict=True)
    return check(root, key)


if __name__ == '__main__':
    sys.exit(main())
