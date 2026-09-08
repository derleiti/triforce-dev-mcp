"""Small memory control plane; sensitive history is restricted to internal operators."""
from app.services.memory_trigger import MemoryEvent, get_memory_engine
from app.services.episodic_memory import bounded_text, redact


def _engine(request):
    engine = get_memory_engine()
    state = getattr(request, 'state', None)
    user = getattr(state, 'mcp_auth_user', None)
    method = getattr(state, 'mcp_auth_method', None)
    if method not in {'basic', 'jwt'} or not user or user != engine.settings.mcp_oauth_user:
        raise PermissionError('episodic history requires authenticated operator credentials')
    if not engine.settings.memory_project_id:
        raise ValueError('TRIFORCE_MEMORY_PROJECT_ID must be configured for MCP history')
    return engine


async def history(params, request=None):
    engine = _engine(request)
    project = engine.settings.memory_project_id
    action = params.get('action', 'search')
    if action == 'promote':
        return await engine.promote(params.get('observation_id'), memory_type=str(params.get('memory_type', 'fact')),
                                    content=str(params.get('content', '')), evidence=str(params.get('evidence', '')))
    if action in {'search', 'recent'}:
        event = MemoryEvent('run_resumed' if action == 'recent' else 'task_started',
                            project, 'manual', task=str(params.get('query', '')))
        return await engine.recall(event, manual=True)
    async def operation():
        if action == 'get':
            ids = params.get('ids', [])
            if not isinstance(ids, list) or not ids or any(type(i) is not int or i <= 0 for i in ids):
                raise ValueError('positive observation ids required')
            value = await engine.provider.get_observations(ids, project)
        elif action == 'timeline':
            anchor = params.get('anchor')
            if type(anchor) is not int or anchor <= 0:
                raise ValueError('positive anchor required')
            value = await engine.provider.timeline(anchor, project)
        else:
            raise ValueError('unknown memory history action')
        import json
        # Detail retrieval is explicit and still bounded, even for legacy history.
        return {'context': bounded_text(json.dumps(redact(value), ensure_ascii=False), engine.settings.memory_token_budget),
                'historical_untrusted': True}
    result = await engine._guard(operation)
    return {k: v for k, v in result.items() if k != 'value'} | result.get('value', {})


HISTORY_TOOLS = [{
    'name': 'memory_history',
    'description': 'Scoped episodic history (untrusted). Search compact IDs first, then timeline/get only selected IDs. Internal operators only.',
    'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
        'action': {'type': 'string', 'enum': ['search', 'recent', 'timeline', 'get', 'promote']},
        'query': {'type': 'string', 'maxLength': 512},
        'anchor': {'type': 'integer', 'minimum': 1},
        'ids': {'type': 'array', 'items': {'type': 'integer', 'minimum': 1}, 'maxItems': 5},
        'observation_id': {'type': 'integer', 'minimum': 1},
        'memory_type': {'type': 'string', 'enum': ['fact', 'decision', 'code', 'summary', 'todo']},
        'content': {'type': 'string', 'maxLength': 8000},
        'evidence': {'type': 'string', 'maxLength': 1000}},
        'required': ['action']},
    'annotations': {'readOnlyHint': False, 'openWorldHint': False}}]
HISTORY_HANDLERS = {'memory_history': history}
