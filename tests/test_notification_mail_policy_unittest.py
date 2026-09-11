"""Regression tests for Nova mail reply vs. Suggest-Mode routing."""

from app.mcp.notification_manager import (
    SRC_MAIL,
    _mail_requires_suggestion,
    classify_event,
)


def _event(subject: str, body: str, event_type: str):
    return {
        "source": SRC_MAIL,
        "event_type": event_type,
        "title": f"Mail: {subject}",
        "body": body,
        "metadata": {"subject": subject},
    }


def test_story_mail_is_direct_conversation():
    body = "ich moechte testen ob du direkt antworten kannst. poste einfach eine kurze geschichte."
    event_type = classify_event(SRC_MAIL, "erzaehle mir eine geschichte", body)
    assert event_type == "mail.support"
    assert _mail_requires_suggestion(_event("erzaehle mir eine geschichte", body, event_type)) is False


def test_status_overview_mail_is_direct_conversation():
    body = "Nova, kannst du mir einen Statusueberblick geben? Dies ist auch ein Test des Mail-Notifiers."
    event_type = classify_event(SRC_MAIL, "Uebersicht", body)
    assert event_type == "mail.support"
    assert _mail_requires_suggestion(_event("Uebersicht", body, event_type)) is False


def test_explicit_fix_request_uses_suggest_mode():
    body = "Bitte fix den Fehler im Notifier und optimiere die Logik."
    event_type = classify_event(SRC_MAIL, "Bug", body)
    assert event_type == "mail.action"
    assert _mail_requires_suggestion(_event("Bug", body, event_type)) is True


def test_feature_question_can_be_answered_without_auto_change():
    body = "Ich habe eine Feature-Idee. Was haeltst du davon?"
    event_type = classify_event(SRC_MAIL, "Feature Idee", body)
    assert event_type == "support.feature_req"
    assert _mail_requires_suggestion(_event("Feature Idee", body, event_type)) is False


def test_research_mail_stays_in_suggest_mode():
    body = "Bitte Research zur Notifier-Architektur."
    event_type = classify_event(SRC_MAIL, "Research", body, ["research"])
    assert event_type == "mail.research"
    assert _mail_requires_suggestion(_event("Research", body, event_type)) is True


async def test_poller_leader_retries_after_stale_restart_lock(monkeypatch):
    import app.mcp.notification_manager as nm

    class FakeRedis:
        def __init__(self):
            self.set_calls = 0
            self.owner = None

        async def set(self, key, value, nx=False, ex=None):
            self.set_calls += 1
            if self.set_calls == 1:
                return False
            self.owner = value
            return True

        async def get(self, key):
            return "different-owner"

        async def ttl(self, key):
            return 90

        async def expire(self, key, ttl):
            return True

        async def eval(self, script, numkeys, key, owner):
            if self.owner == owner:
                self.owner = None
                return 1
            return 0

    fake_redis = FakeRedis()
    launched = []

    async def fake_get_redis():
        return fake_redis

    async def fast_sleep(_seconds):
        return None

    async def fake_stop_pollers():
        return None

    monkeypatch.setattr(nm, "_get_redis", fake_get_redis)
    monkeypatch.setattr(nm.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(nm, "_launch_pollers", lambda: launched.append(True))
    monkeypatch.setattr(nm, "stop_pollers", fake_stop_pollers)

    await nm._start_pollers_with_lock()

    assert fake_redis.set_calls >= 2
    assert launched == [True]
