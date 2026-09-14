"""Canonical workspace/device MCP contracts owned by TriForce.

This module is dependency-light by design: the unified registry and workspace
transport bridge both import it, so semantic schemas have exactly one owner.
"""
from __future__ import annotations

WORKSPACE_TOOL_NAMES = ('workspace_status',
 'workspace_pair',
 'workspace_info',
 'file_read',
 'file_tree',
 'code_read',
 'code_tree',
 'code_search',
 'code_grep',
 'file_ops',
 'code_edit',
 'computer_observe',
 'computer_screenshot',
 'clipboard_read',
 'clipboard_write',
 'compute_execute',
 'file_edit',
 'directory_create',
 'workspace_clear',
 'device_info',
 'process_ops',
 'service_ops',
 'app_ops',
 'window_ops',
 'computer_input')

WORKSPACE_CONTROL_TOOLS = [{'name': 'workspace_status',
  'description': 'Check whether this MCP session is paired with a browser-selected local workspace. If the user '
                 'message contains a TriForce workspace ID in the form XXXX-XXXX-XXXX-XXXX-XXXX-XXXX, pass it as '
                 'workspace_id; this tool validates the waiting browser and pairs it automatically.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_id': {'type': 'string',
                                                  'description': 'Optional one-time TriForce workspace ID pasted by '
                                                                 'the user.'},
                                 'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'workspace_pair',
  'description': 'Bind this MCP session to the browser workspace waiting under the one-time ID. Use this when the '
                 'user pastes an ID shown by https://api.ailinux.me/v1/mcp.',
  'inputSchema': {'type': 'object',
                  'properties': {'code': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['code']},
  'annotations': {'readOnlyHint': False}},
 {'name': 'workspace_info',
  'description': 'Analyze the paired browser workspace: file counts, size, extensions and sample paths.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'file_read',
  'description': 'Read a UTF-8 text file inside the paired browser workspace.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'path': {'type': 'string'},
                                 'start_line': {'type': 'integer', 'minimum': 1},
                                 'end_line': {'type': 'integer', 'minimum': 1},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['path']},
  'annotations': {'readOnlyHint': True}},
 {'name': 'file_tree',
  'description': 'List a bounded directory tree inside the paired browser workspace.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'path': {'type': 'string', 'default': '.'},
                                 'max_depth': {'type': 'integer', 'minimum': 1, 'maximum': 8},
                                 'max_entries': {'type': 'integer', 'minimum': 1, 'maximum': 1000},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'code_read',
  'description': 'Read source code inside the paired browser workspace.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'path': {'type': 'string'},
                                 'start_line': {'type': 'integer', 'minimum': 1},
                                 'end_line': {'type': 'integer', 'minimum': 1},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['path']},
  'annotations': {'readOnlyHint': True}},
 {'name': 'code_grep',
  'description': 'Regex-search text files inside the paired browser workspace.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'pattern': {'type': 'string'},
                                 'path': {'type': 'string', 'default': '.'},
                                 'glob': {'type': 'string', 'default': '*'},
                                 'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 500},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['pattern']},
  'annotations': {'readOnlyHint': True}},
 {'name': 'computer_observe',
  'description': 'Observe the explicitly shared primary screen through AILinux Helper. Available only when the user '
                 'enabled screen sharing in the native Helper.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'computer_screenshot',
  'description': 'Capture the explicitly shared primary screen through AILinux Helper. Available only when the user '
                 'enabled screen sharing in the native Helper.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'clipboard_read',
  'description': 'Read the local clipboard through AILinux Helper only when the user explicitly enabled '
                 'clipboard-read sharing.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}}},
  'annotations': {'readOnlyHint': True}},
 {'name': 'clipboard_write',
  'description': 'Write text to the local clipboard through AILinux Helper only when the user explicitly enabled '
                 'clipboard-write sharing.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'text': {'type': 'string', 'maxLength': 1048576},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['text']},
  'annotations': {'readOnlyHint': False}},
 {'name': 'compute_execute',
  'description': 'Execute a command only inside an explicitly released disposable compute runtime. For the AILinux Helper remote-compute share, TriForce runs a hardened Docker sandbox with public-internet egress, blocks access to the TriForce host/private networks, and mirrors the paired share at ~/workspace. This is never a host shell.',
  'inputSchema': {'type': 'object',
                  'properties': {'command': {'type': 'string'},
                                 'cwd': {'type': 'string', 'default': '.'},
                                 'timeout': {'type': 'integer', 'minimum': 1, 'maximum': 300},
                                 'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context selector supplied by an authenticated bridge.'}},
                  'required': ['command']},
  'annotations': {'readOnlyHint': False, 'destructiveHint': True}},
 {'name': 'file_edit',
  'description': 'Create or modify a UTF-8 text file inside the paired browser workspace. Available only when the '
                 'user granted browser Write access. The local runtime creates a persistent fallback backup before '
                 'mutation.',
  'inputSchema': {'type': 'object',
                  'properties': {'workspace_token': {'type': 'string'},
                                 'path': {'type': 'string'},
                                 'operation': {'type': 'string', 'enum': ['create', 'write', 'append', 'replace']},
                                 'content': {'type': 'string'},
                                 'old_text': {'type': 'string'},
                                 'new_text': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['path', 'operation']},
  'annotations': {'readOnlyHint': False}},
 {'name': 'directory_create',
  'description': 'Create a directory inside the paired browser workspace when Write access is enabled. The local '
                 'runtime records a persistent pre-change fallback.',
  'inputSchema': {'type': 'object',
                  'properties': {'path': {'type': 'string'},
                                 'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['path']},
  'annotations': {'readOnlyHint': False}},
 {'name': 'workspace_clear',
  'description': 'Delete every file and subdirectory inside the paired browser workspace while preserving the '
                 'workspace root itself. Requires Write access and confirm=DELETE_ALL. A full fallback snapshot is '
                 'created first and the shared backup store is protected.',
  'inputSchema': {'type': 'object',
                  'properties': {'confirm': {'type': 'string', 'enum': ['DELETE_ALL']},
                                 'workspace_token': {'type': 'string'},
                                 'workspace_context': {'type': 'string',
                                                       'description': 'Optional non-secret workspace context '
                                                                      'selector supplied by an authenticated '
                                                                      'bridge.'}},
                  'required': ['confirm']},
  'annotations': {'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True}}]
