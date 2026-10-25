#!/usr/bin/env python3
"""Read-only, deterministic Future Labs snapshot from the local Claude-Mem API.

Do not treat discussion observations as verified technical decisions.
"""
from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:37777"
SOURCE_PROJECT = "triforce:account:admin@ailinux.me"
SOURCE_IDS = tuple(range(19, 38))


def select(rows):
    indexed = {row["id"]: row for row in rows if type(row.get("id")) is int}
    out = []
    for source_id in SOURCE_IDS:
        row = indexed.get(source_id)
        if not row or row.get("project") != SOURCE_PROJECT:
            raise ValueError(f"missing or out-of-scope observation {source_id}")
        title = str(row.get("title") or "")
        if "Future Lab" not in title:
            raise ValueError(f"unexpected title for observation {source_id}")
        body = str(row.get("narrative") or "")
        out.append({
            "observation_id": source_id,
            "created_at": row.get("created_at"),
            "title": title.splitlines()[0][:180],
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        })
    return out


def snapshot():
    with urllib.request.urlopen(BASE + "/api/observations?limit=100", timeout=8) as response:
        value = json.load(response)
    if value.get("hasMore"):
        raise ValueError("incomplete source window")
    return select(value["items"])


def main():
    entries = snapshot()
    print(json.dumps({
        "schema": "ailinux-future-labs-sources-v1",
        "source": "claude-mem",
        "source_project": SOURCE_PROJECT,
        "observations": entries,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
