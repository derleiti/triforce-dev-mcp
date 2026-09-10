import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.services.aicoder_agent_events import record_aicoder_run
from app.services.aicoder_runner import AICoderRunResult


def _result(status='error', error='boom', elapsed_ms=1000):
    return AICoderRunResult(
        profile_id='pilot', status=status, error=error, model='m', run_id='r1',
        events=[{'type':'run_terminal','elapsed_ms':elapsed_ms}],
    )


def test_success_resets_failure_streak_without_notification(tmp_path: Path):
    state = tmp_path / 'state.json'
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=AsyncMock()) as notify:
        asyncio.run(record_aicoder_run(_result(status='success', error='')))
    assert not notify.called


def test_single_failure_notifies_but_does_not_mail(tmp_path: Path):
    state = tmp_path / 'state.json'
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=AsyncMock(return_value=True)) as notify, \
         patch('app.services.aicoder_agent_events._send_critical_mail', new=AsyncMock()) as mail:
        out = asyncio.run(record_aicoder_run(_result()))
    assert out['kind'] == 'run_failed'
    assert out['failure_streak'] == 1
    assert notify.await_args.kwargs['priority'] == 'high'
    assert not mail.called


def test_third_failure_is_critical_and_sends_one_mail(tmp_path: Path):
    state = tmp_path / 'state.json'
    notify = AsyncMock(return_value=True)
    mail = AsyncMock(return_value=True)
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=notify), \
         patch('app.services.aicoder_agent_events._send_critical_mail', new=mail):
        asyncio.run(record_aicoder_run(_result(), cooldown_s=0))
        asyncio.run(record_aicoder_run(_result(), cooldown_s=0))
        out = asyncio.run(record_aicoder_run(_result(), cooldown_s=0))
        asyncio.run(record_aicoder_run(_result(), cooldown_s=0))
    assert out['kind'] == 'repeated_failure'
    assert out['failure_streak'] == 3
    assert mail.await_count == 1


def test_headless_denial_maps_to_high(tmp_path: Path):
    state = tmp_path / 'state.json'
    notify = AsyncMock(return_value=True)
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=notify):
        out = asyncio.run(record_aicoder_run(_result(error='headless mode denied sudo approval')))
    assert out['kind'] == 'headless_denied'
    assert notify.await_args.kwargs['priority'] == 'high'


def test_long_success_maps_to_normal(tmp_path: Path):
    state = tmp_path / 'state.json'
    notify = AsyncMock(return_value=True)
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=notify):
        out = asyncio.run(record_aicoder_run(_result(status='success', error='', elapsed_ms=600001)))
    assert out['kind'] == 'long_run_completed'
    assert notify.await_args.kwargs['priority'] == 'normal'


def test_headless_denial_does_not_increment_failure_streak(tmp_path: Path):
    state = tmp_path / 'state.json'
    notify = AsyncMock(return_value=True)
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=notify), \
         patch('app.services.aicoder_agent_events._send_critical_mail', new=AsyncMock()) as mail:
        for _ in range(4):
            out = asyncio.run(record_aicoder_run(_result(status='paused', error='headless mode rejected sudo approval'), cooldown_s=0))
    assert out['kind'] == 'headless_denied'
    assert out['failure_streak'] == 0
    assert not mail.called


def test_generic_pause_is_not_failure_or_notification(tmp_path: Path):
    state = tmp_path / 'state.json'
    notify = AsyncMock(return_value=True)
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=notify):
        out = asyncio.run(record_aicoder_run(_result(status='paused', error='model asked for continuation')))
    assert out['kind'] == 'run_paused'
    assert out['failure_streak'] == 0
    assert not notify.called

from datetime import datetime, timezone
import json

from app.services.aicoder_agent_events import send_daily_failure_digest


def test_failed_run_is_persisted_for_daily_digest(tmp_path: Path):
    state = tmp_path / 'state.json'
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.aicoder_agent_events._store_notification', new=AsyncMock(return_value=True)):
        asyncio.run(record_aicoder_run(_result()))
    data = json.loads(state.read_text())
    assert len(data['daily_failures']) == 1
    assert data['daily_failures'][0]['run_id'] == 'r1'


def test_digest_not_due_before_configured_hour(tmp_path: Path):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'daily_failures': [{'id':'r1','profile_id':'pilot'}]}))
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.mail_service.mail_service.send') as send:
        out = asyncio.run(send_daily_failure_digest(now=datetime(2026, 9, 10, 5, 0, tzinfo=timezone.utc)))
    assert out['reason'] == 'not_due'
    assert not send.called


def test_digest_sends_once_and_clears_selected_entries(tmp_path: Path):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'daily_failures': [{'id':'r1','profile_id':'pilot','streak':1,'error':'boom'}]}))
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.mail_service.mail_service.send') as send:
        out = asyncio.run(send_daily_failure_digest(now=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)))
        again = asyncio.run(send_daily_failure_digest(now=datetime(2026, 9, 10, 9, 0, tzinfo=timezone.utc)))
    assert out['sent'] is True and out['count'] == 1
    assert again['reason'] == 'already_sent'
    assert send.call_count == 1
    data = json.loads(state.read_text())
    assert data['daily_failures'] == []
    assert data['last_digest_date'] == '2026-09-10'


def test_digest_mail_error_keeps_pending_entries(tmp_path: Path):
    state = tmp_path / 'state.json'
    state.write_text(json.dumps({'daily_failures': [{'id':'r1','profile_id':'pilot','streak':1,'error':'boom'}]}))
    with patch('app.services.aicoder_agent_events.STATE_FILE', state), \
         patch('app.services.mail_service.mail_service.send', side_effect=RuntimeError('smtp down')):
        out = asyncio.run(send_daily_failure_digest(now=datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)))
    assert out['reason'] == 'mail_error'
    data = json.loads(state.read_text())
    assert len(data['daily_failures']) == 1
    assert 'last_digest_date' not in data
