"""Runtime event translation only; all memory decisions live in the engine."""
import hashlib

from .memory_trigger import get_memory_engine, recall_event


async def remote_recall(connection, event_type, *, run_id='', task='', model='', **details):
    try:
        settings = get_memory_engine().settings
        if not settings.episodic_memory_enabled:
            return {'status': 'disabled', 'context': ''}
        info = connection.client_info if isinstance(connection.client_info, dict) else {}
        workspace = str(info.get('workspace') or '')
        # Never trust a caller-supplied project to cross user/workspace boundaries.
        identity = str(connection.user_id) + '\0' + workspace
        scope = hashlib.sha256(identity.encode()).hexdigest()[:20]
        return await recall_event(event_type, project_id=f'{settings.memory_project_id or "aicoder"}:{scope}',
            run_id=run_id, task=task, model=model, agent='aicoder', repo=workspace, **details)
    except Exception:
        return {'status': 'degraded', 'context': ''}


async def remote_record(connection, event_type, *, run_id='', task='', model='', **details):
    """Translate remote runtime scope into a controlled episodic observation."""
    try:
        settings = get_memory_engine().settings
        if not settings.episodic_memory_enabled or not settings.memory_record_enabled:
            return {'status': 'disabled'}
        info = connection.client_info if isinstance(connection.client_info, dict) else {}
        workspace = str(info.get('workspace') or '')
        identity = str(connection.user_id) + '\0' + workspace
        scope = hashlib.sha256(identity.encode()).hexdigest()[:20]
        from .memory_trigger import MemoryEvent
        event = MemoryEvent(event_type=event_type, project_id=f'{settings.memory_project_id or "aicoder"}:{scope}',
                            run_id=run_id, task=task, model=model, agent='aicoder', repo=workspace, **details)
        return await get_memory_engine().record_event(event)
    except Exception:
        return {'status': 'degraded'}
