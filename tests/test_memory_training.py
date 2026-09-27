from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services import bug_reports
from app.services.memory_training import training_digest


class Provider:
    def __init__(self):
        self.settings = SimpleNamespace(memory_max_results=4)
        nested = {
            "schema": "aicoder-teamrun-memory-v1",
            "task": "fix parser safely",
            "checkpoint": "run_completed",
            "completed_stages": ["research", "code", "tests_function_ok", "atomic_disk_write"],
            "status": "completed",
        }
        self.rows = {
            1: {
                "id": 1,
                "project": "triforce:project:repo:abc",
                "title": "project_memory_revision: parser",
                "narrative": json.dumps({"title": "Parser teamrun", "content": json.dumps(nested), "status": "completed", "deleted": False}),
                "metadata": {
                    "event_type": "project_memory_revision", "entity_id": "team-1",
                    "version": 4, "server_seq": 10, "source": "aicoder-teamrun",
                    "verification_state": "accepted", "deleted": False,
                },
            },
            2: {
                "id": 2,
                "project": "triforce:repo",
                "title": "run_completed: parser",
                "metadata": {
                    "event_type": "run_completed", "verification_state": "completed",
                    "task": "parser", "findings": "pytest passed", "agent": "aicoder", "model": "test/model",
                },
            },
            3: {
                "id": 3,
                "project": "triforce:repo",
                "title": "tool_failed: parser",
                "metadata": {
                    "event_type": "tool_failed", "verification_state": "failed",
                    "tool": "code_edit", "error_type": "SyntaxError", "error": "invalid syntax",
                },
            },
        }

    async def search_global(self, query: str, limit: int):
        mapping = {
            "project_memory_revision": [1],
            "run_completed": [2],
            "tool_failed": [3],
        }
        return [
            {k: v for k, v in self.rows[i].items() if k in {"id", "project", "title", "metadata"}}
            for i in mapping.get(query, [])
        ]

    async def get_observations(self, ids, project):
        return [self.rows[i] for i in ids if self.rows[i]["project"] == project]


@pytest.mark.asyncio
async def test_training_digest_distills_only_evidence_backed_signals(monkeypatch):
    monkeypatch.setattr(bug_reports, "list_training_resolutions", lambda limit=100: [{
        "id": "bug-1", "created_at": "2026-09-27T00:00:00Z", "resolved_at": "2026-09-27T01:00:00Z",
        "app": "Copa OCR", "repo": "copa", "version": "2.2.15", "event_type": "crash",
        "fingerprint": "fp1", "duplicate_count": 3,
        "fix_summary": "Use portal selection", "fix_version": "2.2.16", "fix_commit": "abc123",
        "verification": "tests + manual Wayland pass", "docs_ref": "CHANGELOG.md#2.2.16",
    }])
    digest = await training_digest(engine=SimpleNamespace(provider=Provider()), limit=50)

    assert digest["mode"] == "read_only_distillation"
    assert digest["automatic_promotion"] is False
    assert digest["source_counts"] == {"episodic_selected": 3, "documented_resolved_bugs": 1}
    kinds = {item["kind"] for item in digest["best_practices"]}
    assert {"verified_workflow", "verified_bugfix"} <= kinds
    assert digest["successful_runs"][0]["findings"] == "pytest passed"
    assert digest["anti_patterns"][0]["tool"] == "code_edit"
    regression_kinds = {item["kind"] for item in digest["regression_candidates"]}
    assert {"workflow_regression", "bug_regression"} <= regression_kinds


def test_verified_bug_resolution_is_archived_and_training_eligible(monkeypatch, tmp_path):
    monkeypatch.setattr(bug_reports, "_DB_PATH", tmp_path / "bugs.sqlite3")
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda *args, **kwargs: None)
    archived = []
    monkeypatch.setattr(bug_reports, "_smtp_send_resolution", lambda report: archived.append(dict(report)))

    created = bug_reports.submit_report({
        "app": "Copa OCR", "repo": "copa", "version": "2.2.15",
        "event_type": "manual", "delivery": "manual", "user_message": "selection failed",
    })
    result = bug_reports.resolve_report(
        created["report_id"],
        fix_summary="Use the XDG portal selection path on Wayland.",
        verification="pytest passed; manual KDE Wayland selection passed",
        docs_ref="CHANGELOG.md#2.2.16",
        fix_version="2.2.16",
        fix_commit="abc123",
    )
    assert result["training_eligible"] is True
    assert result["resolution_mail_status"] == "sent"
    row = bug_reports.get_report(created["report_id"])
    assert row["status"] == "resolved"
    assert row["fix_version"] == "2.2.16"
    assert row["docs_ref"] == "CHANGELOG.md#2.2.16"
    assert len(archived) == 1
    assert bug_reports.list_training_resolutions()[0]["fingerprint"] == row["fingerprint"]

    second = bug_reports.resolve_report(
        created["report_id"],
        fix_summary="Use the XDG portal selection path on Wayland.",
        verification="pytest passed; manual KDE Wayland selection passed",
        docs_ref="CHANGELOG.md#2.2.16",
        fix_version="2.2.16",
        fix_commit="abc123",
    )
    assert second["idempotent"] is True
    assert len(archived) == 1


def test_bug_resolution_requires_documented_verification(monkeypatch, tmp_path):
    monkeypatch.setattr(bug_reports, "_DB_PATH", tmp_path / "bugs.sqlite3")
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda *args, **kwargs: None)
    created = bug_reports.submit_report({"app": "AICoder", "event_type": "manual", "delivery": "manual"})
    with pytest.raises(ValueError, match="docs_ref"):
        bug_reports.resolve_report(
            created["report_id"], fix_summary="fix", verification="pytest passed", docs_ref=""
        )


def test_runtime_policy_closes_bug_and_training_loop():
    from app.mcp.agent_instructions import build_runtime_policy

    prompt = build_runtime_policy("mcp")
    assert "bug_report_resolve" in prompt
    assert "memory_training" in prompt
    assert "bugs@ailinux.me" in prompt


def test_training_and_bug_resolution_tools_are_admin_scoped():
    from app.mcp.tool_registry_unified import get_canonical_all_tools, tool_scope

    tools = {tool["name"]: tool for tool in get_canonical_all_tools()}
    assert tools["memory_training"]["annotations"]["readOnlyHint"] is True
    assert tools["bug_report_resolve"]["annotations"]["readOnlyHint"] is False
    assert tool_scope("memory_training", tools["memory_training"].get("x_inventory", "")) == "triforce_admin"
    assert tool_scope("bug_report_resolve", tools["bug_report_resolve"].get("x_inventory", "")) == "triforce_admin"
