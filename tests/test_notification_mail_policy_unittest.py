"""Regression tests for Nova mail reply vs. Suggest-Mode routing."""

import asyncio

import pytest

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


@pytest.fixture
def poller_manager(monkeypatch):
    import app.mcp.notification_manager as nm

    monkeypatch.setattr(nm, "_poller_tasks", [])
    monkeypatch.setattr(nm, "_poller_status", {})
    monkeypatch.setattr(nm, "_redis", None)
    monkeypatch.setattr(nm, "_poller_leader_task", None)
    monkeypatch.setattr(nm, "_POLLER_RETRY_INTERVAL", 0.01)
    monkeypatch.setattr(nm, "_POLLER_REFRESH_INTERVAL", 0.01)
    return nm


class FakeRedis:
    def __init__(self, nm):
        self.nm = nm
        self.owner = None
        self.set_calls = 0
        self.renewed = asyncio.Event()
        self.fail_renew = False

    async def set(self, key, value, nx=False, ex=None):
        assert key == self.nm._POLLER_LOCK_KEY
        assert nx and ex == self.nm._POLLER_LOCK_TTL
        self.set_calls += 1
        if self.owner is not None:
            return False
        self.owner = value
        return True

    async def eval(self, script, numkeys, key, owner, *args):
        assert numkeys == 1 and key == self.nm._POLLER_LOCK_KEY
        if script == self.nm._POLLER_RENEW_SCRIPT:
            assert args == (self.nm._POLLER_LOCK_TTL,)
            if self.fail_renew:
                raise ConnectionError("redis disconnected")
            if self.owner != owner:
                return 0
            self.renewed.set()
            return 1
        assert script == self.nm._POLLER_RELEASE_SCRIPT and not args
        if self.owner != owner:
            return 0
        self.owner = None
        return 1


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.005)


async def test_poller_leader_retries_after_stale_restart_lock(monkeypatch, poller_manager):
    nm = poller_manager
    r = FakeRedis(nm)
    r.owner = "stale"
    launched = []
    async def get_redis():
        return r
    monkeypatch.setattr(nm, "_get_redis", get_redis)
    monkeypatch.setattr(nm, "_launch_pollers", lambda: launched.append(r.owner))
    nm.start_pollers()
    first = nm._poller_leader_task
    nm.start_pollers()
    assert nm._poller_leader_task is first
    try:
        await eventually(lambda: r.set_calls >= 2)
        assert launched == []
        r.owner = None
        await asyncio.wait_for(r.renewed.wait(), 2)
        assert len(launched) == 1 and launched[0] == r.owner
    finally:
        await nm.stop_pollers()
    assert r.owner is None and first.done()
    assert nm._poller_leader_task is None


async def test_poller_waits_for_redis_without_launching_unlocked(monkeypatch, poller_manager):
    nm = poller_manager
    r = FakeRedis(nm)
    available = False
    launched = []
    async def get_redis():
        return r if available else None
    monkeypatch.setattr(nm, "_get_redis", get_redis)
    monkeypatch.setattr(nm, "_launch_pollers", lambda: launched.append(True))
    nm.start_pollers()
    try:
        await eventually(lambda: nm._poller_status.get("leader") == "waiting:redis-unavailable")
        assert not launched
        available = True
        await eventually(lambda: bool(launched))
        assert launched == [True]
    finally:
        await nm.stop_pollers()


async def test_poller_stops_on_lease_loss_and_reacquires(monkeypatch, poller_manager):
    nm = poller_manager
    r = FakeRedis(nm)
    started = []
    stopped = []
    async def get_redis():
        return r
    async def poller(owner):
        try:
            started.append(owner)
            await asyncio.Future()
        finally:
            stopped.append(owner)
    def launch():
        nm._poller_tasks.append(asyncio.create_task(poller(r.owner)))
    monkeypatch.setattr(nm, "_get_redis", get_redis)
    monkeypatch.setattr(nm, "_launch_pollers", launch)
    nm.start_pollers()
    try:
        await eventually(lambda: len(started) == 1)
        first = started[0]
        r.owner = "new-leader"
        await eventually(lambda: stopped == [first])
        assert r.owner == "new-leader"
        assert not nm._poller_tasks
        r.owner = None
        await eventually(lambda: len(started) == 2)
        assert started[1] != first
    finally:
        await nm.stop_pollers()
    assert len(stopped) == 2 and r.owner is None


async def test_poller_recovers_after_renewal_error(monkeypatch, poller_manager):
    nm = poller_manager
    r = FakeRedis(nm)
    launched = []
    async def get_redis():
        return r
    monkeypatch.setattr(nm, "_get_redis", get_redis)
    monkeypatch.setattr(nm, "_launch_pollers", lambda: launched.append(r.owner))
    nm.start_pollers()
    try:
        await asyncio.wait_for(r.renewed.wait(), 2)
        r.fail_renew = True
        await eventually(lambda: nm._poller_status.get("leader") == "waiting:redis-error")
        r.fail_renew = False
        await eventually(lambda: len(launched) >= 2)
        assert launched[1] != launched[0]
    finally:
        await nm.stop_pollers()


