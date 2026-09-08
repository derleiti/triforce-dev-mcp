import json
from types import SimpleNamespace

import httpx
import pytest

from app.services.episodic_memory import ClaudeMemAdapter, MemoryUnavailable, redact


def config(tmp_path, **changes):
    (tmp_path / 'settings.json').write_text(json.dumps({'CLAUDE_MEM_WORKER_PORT': '49999'}))
    values = dict(episodic_memory_enabled=True, episodic_memory_provider='claude-mem',
        episodic_memory_data_dir=str(tmp_path), memory_timeout=.05, memory_max_results=4,
        memory_token_budget=1200, memory_auto_recall=True, memory_trigger_file=True,
        memory_trigger_failure=True, memory_trigger_retry=True, memory_record_enabled=False,
        memory_promotion_enabled=False, memory_project_id='test-project', memory_stale_days=90)
    return SimpleNamespace(**(values | changes))


@pytest.mark.asyncio
async def test_adapter_contract_and_projection(tmp_path):
    def handle(request):
        assert request.url.port == 49999
        assert request.url.params['format'] == 'json'
        return httpx.Response(200, json={'observations': [
            {'id': 1, 'project': 'test-project', 'title': 'fix', 'narrative': 'large history', 'metadata': '{}'},
            {'id': 2, 'project': 'another-project', 'title': 'private'}]})
    adapter = ClaudeMemAdapter(config(tmp_path), httpx.MockTransport(handle))
    assert await adapter.search('fix', 'test-project', 3) == [
        {'id': 1, 'project': 'test-project', 'title': 'fix', 'metadata': {}}]


@pytest.mark.asyncio
async def test_adapter_rejects_malformed(tmp_path):
    adapter = ClaudeMemAdapter(config(tmp_path), httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(MemoryUnavailable):
        await adapter.search('fix', 'test-project', 4)


def test_no_guessed_port(tmp_path):
    s = config(tmp_path)
    (tmp_path / 'settings.json').write_text('{}')
    with pytest.raises(KeyError):
        ClaudeMemAdapter(s).endpoint()


def test_secret_filter():
    value = {'password': 'hunter2', 'text': 'Authorization: Bearer secret\nAPI_KEY=secret\nCookie: session=secret\n-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----'}
    result = json.dumps(redact(value))
    assert 'secret' not in result and 'hunter2' not in result

from unittest.mock import AsyncMock
from app.services.memory_trigger import MemoryEvent, MemoryTriggerEngine


def engine(tmp_path, rows=None, **changes):
    provider = AsyncMock()
    provider.search.return_value = rows or []
    provider.health.return_value = {'worker_status': 'ok'}
    return MemoryTriggerEngine(config(tmp_path, **changes), provider)


@pytest.mark.asyncio
async def test_task_recall_empty_and_health(tmp_path):
    e = engine(tmp_path)
    result = await e.recall(MemoryEvent('task_started', 'test-project', 'r1', task='fix parser'))
    assert result['status'] == 'ok' and result['context'] == ''
    assert e.provider.search.await_count == 1
    assert (await e.health())['search_available']

import time


def memory(i=1, **meta):
    return {'id': i, 'project': 'test-project', 'title': 'parser regression fix',
            'created_at_epoch': time.time() * 1000,
            'metadata': {'verification_state': 'failed', 'approaches': 'Approach A failed: strips valid zero', **meta}}


@pytest.mark.asyncio
async def test_task_matching_context_and_limits(tmp_path):
    e = engine(tmp_path, [memory(i) for i in range(10)], memory_token_budget=1000, memory_max_results=3)
    result = await e.recall(MemoryEvent('task_started', 'test-project', 'r1', task='parser'))
    assert result['ids'] and len(result['ids']) <= 3
    assert 'failed' in result['context']
    assert len(result['context'].encode()) <= 1000

@pytest.mark.asyncio
@pytest.mark.parametrize('event_type', ['test_failed', 'exception_raised', 'tool_failed', 'provider_failed', 'retry_requested'])
async def test_failure_and_retry(tmp_path, event_type):
    e = engine(tmp_path, [memory()])
    result = await e.recall(MemoryEvent(event_type, 'test-project', 'r1',
        error='Traceback old line\nValueError: parser at 0xab12 line 987', error_type='ValueError'))
    query = e.provider.search.call_args.args[0]
    assert 'Traceback' not in query and '987' not in query and '0xab12' not in query
    assert result['ids'] == [1] and 'Approach A failed' in result['context']

@pytest.mark.asyncio
async def test_file_dedup_and_new_run(tmp_path):
    e = engine(tmp_path, [memory(file='parser.py')])
    for kind, path in [('file_opened', './parser.py'), ('file_modified', 'parser.py'), ('file_opened', 'parser.py')]:
        await e.recall(MemoryEvent(kind, 'test-project', 'r1', file=path))
    assert e.provider.search.await_count == 1
    await e.recall(MemoryEvent('file_opened', 'test-project', 'r2', file='parser.py'))
    assert e.provider.search.await_count == 2

@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['agent_handoff', 'run_resumed', 'merge_started', 'commit_started'])
async def test_handoff_resume_integration_events(tmp_path, kind):
    e = engine(tmp_path, [memory(1, verification_state='verified', evidence='pytest parser passed'), memory(2)])
    result = await e.recall(MemoryEvent(kind, 'test-project', 'new-run', task='parser'))
    assert 'verified' in result['context'] and 'failed' in result['context']
    assert 'pytest parser passed' in result['context'] and 'Approach A failed' in result['context']

@pytest.mark.asyncio
async def test_record_disabled_by_default(tmp_path):
    e = engine(tmp_path)
    result = await e.record_event(MemoryEvent('run_completed', 'test-project', 'r1', task='done'))
    assert result['status'] == 'disabled'
    e.provider.record.assert_not_awaited()


@pytest.mark.asyncio
async def test_record_redacts_and_deduplicates(tmp_path):
    e = engine(tmp_path, memory_record_enabled=True)
    e.provider.record.return_value = 42
    event = MemoryEvent('tool_failed', 'test-project', 'r1', task='fix', tool='x',
                        error='Authorization: Bearer supersecret', verification_state='failed')
    first = await e.record_event(event)
    second = await e.record_event(event)
    assert first['status'] == 'ok' and first['observation_id'] == 42
    assert second['reason'] == 'deduplicated'
    kwargs = e.provider.record.call_args.kwargs
    assert 'supersecret' not in json.dumps(kwargs)
    assert kwargs['metadata']['verification_state'] == 'failed'


@pytest.mark.asyncio
async def test_promotion_rejects_unverified(tmp_path):
    e = engine(tmp_path, memory_promotion_enabled=True)
    e.provider.get_observations.return_value = [memory(7, verification_state='failed')]
    result = await e.promote(7, memory_type='fact', content='parser fix', evidence='pytest passed')
    assert result == {'status': 'rejected', 'reason': 'observation_not_verified'}


@pytest.mark.asyncio
async def test_promotion_requires_evidence(tmp_path):
    e = engine(tmp_path, memory_promotion_enabled=True)
    e.provider.get_observations.return_value = [memory(7, verification_state='verified', evidence='')]
    result = await e.promote(7, memory_type='fact', content='parser fix')
    assert result == {'status': 'rejected', 'reason': 'verification_evidence_required'}
