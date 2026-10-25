#!/usr/bin/env python3
"""Read-only GitHub Actions evidence for published TriForce default branch.

Do not mistake startup_failure for a failed job or remote CI for local HEAD.
"""
from __future__ import annotations
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API = 'https://api.github.com/repos/derleiti/triforce-dev-mcp'
TRACKED = {'CI', 'Security', 'pages build and deployment'}


def collect(fetch):
    runs = fetch(API + '/actions/runs?branch=master&per_page=50').get('workflow_runs', [])
    seen = {}
    for r in runs:
        name = r.get('name')
        if name not in TRACKED or name in seen:
            continue
        seen[name] = {k: r.get(k) for k in (
            'id', 'name', 'head_sha', 'created_at', 'updated_at',
            'status', 'conclusion', 'html_url', 'event')}
    if not seen:
        raise ValueError('no expected GitHub workflow results')
    return {'schema': 'future-labs-ci-evidence-v1',
            'checked_at': datetime.now(timezone.utc).isoformat(),
            'repository': 'derleiti/triforce-dev-mcp',
            'scope': 'remote_master_only',
            'runs': list(seen.values()),
            'note': 'startup_failure is not a test failure; missing runs do not prove success'}


def fetch_json(url):
    if not url.startswith(API + '/'):
        raise ValueError('untrusted API URL')
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                              'User-Agent': 'ailinux-future-labs-audit'})
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.load(response)


if __name__ == '__main__':
    print(json.dumps(collect(fetch_json), indent=2, ensure_ascii=False))
