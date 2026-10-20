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
from typing import Any

FLSTUDIO_BRIDGE_ROOT = '/home/zombie/workspace/triforce-flstudio-bridge'
BRIDGE_PYTHON = f'{FLSTUDIO_BRIDGE_ROOT}/.venv/bin/python'
BRIDGE_SCRIPT = f'{FLSTUDIO_BRIDGE_ROOT}/flstudio_ssh_mcp.py'


async def handle_flstudio(params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get('action') or 'status')
    if action not in {'status', 'list', 'call'}:
        return {'ok': False, 'error': 'action must be status, list or call'}
    arguments = params.get('arguments') or {}
    if not isinstance(arguments, dict):
        return {'ok': False, 'error': 'arguments must be a JSON object'}
    tool = str(params.get('tool') or '')
    if action == 'call' and not re.fullmatch(r'fl_[a-zA-Z0-9_]{1,96}', tool):
        return {'ok': False, 'error': 'tool must be an FL Studio MCP tool name'}
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
