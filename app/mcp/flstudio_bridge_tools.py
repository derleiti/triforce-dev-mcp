"""Owner/admin-only bridge to the FL Studio MCP on zombie-pc via WireGuard SSH.

This is a *client* of the upstream MCP, not a reimplementation. No listening
port or shell command parameter is exposed through this tool.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import signal
from pathlib import Path
from typing import Any

FLSTUDIO_BRIDGE_ROOT = '/home/zombie/workspace/triforce-flstudio-bridge'
BRIDGE_PYTHON = f'{FLSTUDIO_BRIDGE_ROOT}/.venv/bin/python'
BRIDGE_SCRIPT = f'{FLSTUDIO_BRIDGE_ROOT}/flstudio_ssh_mcp.py'

# Private, persistent MCP client. A connect failure falls back to the previous
# SSH transport. Once a request was sent, never replay a write on timeout.
FAST_SOCKET = Path('/home/zombie/.cache/triforce-flstudio-fast/mcp.sock')


async def _fast_request(payload: dict[str, Any]) -> dict[str, Any] | None:
    if os.environ.get('TRIFORCE_FLSTUDIO_FAST','1') == '0':
        return None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(str(FAST_SOCKET), limit=4*1024*1024),
            timeout=0.35,
        )
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        writer.write(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()+b'\n')
        await writer.drain()
        timeout = 52 if payload.get('action') in ('call','batch') else 28
        raw = await asyncio.wait_for(reader.readline(),timeout=timeout)
        if not raw or len(raw)>4*1024*1024:
            raise ValueError('Empty or oversized fast bridge response')
        return json.loads(raw)
    except (OSError,ValueError,asyncio.TimeoutError) as exc:
        # Do not fall back after a dispatched request: that could duplicate
        # non-idempotent FL Studio edits.
        return {'is_error':True,'error':type(exc).__name__,
                'detail':'Fast Bridge response failed; check FL Studio state before retrying.'}
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass



async def handle_flstudio(params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get('action') or 'status')
    if action not in {'status', 'list', 'call', 'batch'}:
        return {'ok': False, 'error': 'action must be status, list, call or batch'}
    arguments = params.get('arguments') or {}
    if not isinstance(arguments, dict):
        return {'ok': False, 'error': 'arguments must be a JSON object'}
    tool = str(params.get('tool') or '')
    if action == 'call' and not re.fullmatch(r'fl_[a-zA-Z0-9_]{1,96}', tool):
        return {'ok': False, 'error': 'tool must be an FL Studio MCP tool name'}
    calls = params.get('calls', [])
    if action == 'batch':
        if not isinstance(calls,list) or not 1 <= len(calls) <= 24:
            return {'ok': False, 'error': 'batch requires 1-24 calls'}
        if any(not isinstance(item,dict) or not re.fullmatch(
                r'fl_[a-zA-Z0-9_]{1,96}',str(item.get('tool') or '')) or
               not isinstance(item.get('arguments',{}),dict) for item in calls):
            return {'ok':False,'error':'invalid FL Studio batch entry'}

    payload = {'action':action}
    if action == 'call':
        payload.update(tool=tool,arguments=arguments)
    if action == 'batch':
        payload.update(calls=calls,dry_run=bool(params.get('dry_run',False)))

    response = await _fast_request(payload)
    if response is not None:
        return {'ok':not response.get('is_error',False) and not bool(response.get('error')),
                'action':action,'target':'zombie-pc','transport':'persistent-unix-mcp',
                'data':response}
    if action == 'batch':
        return {'ok':False,'error':'Fast Bridge offline: batch was not dispatched'}
    if not os.path.isfile(BRIDGE_SCRIPT) or not os.access(BRIDGE_PYTHON, os.X_OK):
        return {'ok': False, 'error': 'FL Studio bridge is not installed on the TriForce host'}

    argv = [BRIDGE_PYTHON, BRIDGE_SCRIPT, action]
    if action == 'call':
        argv += [tool, '--json', json.dumps(arguments, ensure_ascii=False)]
    limit = 45 if action == 'call' else 25
    process = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=limit)
    except asyncio.TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        return {'ok': False, 'error': f'FL Studio MCP timed out after {limit}s'}
    try:
        result = json.loads(stdout.decode('utf-8', 'replace').strip())
    except (ValueError, UnicodeError):
        return {'ok': False, 'error': 'Invalid FL Studio MCP response',
                'returncode': process.returncode}
    return {'ok': process.returncode == 0 and not bool(result.get('is_error')),
            'action': action, 'target': 'zombie-pc', 'data': result}


FLSTUDIO_HANDLERS = {'flstudio': handle_flstudio}
