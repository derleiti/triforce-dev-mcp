"""Small memory control plane; sensitive history is restricted to internal operators."""
import hashlib
import json
import re

from app.services.episodic_memory import bounded_text, redact
from app.services.memory_trigger import MemoryEvent, get_memory_engine

_PROJECT_KEY_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,160}$")


def _engine(request):
    engine = get_memory_engine()
    state = getattr(request, 'state', None)
    user = getattr(state, 'mcp_auth_user', None)
    method = getattr(state, 'mcp_auth_method', None)
    full_access = getattr(state, 'mcp_auth_full_access', False) is True
    direct_operator = method in {'basic', 'jwt'}
    scoped_oauth_operator = method in {'bearer', 'query'} and full_access
    if not (direct_operator or scoped_oauth_operator) or not user or user != engine.settings.mcp_oauth_user:
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


async def training(params, request=None):
    engine = _engine(request)
    from app.services.memory_training import training_digest
    return await training_digest(engine=engine, limit=int(params.get('limit') or 50))


async def feature_experience_store(params, request=None):
    """Store one verified Dev-MCP feature experience in episodic history."""
    engine = _engine(request)
    project_key = str(params.get('project_key') or 'triforce').strip()
    if not _PROJECT_KEY_RE.fullmatch(project_key) or project_key.startswith(('/', '\\')) or '\\' in project_key:
        raise ValueError('invalid project_key')
    if any(part in {'', '.', '..'} for part in project_key.split('/')):
        raise ValueError('invalid project_key')

    def clean(name, limit):
        return bounded_text(str(redact(params.get(name) or '')).strip(), limit)

    task = clean('task', 1200)
    summary = clean('summary', 3000)
    architecture = clean('architecture', 2200)
    verification = clean('verification', 1400)
    lessons = clean('lessons', 1200)
    future_features = clean('future_features', 1200)
    repo = clean('repo', 500)
    commit = clean('commit', 128)
    if not task or not summary or not verification:
        raise ValueError('task, summary and verification are required')

    fingerprint = hashlib.sha256(
        (project_key + '\n' + task + '\n' + summary + '\n' + architecture).encode('utf-8', errors='replace')
    ).hexdigest()
    project = f"{engine.settings.memory_project_id}:dev:{project_key}"
    dedupe_key = f'feature-experience:{project}:{fingerprint}'
    if engine._seen(dedupe_key):
        return {'status': 'skipped', 'reason': 'deduplicated', 'fingerprint': fingerprint[:16]}

    payload = redact({
        'schema': 'mcp-feature-experience-v1',
        'event_type': 'feature_experience',
        'project_key': project_key,
        'task': task,
        'summary': summary,
        'architecture': architecture,
        'verification': verification,
        'lessons': lessons,
        'future_features': future_features,
        'repo': repo,
        'commit': commit,
        'verification_state': 'verified',
        'source': 'dev-mcp',
        'fingerprint': fingerprint,
    })

    async def operation():
        observation_id = await engine.provider.record(
            text=bounded_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')), 6000),
            title=bounded_text(f"feature_experience: {task}", 180),
            project=project,
            metadata=payload,
        )
        engine._seen(dedupe_key, remember=True, ttl=3600)
        return {'observation_id': observation_id, 'project': project, 'fingerprint': fingerprint[:16]}

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
    'annotations': {'readOnlyHint': False, 'openWorldHint': False}},
    {
        'name': 'memory_training',
        'description': 'Read-only Training Center digest: distill verified workflows, failures and documented bug fixes into best-practice and regression candidates. Internal operators only; never auto-promotes or changes code.',
        'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 50}}},
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'feature_experience_store',
        'description': 'Store one verified completed Dev-MCP feature experience in episodic Claude-Mem history for later Training Center reuse. Internal operators only; fail-open and deduplicated.',
        'inputSchema': {'type': 'object', 'additionalProperties': False, 'properties': {
            'project_key': {'type': 'string', 'maxLength': 160, 'default': 'triforce'},
            'task': {'type': 'string', 'maxLength': 1200},
            'summary': {'type': 'string', 'maxLength': 3000},
            'architecture': {'type': 'string', 'maxLength': 2200},
            'verification': {'type': 'string', 'maxLength': 1400},
            'lessons': {'type': 'string', 'maxLength': 1200},
            'future_features': {'type': 'string', 'maxLength': 1200},
            'repo': {'type': 'string', 'maxLength': 500},
            'commit': {'type': 'string', 'maxLength': 128}},
            'required': ['task', 'summary', 'verification']},
        'annotations': {'readOnlyHint': False, 'openWorldHint': False},
    }]
HISTORY_HANDLERS = {
    'memory_history': history,
    'memory_training': training,
    'feature_experience_store': feature_experience_store,
}
