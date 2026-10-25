import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.local_memory_summary import summarize_execution


@pytest.mark.asyncio
async def test_local_summary_uses_loopback_metadata_only_and_reads_back(monkeypatch, tmp_path):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"message": {"content": "Der Agent wurde erfolgreich abgeschlossen."}})

    provider = AsyncMock()
    provider.record.return_value = 77
    provider.get_observations.return_value = [{"id": 77, "project": "triforce:agent:codex"}]
    result = await summarize_execution(
        provider=provider, project="triforce:agent:codex", run_id="safe-test",
        db_path=tmp_path / "summary.sqlite3",
        agent="codex", status="success", task_digest="hash123",
        transport=httpx.MockTransport(handle)
    )
    assert result["status"] == "ok"
    request = requests[0]
    assert request.url.host == "127.0.0.1"
    assert request.url.path == "/api/chat"
    payload = json.loads(request.content)
    assert payload["stream"] is False
    assert payload["options"]["temperature"] == 0
    assert "hash123" in payload["messages"][0]["content"]
    assert provider.record.await_args.kwargs["metadata"]["verification_state"] == "observed"
    provider.get_observations.assert_awaited_once_with([77], "triforce:agent:codex")


@pytest.mark.asyncio
async def test_local_summary_denies_unknown_model_and_nonterminal_state(monkeypatch, tmp_path):
    provider = AsyncMock()
    monkeypatch.setenv("TRIFORCE_LOCAL_MEMORY_MODEL", "gpt-oss:120b-cloud")
    result = await summarize_execution(
        provider=provider, project="x", run_id="r", agent="a",
        db_path=tmp_path / "summary.sqlite3",
        status="success", task_digest="h"
    )
    assert result["reason"] == "model_not_allowlisted"
    monkeypatch.setenv("TRIFORCE_LOCAL_MEMORY_MODEL", "openbmb/minicpm-v4.7:1b")
    result = await summarize_execution(
        provider=provider, project="x", run_id="r", agent="a",
        db_path=tmp_path / "summary.sqlite3",
        status="running", task_digest="h"
    )
    assert result["reason"] == "unverified_terminal_state"
    provider.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistent_claim_prevents_duplicate_after_new_invocation(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"message": {"content": "Status erfolgreich beendet."}})
    provider = AsyncMock()
    provider.record.return_value = 77
    provider.get_observations.return_value = [{"id": 77, "project": "triforce:agent:test"}]
    args = dict(provider=provider, project="triforce:agent:test", run_id="stable-run-1",
                agent="test", status="success", task_digest="abcd",
                transport=httpx.MockTransport(handler), db_path=tmp_path / "summary.sqlite3")
    first = await summarize_execution(**args)
    second = await summarize_execution(**args)
    assert first["status"] == "ok"
    assert second == {"status": "skipped", "reason": "already_claimed_or_busy"}
    assert len(calls) == 1
    assert provider.record.await_count == 1


def test_global_generation_lease_and_expired_recovery(tmp_path):
    import sqlite3
    import time
    from app.services.local_memory_summary import _claim, _transition, LEASE_SECONDS
    path = tmp_path / "journal.db"
    first = _claim(path, "project/run-a")
    assert isinstance(first, str)
    assert _claim(path, "project/run-b") is None
    with sqlite3.connect(path) as db:
        db.execute("UPDATE summary_runs SET claimed_at=? WHERE run_key=?",
                   (int(time.time())-LEASE_SECONDS-10, "project/run-a"))
    # Only expired generation is retryable; completed/persisting writes are not.
    second = _claim(path, "project/run-a")
    assert second and second != first
    _transition(path, "project/run-a", first, "verified", 999)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT state FROM summary_runs").fetchone()[0] == "generating"
    _transition(path, "project/run-a", second, "persisting")
    assert _claim(path, "project/run-a") is None
    assert _claim(path, "project/run-b")


@pytest.mark.asyncio
async def test_failed_model_call_releases_generation_lease(tmp_path):
    from app.services.local_memory_summary import _claim
    path=tmp_path / "journal.db"
    provider=AsyncMock()
    result=await summarize_execution(
        provider=provider,project="p",run_id="r",agent="a",
        status="success",task_digest="h",db_path=path,
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    )
    assert result["reason"] == "local_model_unavailable"
    assert _claim(path, "another-run")
    provider.record.assert_not_awaited()
