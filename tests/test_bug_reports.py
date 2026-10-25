from __future__ import annotations

import json

from app.services import bug_reports


def _use_tmp_db(monkeypatch, tmp_path):
    monkeypatch.setattr(bug_reports, "_DB_PATH", tmp_path / "bugs.sqlite3")


def test_redacts_nested_secrets_and_pair_codes(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda report, report_id, duplicate_count: sent.append(report))
    result = bug_reports.submit_report({
        "app": "Helper",
        "event_type": "crash",
        "exception_type": "RuntimeError",
        "stack": "Authorization: Bearer abcdef\npair_code=AAAA-BBBB-CCCC-DDDD-EEEE-FFFF",
        "metadata": {"token": "super-secret", "nested": {"password": "hunter2", "url": "https://x/?token=abc"}},
        "logs": ["api_key=foobar", "safe"],
    })
    row = bug_reports.get_report(result["report_id"])
    assert row is not None
    serialized = json.dumps(row)
    assert "super-secret" not in serialized
    assert "hunter2" not in serialized
    assert "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF" not in serialized
    assert "foobar" not in serialized
    assert serialized.count("[REDACTED]") >= 4
    assert len(sent) == 1


def test_automatic_duplicate_is_persisted_but_mail_is_suppressed(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda report, report_id, duplicate_count: sent.append(report_id))
    payload = {"app": "AILinux", "event_type": "crash", "exception_type": "Boom", "stack": "same stack"}
    first = bug_reports.submit_report(payload)
    second = bug_reports.submit_report(payload)
    assert first["mail_queued"] is True
    assert second["mail_queued"] is False
    assert len(sent) == 1
    rows = bug_reports.list_reports(limit=10)
    assert len(rows) == 2
    assert rows[0]["mail_status"] == "deduped"


def test_manual_reports_are_never_mail_deduped(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda report, report_id, duplicate_count: sent.append(report_id))
    payload = {"app": "AILinux", "event_type": "manual", "delivery": "manual", "user_message": "button submit"}
    assert bug_reports.submit_report(payload)["mail_queued"] is True
    assert bug_reports.submit_report(payload)["mail_queued"] is True
    assert len(sent) == 2


def test_triage_status_and_stats(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda *args, **kwargs: None)
    result = bug_reports.submit_report({"app": "Helper", "event_type": "selftest", "selftest": {"network": "failed"}})
    update = bug_reports.update_status(result["report_id"], "triaged")
    assert update["ok"] is True
    assert bug_reports.get_report(result["report_id"])["status"] == "triaged"
    aggregate = bug_reports.stats()
    assert aggregate["total"] == 1
    assert aggregate["by_status"]["triaged"] == 1


def test_startup_selftest_checks_sqlite(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    result = bug_reports.startup_selftest()
    assert result["ok"] is True
    assert result["checks"]["sqlite"] is True


def test_global_mail_cap_persists_report_without_mail_storm(monkeypatch, tmp_path):
    _use_tmp_db(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(bug_reports, "_MAX_MAILS_PER_HOUR", 1)
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda report, report_id, duplicate_count: sent.append(report_id))
    first = bug_reports.submit_report({"app": "A", "event_type": "manual", "delivery": "manual", "user_message": "first"})
    second = bug_reports.submit_report({"app": "B", "event_type": "manual", "delivery": "manual", "user_message": "second"})
    assert first["mail_queued"] is True
    assert second["mail_queued"] is False
    assert len(sent) == 1
    row = bug_reports.get_report(second["report_id"])
    assert row is not None
    assert row["mail_status"] == "rate_limited"


def test_configurable_bug_mail_addresses_reject_header_injection(monkeypatch):
    monkeypatch.setenv("BUG_REPORT_RECIPIENT", "bugs@ailinux.me")
    monkeypatch.setenv("BUG_REPORT_SENDER", "submit@ailinux.me")
    assert bug_reports.bug_mail_addresses() == ("bugs@ailinux.me", "submit@ailinux.me")
    monkeypatch.setenv("BUG_REPORT_RECIPIENT", "evil@example.org\nBcc: other@example.org")
    import pytest
    with pytest.raises(ValueError, match="invalid BUG_REPORT_RECIPIENT"):
        bug_reports.bug_mail_addresses()


def test_bug_mail_recipient_and_sender_override(monkeypatch):
    monkeypatch.setenv("BUG_REPORT_RECIPIENT", "triage@ailinux.me")
    monkeypatch.setenv("BUG_REPORT_SENDER", "reporter@ailinux.me")
    messages = []

    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def ehlo(self, *args):
            pass
        def starttls(self, **kwargs):
            pass
        def send_message(self, msg):
            messages.append(msg)

    monkeypatch.setenv("BUG_SMTP_STARTTLS", "false")
    monkeypatch.setattr(bug_reports.smtplib, "SMTP", DummySMTP)
    bug_reports._smtp_send(
        {"app": "Mock", "event_type": "manual", "fingerprint": "abcdef"},
        "test-report-id", 1
    )
    assert messages[0]["To"] == "triage@ailinux.me"
    assert "reporter@ailinux.me" in messages[0]["From"]


def test_verified_bugfix_mirrors_to_scoped_claude_mem(monkeypatch, tmp_path):
    import asyncio
    from unittest.mock import AsyncMock, patch
    from app.mcp import bug_report_tools
    _use_tmp_db(monkeypatch, tmp_path)
    monkeypatch.setattr(bug_reports, "_smtp_send", lambda *args: None)
    monkeypatch.setattr(bug_reports, "_smtp_send_resolution", lambda *args: None)
    created = bug_reports.submit_report({"app": "Demo", "repo": "example-repo", "event_type": "manual"})
    engine = type("Engine", (), {})()
    engine.settings = type("Settings", (), {
        "episodic_memory_enabled": True,
        "memory_record_enabled": True,
        "memory_project_id": "triforce",
    })()
    engine.provider = type("Provider", (), {"record": AsyncMock(return_value=555)})()
    fields = {
        "report_id": created["report_id"],
        "fix_summary": "Repaired startup retry",
        "verification": "pytest regression passed",
        "docs_ref": "docs/changes.md",
    }
    with patch("app.services.memory_trigger.get_memory_engine", return_value=engine):
        response = asyncio.run(bug_report_tools.handle_bug_report_resolve(fields))
        assert response["status"] == "resolved"
        assert response["training_eligible"] is True
        call = engine.provider.record.await_args.kwargs
        assert call["metadata"]["event_type"] == "verified_bugfix"
        assert call["metadata"]["report_id"] == created["report_id"]
        assert call["project"].startswith("triforce:bugfix:")
        assert "logs" not in call["metadata"]
        again = asyncio.run(bug_report_tools.handle_bug_report_resolve(fields))
        assert again["idempotent"] is True
        assert engine.provider.record.await_count == 1
