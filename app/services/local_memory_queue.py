"""Bounded durable queue for opt-in local memory summaries.

Only summary metadata is queued. One independent worker processes jobs; model
generation must never run in the CLI agent response path.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sqlite3
import time
from pathlib import Path

from .local_memory_summary import DEFAULT_DB, FINAL_STATES, MAX_ATTEMPTS, summarize_execution

logger = logging.getLogger(__name__)
MAX_PENDING = 100
MAX_AGE_SECONDS = 86400
RETRY_SECONDS = 60


def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(path, timeout=5)
    db.execute('PRAGMA busy_timeout=5000')
    db.execute('''CREATE TABLE IF NOT EXISTS summary_queue (
       run_key TEXT PRIMARY KEY, project TEXT NOT NULL, run_id TEXT NOT NULL,
       agent TEXT NOT NULL, status TEXT NOT NULL, task_digest TEXT NOT NULL,
       state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
       created_at INTEGER NOT NULL, available_at INTEGER NOT NULL,
       observation_id INTEGER)''')
    return db


def enqueue(*, project: str, run_id: str, agent: str, status: str,
            task_digest: str, db_path: Path = DEFAULT_DB) -> dict:
    if status not in FINAL_STATES:
        return {'status': 'skipped', 'reason': 'unverified_terminal_state'}
    if not all(isinstance(s, str) and 0 < len(s) <= 180 for s in
               (project, run_id, agent, task_digest)):
        return {'status': 'skipped', 'reason': 'invalid_scope'}
    key = hashlib.sha256((project + '\0' + run_id).encode()).hexdigest()
    now = int(time.time())
    with _connect(db_path) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM summary_queue WHERE run_key=?', (key,)).fetchone():
            return {'status': 'skipped', 'reason': 'already_queued'}
        count = db.execute(
            "SELECT count(*) FROM summary_queue WHERE state IN ('pending','running')"
        ).fetchone()[0]
        if count >= MAX_PENDING:
            return {'status': 'skipped', 'reason': 'queue_full'}
        db.execute('''INSERT INTO summary_queue
          (run_key,project,run_id,agent,status,task_digest,created_at,available_at)
          VALUES (?,?,?,?,?,?,?,?)''',
                   (key, project, run_id, agent, status, task_digest, now, now))
    return {'status': 'queued'}


def _claim_next(path: Path):
    with _connect(path) as db:
        db.execute('BEGIN IMMEDIATE')
        now = int(time.time())
        db.execute("UPDATE summary_queue SET state='expired' WHERE state='pending' AND created_at<?",
                   (now-MAX_AGE_SECONDS,))
        item = db.execute('''SELECT run_key,project,run_id,agent,status,task_digest,attempts
          FROM summary_queue WHERE state='pending' AND available_at<=?
          ORDER BY created_at,run_key LIMIT 1''', (now,)).fetchone()
        if item:
            db.execute("UPDATE summary_queue SET state='running', attempts=attempts+1 WHERE run_key=?",
                       (item[0],))
    return item


def _complete(path: Path, key: str, result: dict):
    now = int(time.time())
    with _connect(path) as db:
        if result.get('status') == 'ok':
            db.execute("UPDATE summary_queue SET state='verified',observation_id=? WHERE run_key=? AND state='running'",
                       (result['observation_id'], key))
        elif result.get('reason') == 'local_model_unavailable':
            attempts = db.execute('SELECT attempts FROM summary_queue WHERE run_key=?', (key,)).fetchone()[0]
            new_state = 'pending' if attempts < MAX_ATTEMPTS else 'failed'
            db.execute('UPDATE summary_queue SET state=?,available_at=? WHERE run_key=? AND state=?',
                       (new_state, now+RETRY_SECONDS, key, 'running'))
        else:
            db.execute("UPDATE summary_queue SET state='needs_review' WHERE run_key=? AND state='running'",
                       (key,))


async def process_one(provider, db_path: Path = DEFAULT_DB) -> dict:
    item = _claim_next(db_path)
    if item is None:
        return {'status': 'idle'}
    key, project, run_id, agent, status, digest, attempts = item
    try:
        result = await summarize_execution(provider=provider, project=project, run_id=run_id,
                                           agent=agent, status=status, task_digest=digest,
                                           db_path=db_path)
    except Exception as exc:
        logger.warning('local memory queue job needs review: %s', type(exc).__name__)
        result = {'status': 'degraded', 'reason': 'unexpected_error'}
    _complete(db_path, key, result)
    return result


def recover_interrupted(db_path: Path = DEFAULT_DB) -> int:
    """Quarantine interrupted writes instead of risking duplicate observations."""
    with _connect(db_path) as db:
        cur = db.execute("UPDATE summary_queue SET state='needs_review' WHERE state='running'")
        return cur.rowcount


async def run_worker(poll_seconds: float = 5.0):
    recover_interrupted()
    from .memory_trigger import get_memory_engine
    engine = get_memory_engine()
    while True:
        await process_one(engine.provider)
        await asyncio.sleep(poll_seconds)


if __name__ == '__main__':
    asyncio.run(run_worker())
