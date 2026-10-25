"""Local-only status summaries for CLI agents with durable, conservative deduplication.

Do not send prompts or agent outputs to models. Failed or ambiguous writes are
not retried automatically; an operator must inspect before clearing a lease.
"""
from __future__ import annotations

import hashlib
import secrets
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

from .episodic_memory import bounded_text, redact

LOCAL_URL = "http://127.0.0.1:11434"
ALLOWED_MODELS = frozenset({"openbmb/minicpm-v4.7:1b"})
FINAL_STATES = frozenset({"success", "completed", "ok", "error", "failed", "timeout", "quota_exhausted"})
LEASE_SECONDS = 120
MAX_ATTEMPTS = 3
DEFAULT_DB = Path("/home/zombie/.local/share/triforce/local-memory-summaries.sqlite3")


def _claim(db_path: Path, key: str) -> str | None:
    """Atomically admit one active generator across all TriForce processes."""
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    now = int(time.time())
    with sqlite3.connect(db_path, timeout=5) as db:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""CREATE TABLE IF NOT EXISTS summary_runs (
            run_key TEXT PRIMARY KEY, claimed_at INTEGER NOT NULL,
            state TEXT NOT NULL, observation_id INTEGER)""")
        cols = {r[1] for r in db.execute("PRAGMA table_info(summary_runs)")}
        if "attempts" not in cols:
            db.execute("ALTER TABLE summary_runs ADD COLUMN attempts INTEGER NOT NULL DEFAULT 1")
        if "lease_token" not in cols:
            db.execute("ALTER TABLE summary_runs ADD COLUMN lease_token TEXT")
        db.execute("BEGIN IMMEDIATE")
        busy = db.execute("""SELECT COUNT(*) FROM summary_runs
            WHERE state='generating' AND claimed_at>?""", (now-LEASE_SECONDS,)).fetchone()[0]
        if busy:
            return None
        row = db.execute("SELECT state,claimed_at,attempts FROM summary_runs WHERE run_key=?", (key,)).fetchone()
        if row:
            state, claimed, attempts = row
            if state not in ("generating", "retryable") or attempts >= MAX_ATTEMPTS:
                return None
            if state == "generating" and claimed > now-LEASE_SECONDS:
                return None
            db.execute("""UPDATE summary_runs SET state='generating',claimed_at=?,
                attempts=?,lease_token=? WHERE run_key=?""", (now,attempts+1,token,key))
        else:
            db.execute("""INSERT INTO summary_runs
                (run_key,claimed_at,state,attempts,lease_token) VALUES (?,?,'generating',1,?)""",
                (key,now,token))
    return token


def _transition(db_path: Path, key: str, token: str, state: str, observation_id=None):
    with sqlite3.connect(db_path,timeout=5) as db:
        db.execute("""UPDATE summary_runs SET state=?,observation_id=?
            WHERE run_key=? AND lease_token=?""", (state,observation_id,key,token))


async def summarize_execution(*, provider: Any, project: str, run_id: str,
                              agent: str, status: str, task_digest: str,
                              transport=None, db_path: Path | None = None) -> dict:
    """Generate a bounded local observation once, keyed by project and run."""
    model = os.getenv("TRIFORCE_LOCAL_MEMORY_MODEL", "openbmb/minicpm-v4.7:1b")
    if model not in ALLOWED_MODELS:
        return {"status": "skipped", "reason": "model_not_allowlisted"}
    if status not in FINAL_STATES:
        return {"status": "skipped", "reason": "unverified_terminal_state"}
    if not (project and run_id and agent and task_digest):
        return {"status": "skipped", "reason": "missing_scope"}
    key = hashlib.sha256((project + "\0" + run_id).encode()).hexdigest()
    path = db_path or DEFAULT_DB
    token = _claim(path, key)
    if token is None:
        return {"status": "skipped", "reason": "already_claimed_or_busy"}
    evidence = {"agent": bounded_text(agent, 100), "status": status,
                "task_digest": bounded_text(task_digest, 100),
                "run_id": bounded_text(run_id, 100), "content_policy": "metadata_only"}
    instruction = ("Write a SINGLE factual German sentence summarizing the following execution metadata. "
                   "Do not infer the task, fixes, tests, or success beyond status. "
                   "Do not generate markdown or JSON. Metadata: " + json.dumps(evidence, sort_keys=True))
    try:
        result = await _generate(instruction, model, transport)
    except (httpx.TimeoutException, httpx.ConnectError):
        _transition(path,key,token,"retryable")
        return {"status": "degraded", "reason": "local_model_unavailable"}
    answer = result
    if not isinstance(answer, str) or not answer.strip():
        _transition(path,key,token,"retryable")
        return {"status": "skipped", "reason": "empty_model_response"}
    _transition(path,key,token,"persisting")
    summary = bounded_text(redact(answer.strip()), 600)
    return await _persist(provider,project,run_id,agent,status,task_digest,model,summary,path,key,token)


async def _generate(instruction, model, transport):
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=3), trust_env=False,
                                 transport=transport) as client:
        response = await client.post(LOCAL_URL + "/api/chat", json={
            "model": model, "messages": [{"role": "user", "content": instruction}],
            "stream": False, "options": {"temperature": 0, "num_predict": 96},
            "keep_alive": "1m",
        })
        response.raise_for_status()
        body = response.json()
    return body.get("message", {}).get("content", "")


async def _persist(provider, project, run_id, agent, status, task_digest, model, summary, path, key, token):
    metadata = {"event_type": "local_session_summary", "run_id": run_id,
                "agent": bounded_text(agent, 100), "verification_state": "observed",
                "model": model, "source": "triforce-local-ollama",
                "task_digest": task_digest, "status": status,
                "digest": hashlib.sha256(summary.encode()).hexdigest()[:20]}
    memory_id = await provider.record(text=summary,
                                      title=f"local_session_summary: {agent[:85]} {status}",
                                      project=project, metadata=metadata)
    rows = await provider.get_observations([memory_id], project)
    if not any(row.get("id") == memory_id for row in rows):
        return {"status": "degraded", "reason": "readback_mismatch"}
    _transition(path, key, token, "verified", memory_id)
    return {"status": "ok", "observation_id": memory_id, "model": model}
