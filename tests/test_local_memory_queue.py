import asyncio
import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services import local_memory_queue as q


def fields(run_id='run-1'):
    return dict(project='triforce:agent:pilot', run_id=run_id,
                agent='pilot', status='success', task_digest='sha256abcdef')


def test_enqueue_durable_dedupe_and_limit(tmp_path, monkeypatch):
    db = tmp_path / 'queue.sqlite3'
    assert q.enqueue(db_path=db, **fields())['status'] == 'queued'
    assert q.enqueue(db_path=db, **fields())['reason'] == 'already_queued'
    monkeypatch.setattr(q, 'MAX_PENDING', 1)
    assert q.enqueue(db_path=db, **fields('run-2'))['reason'] == 'queue_full'
    assert q._claim_next(db)[1] == 'triforce:agent:pilot'
    assert q._claim_next(db) is None


@pytest.mark.asyncio
async def test_worker_verifies_and_does_not_replay(tmp_path):
    db = tmp_path / 'queue.sqlite3'
    q.enqueue(db_path=db, **fields())
    provider = AsyncMock()
    with patch.object(q, 'summarize_execution',
                      new=AsyncMock(return_value={'status': 'ok', 'observation_id': 88})) as summary:
        assert (await q.process_one(provider, db))['status'] == 'ok'
        assert (await q.process_one(provider, db))['status'] == 'idle'
        assert summary.await_count == 1
    with sqlite3.connect(db) as cx:
        assert cx.execute('SELECT state,observation_id FROM summary_queue').fetchone() == ('verified', 88)


@pytest.mark.asyncio
async def test_local_failure_retries_but_uncertain_write_requires_review(tmp_path):
    db = tmp_path / 'queue.sqlite3'
    q.enqueue(db_path=db, **fields())
    with patch.object(q, 'summarize_execution', new=AsyncMock(return_value={
            'status': 'degraded', 'reason': 'local_model_unavailable'})):
        await q.process_one(AsyncMock(), db)
    with sqlite3.connect(db) as cx:
        assert cx.execute('SELECT state FROM summary_queue').fetchone()[0] == 'pending'
        cx.execute('UPDATE summary_queue SET available_at=0')
    with patch.object(q, 'summarize_execution', new=AsyncMock(side_effect=RuntimeError('uncertain'))):
        await q.process_one(AsyncMock(), db)
    with sqlite3.connect(db) as cx:
        assert cx.execute('SELECT state FROM summary_queue').fetchone()[0] == 'needs_review'


def test_worker_restart_quarantines_uncertain_jobs(tmp_path):
    db = tmp_path / 'queue.sqlite3'
    q.enqueue(db_path=db, **fields())
    assert q._claim_next(db)
    assert q.recover_interrupted(db) == 1
    assert q._claim_next(db) is None
    with sqlite3.connect(db) as cx:
        assert cx.execute('SELECT state FROM summary_queue').fetchone()[0] == 'needs_review'
