"""Official Claude-Mem worker boundary. No observer, IDE hooks or DB access."""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol

import httpx


class MemoryUnavailable(RuntimeError):
    """Safe, content-free failure reason for the optional history service."""


_SECRET_KEY = re.compile(r"(?i)(api[_-]?key|token|authorization|password|passwd|private[_-]?key|cookie|session[_-]?secret|client[_-]?secret)")
_SECRET_TEXT = re.compile(
    r"(?is)-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|$)"
    r"|<private>.*?(?:</private>|$)"
    r"|(?:authorization|cookie|set-cookie)\s*[:=][^\r\n]*"
    r"|(?:[\w.-]*(?:api[_-]?key|token|password|passwd|session[_-]?secret|client[_-]?secret))"
    r"[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
    r"|\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{20,}|github_pat_[\w]+|AKIA[A-Z0-9]{16}|eyJ[\w-]+\.[\w-]+\.[\w-]+)"
    r"|\bBearer\s+\S+"
    r"|https?://[^\s/@]+:[^\s/@]+@[^\s]+"
)


def redact(value: Any) -> Any:
    """Defense in depth for both legacy recalls and new observations."""
    if isinstance(value, str):
        return _SECRET_TEXT.sub('[REDACTED]', value)
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if _SECRET_KEY.search(str(k)) else redact(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def bounded_text(text: str, budget: int) -> str:
    # UTF-8 bytes are a conservative upper bound for byte-based agent tokenizers.
    return text.encode('utf-8')[:budget].decode('utf-8', errors='ignore')


class EpisodicMemoryProvider(Protocol):
    async def health(self) -> dict: ...
    async def search(self, query: str, project: str, limit: int) -> list[dict]: ...
    async def timeline(self, anchor: int, project: str, depth: int = 1) -> dict: ...
    async def get_observations(self, ids: list[int], project: str) -> list[dict]: ...
    async def recent(self, project: str, limit: int) -> list[dict]: ...
    async def record(self, text: str, title: str, project: str, metadata: dict) -> int: ...


class ClaudeMemAdapter:
    """13.24.1 public HTTP contract; every operation bounded and loopback-only."""

    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport

    def endpoint(self) -> str:
        directory = (self.settings.episodic_memory_data_dir or os.getenv('CLAUDE_MEM_DATA_DIR')
                     or str(Path.home() / '.claude-mem'))
        config = json.loads((Path(directory).expanduser() / 'settings.json').read_text())
        # Upstream settings can also use the legacy env wrapper.
        config = config.get('env', config)
        host = str(config.get('CLAUDE_MEM_WORKER_HOST', '127.0.0.1'))
        if host not in {'127.0.0.1', '::1', 'localhost'}:
            raise MemoryUnavailable('non_loopback_worker')
        port = int(config['CLAUDE_MEM_WORKER_PORT'])
        if not 1 <= port <= 65535:
            raise MemoryUnavailable('invalid_worker_port')
        return f'http://{"[::1]" if host == "::1" else "127.0.0.1"}:{port}'

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        if not self.settings.episodic_memory_enabled:
            raise MemoryUnavailable('disabled')
        async with httpx.AsyncClient(timeout=self.settings.memory_timeout, trust_env=False,
                                     transport=self.transport, follow_redirects=False) as client:
            async with client.stream(method, self.endpoint() + path, **kwargs) as response:
                response.raise_for_status()
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > 512_000:
                        raise MemoryUnavailable('response_too_large')
                value = json.loads(payload)
                if isinstance(value, dict) and (value.get('isError') or value.get('error')):
                    raise MemoryUnavailable('worker_error')
                return value

    @staticmethod
    def _rows(value: Any, project: str) -> list[dict]:
        if not isinstance(value, list) or any(not isinstance(row, dict) or
                type(row.get('id')) is not int or not isinstance(row.get('project'), str)
                for row in value):
            raise MemoryUnavailable('malformed_response')
        rows = []
        for raw in value:
            if raw['project'] != project:
                continue
            row = redact(raw)
            metadata = row.get('metadata') or {}
            if isinstance(metadata, str):
                metadata = json.loads(metadata)
            if not isinstance(metadata, dict):
                raise MemoryUnavailable('malformed_metadata')
            row['metadata'] = redact(metadata)
            rows.append(row)
        return rows

    async def health(self) -> dict:
        value = await self._request('GET', '/api/health')
        if not isinstance(value, dict) or value.get('status') != 'ok':
            raise MemoryUnavailable('worker_unhealthy')
        return {'worker_status': 'ok', 'version': str(value.get('version', 'unknown'))}

    async def search(self, query: str, project: str, limit: int) -> list[dict]:
        async def fetch(term):
            value = await self._request('GET', '/api/search', params={
                'query': bounded_text(redact(term), 512), 'project': project,
                'format': 'json', 'type': 'observations',
                'limit': min(limit, self.settings.memory_max_results), 'orderBy': 'relevance'})
            if not isinstance(value, dict) or 'observations' not in value:
                raise MemoryUnavailable('malformed_response')
            return self._rows(value['observations'], project)
        rows = await fetch(query)
        if not rows and query:
            # SQLite upstream quotes the ENTIRE query as a phrase. Bounded term
            # fallback gives related-task recall without pretending it accepts OR.
            terms = sorted(set(re.findall(r'[\w./-]{3,}', redact(query))), key=lambda t: (-len(t), t))[:3]
            batches = await asyncio.gather(*(fetch(term) for term in terms if term != query))
            rows = list({r['id']: r for batch in batches for r in batch}.values())
        # Public JSON search contains full rows. Do not pass narratives to callers.
        keys = {'id', 'title', 'subtitle', 'project', 'type', 'created_at_epoch',
                'created_at', 'metadata', 'files_read', 'files_modified'}
        return [{k: v for k, v in row.items() if k in keys}
                for row in rows[:min(limit, self.settings.memory_max_results)]]

    async def recent(self, project: str, limit: int) -> list[dict]:
        return await self.search('', project, limit)

    async def get_observations(self, ids: list[int], project: str) -> list[dict]:
        ids = list(dict.fromkeys(ids))[:self.settings.memory_max_results]
        value = await self._request('POST', '/api/observations/batch', json={'ids': ids, 'project': project})
        return [r for r in self._rows(value, project) if r['id'] in ids]

    async def timeline(self, anchor: int, project: str, depth: int = 1) -> dict:
        # Verify anchor scope before invoking text-based timeline.
        if not await self.get_observations([anchor], project):
            return {'content': []}
        value = await self._request('GET', '/api/timeline', params={
            'anchor': anchor, 'project': project, 'depth_before': min(max(depth, 0), 2),
            'depth_after': min(max(depth, 0), 2)})
        if not isinstance(value, dict) or not isinstance(value.get('content'), list):
            raise MemoryUnavailable('malformed_response')
        return redact(value)

    async def record(self, text: str, title: str, project: str, metadata: dict) -> int:
        if not self.settings.memory_record_enabled:
            raise MemoryUnavailable('record_disabled')
        value = await self._request('POST', '/api/memory/save', json=redact({
            'text': text, 'title': title, 'project': project, 'metadata': metadata}))
        if not isinstance(value, dict) or value.get('success') is not True or type(value.get('id')) is not int:
            raise MemoryUnavailable('malformed_response')
        return value['id']
