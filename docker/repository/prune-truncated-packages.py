#!/usr/bin/env python3
"""Delete mismatched-size mirrored artifacts from COW staging only.

apt-mirror/wget sometimes leaves a partial file with a good timestamp and then
skips it on later runs. Removing just the stage view creates overlay whiteouts;
the published lower generation is never modified.
"""
import argparse
import hashlib
from pathlib import Path
import sys


def prune(root: Path) -> int:
    removed, examined, unsafe = 0, 0, 0
    # Debug symbols are a manageable subset and can be silently corrupted
    # without a size mismatch. Rehash these before apt-mirror decides to skip.
    hashed_debug = 0
    seen = set()
    for idx in root.glob('**/dists/*/**/Packages'):
        parts = idx.parts
        repo = Path(*parts[:parts.index('dists')])
        stanza = {}
        def visit():
            nonlocal removed, examined, unsafe, hashed_debug
            raw = stanza.get('Filename', '')
            if not raw.endswith(('.deb', '.udeb', '.ddeb')):
                return
            rel = Path(raw)
            if rel.is_absolute() or '..' in rel.parts:
                unsafe += 1
                print('UNSAFE_FILENAME',raw,file=sys.stderr)
                return
            f = repo / rel
            if f in seen:
                return
            seen.add(f)
            size = stanza.get('Size', '')
            try:
                expected = int(size)
                if expected <= 0:
                    raise ValueError('invalid package size')
                if f.is_symlink():
                    unsafe += 1
                    print('UNSAFE_SYMLINK',f,file=sys.stderr)
                    return
                if not f.is_file():
                    return
                examined += 1
                actual = f.stat().st_size
                if actual != expected:
                    print('QUARANTINE_TRUNCATED',f.relative_to(root),f'{actual}/{expected}')
                    f.unlink()
                    removed += 1
                elif f.suffix == '.ddeb' and len(stanza.get('SHA256', '')) == 64:
                    hashed_debug += 1
                    digest = hashlib.sha256()
                    with f.open('rb') as reader:
                        for chunk in iter(lambda: reader.read(4 * 1024 * 1024), b''):
                            digest.update(chunk)
                    if digest.hexdigest().lower() != stanza['SHA256'].lower():
                        print('QUARANTINE_BAD_SHA256', f.relative_to(root))
                        f.unlink()
                        removed += 1
            except (ValueError,OSError) as err:
                unsafe += 1
                print('INVALID_RECORD',raw,err,file=sys.stderr)
        try:
            with idx.open(encoding='utf8',errors='replace') as reader:
                for line in reader:
                    if line.strip() == '':
                        visit()
                        stanza={}
                    elif line[0] not in ' \t' and ': ' in line:
                        k,v=line.split(': ',1)
                        if k in ('Filename','Size','SHA256'):
                            stanza[k]=v.strip()
                visit()
        except OSError as exc:
            unsafe += 1
            print('UNREADABLE_INDEX',idx,exc,file=sys.stderr)
    print(f'STAGING_PREFLIGHT examined={examined} debug_hashed={hashed_debug} removed={removed} errors={unsafe}')
    return 1 if unsafe else 0


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mirror-root',type=Path,required=True)
    args=p.parse_args()
    return prune(args.mirror_root.resolve(strict=True))

if __name__=='__main__':
    sys.exit(main())
