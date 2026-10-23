#!/usr/bin/env python3
"""Synchronize Google Chrome's APT repository using HTTP/2.

Google's dl.google.com may stall HTTP/1.1 wget connections. This preserves Chrome
in the public AILinux mirror, validates upstream SHA256 metadata and payloads,
and never publishes a partially downloaded Chrome Packages index.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
from urllib.parse import quote

BASE = 'https://dl.google.com/linux/chrome/deb'
MIRROR_SUBDIR = 'dl.google.com/linux/chrome/deb'
INDEX = 'dists/stable/main/binary-amd64'


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def download(path: str, dest: Path, max_time: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ['curl', '--http2', '--ipv4', '--fail', '--location',
           '--silent', '--show-error', '--retry', '2', '--retry-all-errors',
           '--retry-delay', '1', '--connect-timeout', '12',
           '--max-time', str(max_time), '--output', str(dest),
           f'{BASE}/{quote(path, safe="/")}']
    subprocess.run(cmd, check=True)


def download_ranged_package(path: str, dest: Path, size: int) -> None:
    """Fetch a large Chrome .deb in bounded ranges with end-to-end SHA later.

    Some Google endpoints respond to small ranges but stall on entire files.
    A failed range raises immediately and leaves the published index intact.
    """
    chunk_size = 256 * 1024
    dest.parent.mkdir(parents=True, exist_ok=True)
    chunk_file = dest.with_name(f'.{dest.name}.range')
    try:
        with dest.open('wb') as out:
            for offset in range(0, size, chunk_size):
                end = min(offset + chunk_size, size) - 1
                cmd = ['curl', '--http2', '--ipv4', '--fail', '--location',
                       '--silent', '--show-error', '--retry', '1', '--retry-all-errors',
                       '--connect-timeout', '4', '--max-time', '9',
                       '--range', f'{offset}-{end}', '--output', str(chunk_file),
                       '--write-out', '%{http_code}',
                       f'{BASE}/{quote(path, safe="/")}']
                res = subprocess.run(cmd, check=True, capture_output=True, text=True)
                expected_len = end - offset + 1
                if res.stdout.strip() != '206' or chunk_file.stat().st_size != expected_len:
                    raise RuntimeError(f'Invalid HTTP range response for {path} at {offset}')
                with chunk_file.open('rb') as data:
                    for piece in iter(lambda: data.read(65536), b''):
                        out.write(piece)
                if (offset // chunk_size) % 128 == 127:
                    print(f'[chrome-http2] Downloaded {end + 1}/{size} bytes of {path}', flush=True)
    finally:
        chunk_file.unlink(missing_ok=True)


def expected_sha256(inrelease: str, relative: str) -> tuple[str, int]:
    inside = False
    for line in inrelease.splitlines():
        if line == 'SHA256:':
            inside = True
            continue
        if inside and not line.startswith(' '):
            break
        if inside:
            items = line.split()
            if len(items) == 3 and items[2] == relative:
                return items[0], int(items[1])
    raise RuntimeError(f'No SHA256 entry for {relative} in Google InRelease')


def packages_from_index(data: bytes) -> list[dict[str, str]]:
    parsed = []
    for stanza in data.decode('utf-8').strip().split('\n\n'):
        fields = {}
        for line in stanza.splitlines():
            if not line.startswith(' ') and ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        if not {'Package', 'Filename', 'SHA256', 'Size', 'Architecture'} <= fields.keys():
            raise RuntimeError('Malformed Chrome Packages entry')
        if fields['Architecture'] != 'amd64':
            raise RuntimeError('Unexpected Google Chrome package architecture')
        p = PurePosixPath(fields['Filename'])
        if p.is_absolute() or '..' in p.parts or p.parts[0] != 'pool':
            raise RuntimeError('Unsafe Chrome package Filename: ' + str(p))
        parsed.append(fields)
    if not parsed:
        raise RuntimeError('Empty Google Chrome Packages index')
    return parsed


def verify_payloads(repo: Path, parsed: list[dict[str, str]]) -> tuple[int, int]:
    valid, invalid = 0, 0
    for package in parsed:
        file = repo / package['Filename']
        expected_size = int(package['Size'])
        if file.is_file() and file.stat().st_size == expected_size and digest(file) == package['SHA256']:
            valid += 1
        else:
            invalid += 1
    return valid, invalid


def probe_large_package(path: str, size: int) -> None:
    """Fail quickly if Google's CDN cannot serve bytes near the end of a .deb.

    Some Google edges return the first 256 KiB instantly but stall on larger
    bodies and later offsets. Do not stall the entire two-hour mirror cron.
    """
    if size <= 1024 * 1024:
        return
    lower = size - 65536
    with tempfile.NamedTemporaryFile(prefix='chrome-tail-probe-', delete=True) as sample:
        cmd = ['curl', '--http2', '--ipv4', '--fail', '--location',
               '--silent', '--show-error', '--connect-timeout', '4',
               '--max-time', '9', '--retry', '0',
               '--range', f'{lower}-{size - 1}', '--output', sample.name,
               '--write-out', '%{http_code}', f'{BASE}/{quote(path, safe="/")}']
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        if result.stdout.strip() != '206' or Path(sample.name).stat().st_size != 65536:
            raise RuntimeError(f'Google large-package range probe failed: {path}')


def atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.chrome-staging-', dir=dest.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(data)
        os.chmod(name, 0o644)
        os.replace(name, dest)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mirror-root', required=True, type=Path)
    args = parser.parse_args()
    repo = args.mirror_root / MIRROR_SUBDIR
    index = repo / INDEX
    repo.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix='chrome-http2-') as temp:
            temp = Path(temp)
            download('dists/stable/InRelease', temp / 'InRelease', 45)
            download(f'{INDEX}/Packages.gz', temp / 'Packages.gz', 45)
            compressed = (temp / 'Packages.gz').read_bytes()
            unpacked = gzip.decompress(compressed)
            inrelease = (temp / 'InRelease').read_text(encoding='utf-8')
            for name, data in [('main/binary-amd64/Packages.gz', compressed),
                               ('main/binary-amd64/Packages', unpacked)]:
                expected_hash, size = expected_sha256(inrelease, name)
                if len(data) != size or hashlib.sha256(data).hexdigest() != expected_hash:
                    raise RuntimeError(f'Google Chrome index SHA256 mismatch: {name}')
            parsed = packages_from_index(unpacked)
            valid, invalid = verify_payloads(repo, parsed)
            print(f'[chrome-http2] Verified Google index, {len(parsed)} package(s); {valid} cached, {invalid} need download', flush=True)
            for package in parsed:
                dest = repo / package['Filename']
                if dest.exists() and dest.stat().st_size == int(package['Size']) and digest(dest) == package['SHA256']:
                    continue
                partial = dest.with_name(f'.{dest.name}.{os.getpid()}.partial')
                try:
                    probe_large_package(package['Filename'], int(package['Size']))
                    if int(package['Size']) > 1024 * 1024:
                        download_ranged_package(package['Filename'], partial, int(package['Size']))
                    else:
                        download(package['Filename'], partial, 30)
                    if partial.stat().st_size != int(package['Size']) or digest(partial) != package['SHA256']:
                        raise RuntimeError(f'Chrome package failed SHA256/size check: {package["Filename"]}')
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.chmod(partial, 0o644)
                    os.replace(partial, dest)
                    print(f'[chrome-http2] Downloaded {package["Package"]}', flush=True)
                finally:
                    partial.unlink(missing_ok=True)
            _, invalid = verify_payloads(repo, parsed)
            if invalid:
                raise RuntimeError(f'{invalid} Chrome .deb package(s) failed final verification')
            atomic_write(index / 'Packages', unpacked)
            atomic_write(index / 'Packages.gz', compressed)
            print(f'[chrome-http2] Success: {len(parsed)} current Chrome packages, upstream SHA256 verified', flush=True)
            return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f'[chrome-http2] WARN: Refresh failed ({error}); checking signed mirror cache', file=sys.stderr, flush=True)
        cached = index / 'Packages'
        if cached.is_file() and cached.stat().st_size:
            try:
                parsed = packages_from_index(cached.read_bytes())
                _, invalid = verify_payloads(repo, parsed)
                if not invalid:
                    print('[chrome-http2] WARN: Keeping complete cached Google Chrome package index', file=sys.stderr, flush=True)
                    # Exit 2 signals an intentionally degraded but usable mirror.
                    return 2
            except (OSError, ValueError, RuntimeError):
                pass
        print('[chrome-http2] ERROR: Neither fresh Chrome packages nor a valid cached index are available', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
