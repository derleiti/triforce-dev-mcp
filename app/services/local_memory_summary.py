"""Local-only status summaries for CLI agents with durable, conservative deduplication.

Do not send prompts or agent outputs to models. Failed or ambiguous writes are
not retried automatically; an operator must inspect before clearing a lease.
"""
from __future__ import annotations

import hashlib
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
DEFAULT_DB = Path("/home/zombie/.local/share/triforce/local-memory-summaries.sqlite3")


def _claim(db_path: Path, key: str) -> bool:
    db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with sqlite3.connect(db_path, timeout=5) as db:
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("""CREATE TABLE IF NOT EXISTS summary_runs (
            run_key TEXT PRIMARY KEY, claimed_at INTEGER NOT NULL,
            state TEXT NOT NULL, observation_id INTEGER)""")
        result = db.execute(
            "INSERT OR IGNORE INTO summary_runs(run_key, claimed_at, state) VALUES (?,?,?)",
            (key, int(time.time()), "claimed"))
        return result.rowcount == 1


def _complete(db_path: Path, key: str, observation_id: int) -> None:
    with sqlite3.connect(db_path, timeout=5) as db:
        db.execute("UPDATE summary_runs SET state='verified',observation_id=? WHERE run_key=?",
                   (observation_id, key))


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
    if not _claim(path, key):
        return {"status": "skipped", "reason": "already_claimed"}
    evidence = {"agent": bounded_text(agent, 100), "status": status,
                "task_digest": bounded_text(task_digest, 100),
                "run_id": bounded_text(run_id, 100), "content_policy": "metadata_only"}
    instruction = ("Write a SINGLE factual German sentence summarizing the following execution metadata. "
                   "Do not infer the task, fixes, tests, or success beyond status. "
                   "Do not generate markdown or JSON. Metadata: " + json.dumps(evidence, sort_keys=True))
    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=3), trust_env=False,
                                 transport=transport) as client:
        response = await client.post(LOCAL_URL + "/api/chat", json={
            "model": model, "messages": [{"role": "user", "content": instruction}],
            "stream": False, "options": {"temperature": 0, "num_predict": 96},
            "keep_alive": "1m",
        })
        response.raise_for_status()
        body = response.json()
    answer = body.get("message", {}).get("content", "")
    if not isinstance(answer, str) or not answer.strip():
        return {"status": "skipped", "reason": "empty_model_response"}
    summary = bounded_text(redact(answer.strip()), 600)
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
    _complete(path, key, memory_id)
    return {"status": "ok", "observation_id": memory_id, "model": model}
