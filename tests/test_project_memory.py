from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes import project_memory as project_memory_route
from app.routes.client_auth import get_current_client
from app.services.project_memory import (
    ProjectMemoryStore,
    ProjectMemoryValidationError,
)


def change(entity_id="e1", *, base_version=0, kind="todo", title="Do thing",
           content="later", deleted=False, device_id="dev-a", source="aicoder"):
    return {
        "entity_id": entity_id,
        "kind": kind,
        "title": title,
        "content": content,
        "status": "active",
        "base_version": base_version,
        "deleted": deleted,
        "device_id": device_id,
        "source": source,
    }


@pytest.fixture
def store(tmp_path: Path) -> ProjectMemoryStore:
    return ProjectMemoryStore(tmp_path / "project_memory.db")


def test_insert_update_conflict_tombstone_and_cursor(store):
    first = store.sync(owner_id="owner-a", project_key="project-a", changes=[change()])
    assert first["cursor"] == 1
    assert first["accepted"][0]["version"] == 1
    assert first["accepted"][0]["server_seq"] == 1

    updated = store.sync(
        owner_id="owner-a", project_key="project-a", cursor=first["cursor"],
        changes=[change(base_version=1, content="done")],
    )
    assert updated["accepted"][0]["version"] == 2
    assert updated["remote"][0]["content"] == "done"

    conflict = store.sync(
        owner_id="owner-a", project_key="project-a", cursor=updated["cursor"],
        changes=[change(base_version=1, content="stale writer")],
    )
    assert not conflict["accepted"]
    assert conflict["conflicts"][0]["current"]["version"] == 2
    assert conflict["conflicts"][0]["current"]["content"] == "done"
    assert store.current(owner_id="owner-a", project_key="project-a")[0]["content"] == "done"

    tombstone = store.sync(
        owner_id="owner-a", project_key="project-a", cursor=updated["cursor"],
        changes=[change(base_version=2, content="", deleted=True)],
    )
    assert tombstone["accepted"][0]["version"] == 3
    assert tombstone["accepted"][0]["deleted"] is True
    assert store.current(owner_id="owner-a", project_key="project-a") == []
    current_with_deleted = store.current(
        owner_id="owner-a", project_key="project-a", include_deleted=True
    )
    assert current_with_deleted[0]["deleted"] is True


def test_multiple_changes_pull_and_idempotent_retry(store):
    result = store.sync(
        owner_id="owner", project_key="project",
        changes=[change("a"), change("b", kind="idea", title="Idea")],
    )
    assert len(result["accepted"]) == 2
    assert [row["server_seq"] for row in result["remote"]] == [1, 2]
    assert result["cursor"] == 2

    retry = store.sync(
        owner_id="owner", project_key="project", cursor=result["cursor"],
        changes=[change("a")],
    )
    assert retry["accepted"][0]["idempotent"] is True
    assert retry["accepted"][0]["version"] == 1
    assert retry["cursor"] == 2
    assert retry["remote"] == []

    pulled = store.sync(owner_id="owner", project_key="project", cursor=0, changes=[])
    assert len(pulled["remote"]) == 2


def test_account_and_project_isolation(store):
    store.sync(owner_id="owner-a", project_key="project-a", changes=[change()])
    assert store.current(owner_id="owner-b", project_key="project-a") == []
    assert store.current(owner_id="owner-a", project_key="project-b") == []

    other = store.sync(owner_id="owner-b", project_key="project-a", changes=[change()])
    assert other["accepted"][0]["version"] == 1
    other_project = store.sync(owner_id="owner-a", project_key="project-b", changes=[change()])
    assert other_project["accepted"][0]["version"] == 1


def test_invalid_kind_size_and_secret_rejected(store):
    with pytest.raises(ProjectMemoryValidationError):
        store.sync(
            owner_id="owner", project_key="project",
            changes=[change(kind="chat_transcript")],
        )
    with pytest.raises(ProjectMemoryValidationError):
        store.sync(
            owner_id="owner", project_key="project",
            changes=[change(content="x" * 12001)],
        )
    with pytest.raises(ProjectMemoryValidationError):
        store.sync(
            owner_id="owner", project_key="project",
            changes=[change(content="Authorization: Bearer abcdefghijklmnop")],
        )


def test_conflict_attempt_is_historically_preserved(store):
    store.sync(owner_id="owner", project_key="project", changes=[change()])
    store.sync(
        owner_id="owner", project_key="project",
        changes=[change(base_version=0, content="conflicting")],
    )
    conn = sqlite3.connect(store.path)
    try:
        rows = conn.execute(
            "SELECT accepted, conflict, content FROM project_memory_revisions ORDER BY seq"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, 0, "later"), (0, 1, "conflicting")]


@pytest.mark.asyncio
async def test_claude_mem_failure_is_fail_open(monkeypatch, tmp_path):
    class Settings:
        episodic_memory_enabled = True
        memory_record_enabled = True
        memory_project_id = "aicoder"

    class Provider:
        async def record(self, **kwargs):
            raise RuntimeError("worker down")

    class Engine:
        settings = Settings()
        provider = Provider()

    monkeypatch.setattr(
        "app.services.memory_trigger.get_memory_engine",
        lambda: Engine(),
    )
    await project_memory_route._mirror_accepted("project", [{
        **change(),
        "version": 1,
        "server_seq": 1,
        "updated_at": "now",
    }])


def test_authenticated_route_derives_owner_from_session(monkeypatch, tmp_path):
    store = ProjectMemoryStore(tmp_path / "route.db")
    monkeypatch.setattr(project_memory_route, "get_project_memory_store", lambda: store)

    async def no_mirror(project_key, accepted):
        return None

    monkeypatch.setattr(project_memory_route, "_mirror_accepted", no_mirror)

    app = FastAPI()
    app.include_router(project_memory_route.router, prefix="/v1")

    async def account_a():
        return {"email": "a@example.test", "client_id": "client-a"}

    app.dependency_overrides[get_current_client] = account_a
    client = TestClient(app)

    response = client.post("/v1/project-memory/sync", json={
        "project_key": "project",
        "cursor": 0,
        "owner_id": "attacker-controlled",
        "changes": [change()],
    })
    assert response.status_code == 200
    assert response.json()["accepted"][0]["version"] == 1

    async def account_b():
        return {"email": "b@example.test", "client_id": "client-b"}

    app.dependency_overrides[get_current_client] = account_b
    response_b = client.post("/v1/project-memory/sync", json={
        "project_key": "project",
        "cursor": 0,
        "changes": [],
    })
    assert response_b.status_code == 200
    assert response_b.json()["remote"] == []


def test_route_requires_auth_dependency():
    app = FastAPI()
    app.include_router(project_memory_route.router, prefix="/v1")
    client = TestClient(app)
    response = client.post("/v1/project-memory/sync", json={
        "project_key": "project",
        "cursor": 0,
        "changes": [],
    })
    assert response.status_code == 401
