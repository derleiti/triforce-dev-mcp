from __future__ import annotations

import asyncio

from app.services import slack_notifications as slack


def test_slack_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SLACK_ENABLED", raising=False)
    result = asyncio.run(slack.deliver_slack_notification({"priority": "critical"}))
    assert result == {"sent": False, "reason": "disabled"}


def test_slack_requires_token_when_enabled(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    result = asyncio.run(slack.deliver_slack_notification({"priority": "critical"}))
    assert result == {"sent": False, "reason": "missing_token"}


def test_low_signal_events_are_filtered(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    event = {"priority": "normal", "event_type": "support.general", "tags": []}
    result = asyncio.run(slack.deliver_slack_notification(event))
    assert result == {"sent": False, "reason": "filtered"}


def test_release_routes_to_release_channel(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_CHANNEL_RELEASES", "ailinux-releases")

    called = {}

    def fake_post(token, channel, text):
        called.update(token=token, channel=channel, text=text)
        return True, "123.456"

    monkeypatch.setattr(slack, "_post_sync", fake_post)
    event = {
        "id": "evt1",
        "priority": "high",
        "event_type": "ops.error",
        "title": "Release failed",
        "body": "Packaging failed",
        "tags": ["release"],
    }
    result = asyncio.run(slack.deliver_slack_notification(event))
    assert result["sent"] is True
    assert result["channel"] == "ailinux-releases"
    assert called["token"] == "xoxb-test"
    assert called["channel"] == "ailinux-releases"
    assert "Release failed" in called["text"]


def test_high_mcp_routes_to_mcp(monkeypatch):
    monkeypatch.setenv("SLACK_ENABLED", "true")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setattr(slack, "_post_sync", lambda token, channel, text: (True, "ok"))
    event = {
        "priority": "high",
        "event_type": "support.general",
        "title": "Pairing regression",
        "tags": ["mcp"],
    }
    result = asyncio.run(slack.deliver_slack_notification(event))
    assert result["sent"] is True
    assert result["channel"] == "mcp"