async def test_poller_shutdown_while_waiting_cancels_supervisor(monkeypatch, poller_manager):
    nm = poller_manager
    async def unavailable():
        return None
    monkeypatch.setattr(nm, "_get_redis", unavailable)
    monkeypatch.setattr(nm, "_launch_pollers", lambda: pytest.fail("unlocked launch"))
    nm.start_pollers()
    task = nm._poller_leader_task
    await eventually(lambda: nm._poller_status.get("leader") == "waiting:redis-unavailable")
    await nm.stop_pollers()
    assert task.done() and nm._poller_leader_task is None
    assert nm._poller_status["leader"] == "stopped"


async def test_notification_redis_uses_configured_endpoint(monkeypatch, poller_manager):
    from types import SimpleNamespace
    import app.config
    import redis.asyncio

    nm = poller_manager
    calls = []

    class Client:
        available = False
        closed = False

        async def ping(self):
            await asyncio.sleep(0)
            if not self.available:
                raise ConnectionError("temporary outage")
            return True

        async def aclose(self):
            self.closed = True

    client = Client()

    def from_url(url, **kwargs):
        calls.append((url, kwargs))
        return client

    monkeypatch.setattr(
        app.config, "get_settings",
        lambda: SimpleNamespace(redis_url="redis://redis.test:6380/4"),
    )
    monkeypatch.setattr(redis.asyncio, "from_url", from_url)
    assert await asyncio.gather(nm._get_redis(), nm._get_redis()) == [None, None]
    client.available = True
    assert await asyncio.gather(nm._get_redis(), nm._get_redis()) == [client, client]
    assert len(calls) == 1
    assert calls[0][0] == "redis://redis.test:6380/4"
    assert calls[0][1]["socket_connect_timeout"] == 3
    await nm.stop_pollers()
    assert client.closed and nm._redis is None


def test_flarum_configured_checks_canonical_config(monkeypatch):
    import app.mcp.flarum_tools as ft

    monkeypatch.setattr(ft, "_env_value", lambda name: "token" if name == "FLARUM_TOKEN" else None)
    assert ft.is_flarum_configured() is True

    monkeypatch.setattr(ft, "_env_value", lambda _name: None)
    assert ft.is_flarum_configured() is False


def test_launch_pollers_skips_forum_without_auth(monkeypatch):
    import app.mcp.flarum_tools as ft
    import app.mcp.notification_manager as nm

    launched = []

    def fake_create_task(coro):
        launched.append(coro.cr_code.co_name)
        coro.close()

        class DummyTask:
            def set_name(self, _name):
                pass

        return DummyTask()

    monkeypatch.setattr(ft, "is_flarum_configured", lambda: False)
    monkeypatch.setattr(nm.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(nm, "_poller_tasks", [])
    monkeypatch.setattr(nm, "_poller_status", {})

    nm._launch_pollers()

    assert "_poll_forum" not in launched
    assert nm._poller_status["forum"] == "disabled:no-auth"


def test_launch_pollers_skips_mail_without_imap_auth(monkeypatch):
    import app.config as config
    import app.mcp.flarum_tools as ft
    import app.mcp.notification_manager as nm

    launched = []

    class MailSettings:
        notification_external_pollers_enabled = True
        mail_imap_host = ""
        mail_imap_user = ""
        mail_imap_pass = ""

    def fake_create_task(coro):
        launched.append(coro.cr_code.co_name)
        coro.close()

        class DummyTask:
            def set_name(self, _name):
                pass

        return DummyTask()

    monkeypatch.setattr(config, "get_settings", lambda: MailSettings())
    monkeypatch.setattr(ft, "is_flarum_configured", lambda: False)
    monkeypatch.setattr(nm.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(nm, "_poller_tasks", [])
    monkeypatch.setattr(nm, "_poller_status", {})

    nm._launch_pollers()

    assert "_poll_mail" not in launched
    assert nm._poller_status["mail"] == "disabled:no-imap-auth"


def test_external_pollers_disabled_on_worker_preserves_local_digest(monkeypatch):
    from types import SimpleNamespace
    import app.config
    import app.mcp.notification_manager as nm
    import app.mcp.flarum_tools as ft

    launched = []

    def fake_create_task(coro):
        launched.append(coro.cr_code.co_name)
        coro.close()

        class DummyTask:
            def set_name(self, _name):
                pass

        return DummyTask()

    monkeypatch.setattr(
        app.config, "get_settings",
        lambda: SimpleNamespace(notification_external_pollers_enabled=False),
    )
    monkeypatch.setattr(ft, "is_flarum_configured", lambda: pytest.fail("external poller must not be configured"))
    monkeypatch.setattr(nm.asyncio, "create_task", fake_create_task)
    monkeypatch.setattr(nm, "_poller_tasks", [])
    monkeypatch.setattr(nm, "_poller_status", {})

    nm._launch_pollers()
    assert launched == ["aicoder_failure_digest_loop"]
    assert all(nm._poller_status[name] == "disabled:node-policy"
               for name in ("mail", "forum", "wordpress"))


def test_external_poller_policy_setting():
    from app.config import Settings
    assert Settings.model_validate({}).notification_external_pollers_enabled is True
    assert Settings.model_validate({
        "TRIFORCE_NOTIFICATION_EXTERNAL_POLLERS_ENABLED": "false",
    }).notification_external_pollers_enabled is False
