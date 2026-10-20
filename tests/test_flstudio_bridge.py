"""Security and contract checks for the privileged FL Studio MCP federation tool."""
import asyncio
from fastapi import Request

from app.mcp.flstudio_bridge_tools import handle_flstudio
from app.mcp.runtime_registry import RuntimeToolRegistry
from app.mcp.tool_registry_unified import get_unified_tools, tool_scope
from app.utils.mcp_security import filter_tools_for_external, is_tool_allowed


def test_flstudio_is_one_canonical_admin_only_tool():
    tools = [t for t in get_unified_tools() if t['name'] == 'flstudio']
    assert len(tools) == 1
    assert tool_scope('flstudio') == 'triforce_admin'
    assert tools[0]['x_scope'] == 'triforce_admin'
    guest = Request({'type': 'http', 'method': 'POST', 'path': '/v1/mcp',
                     'headers': [], 'client': ('198.51.100.20', 1234),
                     'server': ('api.ailinux.me', 443), 'scheme': 'https'})
    assert not filter_tools_for_external(tools, guest)
    assert not is_tool_allowed('flstudio', guest, {'action': 'status'})
    assert not is_tool_allowed('flstudio', guest, {'action': 'call', 'tool': 'fl_set_tempo'})


def test_flstudio_registry_is_privileged():
    registry = RuntimeToolRegistry()
    entry = registry.get_entry('flstudio')
    assert entry is not None
    assert entry.handler is not None
    assert entry.classification == 'write_privileged'
    assert entry.min_tier == 'enterprise'
    profile = registry.resolve_client_profile(request_meta={'source_ip': '198.51.100.20', 'auth_method': 'public_guest'})
    assert registry.evaluate_policy(entry, client_profile=profile)['decision'] == 'blocked'


def test_flstudio_invalid_requests_do_not_execute():
    assert not asyncio.run(handle_flstudio({'action': 'something-else'}))['ok']
    assert not asyncio.run(handle_flstudio({'action': 'call', 'tool': 'bash'}))['ok']
    assert not asyncio.run(handle_flstudio({'action': 'call', 'tool': 'fl_get_tempo', 'arguments': 'bad'}))['ok']


def test_fast_batch_limits_and_name_validation():
    too_many = [{'tool': 'fl_get_tempo'}] * 25
    assert not asyncio.run(handle_flstudio({'action': 'batch', 'calls': too_many}))['ok']
    assert not asyncio.run(handle_flstudio({'action': 'batch', 'calls': []}))['ok']
    assert not asyncio.run(handle_flstudio({'action': 'batch', 'calls': [{'tool': 'bash'}]}))['ok']
    assert not asyncio.run(handle_flstudio({'action': 'batch', 'calls': [{'tool': 'fl_get_tempo', 'arguments': 'bad'}]}))['ok']


def test_fast_batch_is_admin_only():
    guest = Request({'type': 'http', 'method': 'POST', 'path': '/v1/mcp',
                     'headers': [], 'client': ('198.51.100.20', 1234),
                     'server': ('api.ailinux.me', 443), 'scheme': 'https'})
    payload = {'action': 'batch', 'calls': [{'tool': 'fl_get_tempo'}]}
    assert not is_tool_allowed('flstudio', guest, payload)
