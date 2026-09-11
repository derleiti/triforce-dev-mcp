"""
MCP Command Filter Service
==========================

Filters and executes MCP/API commands from chat messages.
Allows non-MCP models to access API functionality transparently.

Features:
- Detects @mcp.call() patterns in text
- Detects /api, /mcp, /v1, /triforce commands
- Executes commands and injects results
- Supports web search for unknown queries
- Logs ALL detected commands to TriForce Central Logger

Version: 2.81 - Central Logging Integration
"""

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ailinux.mcp_filter")

# TriForce Central Logger Integration
try:
    from ..utils.triforce_logging import central_logger
    _HAS_CENTRAL_LOGGING = True
except ImportError:
    _HAS_CENTRAL_LOGGING = False
    central_logger = None

# Pattern für MCP-Aufrufe
MCP_CALL_PATTERN = re.compile(
    r'@mcp\.call\s*\(\s*([a-zA-Z0-9_.\-]+)\s*,?\s*(\{[^}]*\})?\s*\)',
    re.IGNORECASE | re.DOTALL
)

# Pattern für Slash-Kommandos
SLASH_COMMAND_PATTERN = re.compile(
    r'(?:^|\s)/(mcp|api|v1|triforce|ollama|tristar)\s+(\S+)(?:\s+(.*))?',
    re.IGNORECASE | re.MULTILINE
)

# Pattern für direkte API-Anfragen
API_REQUEST_PATTERN = re.compile(
    r'(?:GET|POST|PUT|DELETE)\s+((?:/v1|/mcp|/triforce)[^\s]+)',
    re.IGNORECASE
)

# Pattern für natürlichsprachliche Anfragen
NATURAL_API_PATTERNS = [
    (re.compile(r'(?:list|show|get)\s+(?:all\s+)?(?:ollama\s+)?models?', re.I), 'ollama.list', {}),
    (re.compile(r'(?:list|show|get)\s+(?:running|active)\s+models?', re.I), 'ollama.ps', {}),
    (re.compile(r'pull\s+(?:model\s+)?([a-zA-Z0-9.:_-]+)', re.I), 'ollama.pull', lambda m: {'name': m.group(1)}),
    (re.compile(r'(?:show|info|details?)\s+(?:model\s+)?([a-zA-Z0-9.:_-]+)', re.I), 'ollama.show', lambda m: {'name': m.group(1)}),
    (re.compile(r'(?:delete|remove)\s+(?:model\s+)?([a-zA-Z0-9.:_-]+)', re.I), 'ollama.delete', lambda m: {'name': m.group(1)}),
    (re.compile(r'(?:list|show|get)\s+(?:all\s+)?prompts?', re.I), 'tristar.prompts.list', {}),
    (re.compile(r'(?:list|show|get)\s+(?:all\s+)?agents?', re.I), 'tristar.agents', {}),
    (re.compile(r'(?:system|tristar)\s+status', re.I), 'tristar.status', {}),
    (re.compile(r'(?:get|show|read)\s+logs?(?:\s+for\s+(\S+))?', re.I), 'tristar.logs', lambda m: {'agent_id': m.group(1)} if m.group(1) else {}),
    (re.compile(r'(?:get|show|read)\s+settings?', re.I), 'tristar.settings', {}),
    (re.compile(r'ollama\s+health', re.I), 'ollama.health', {}),
]


class MCPFilter:
    """Filters MCP commands from chat and executes them."""

    def __init__(self):
        self._handlers = None

    async def _get_handlers(self) -> Dict[str, Any]:
        """Lazy load handlers to avoid circular imports."""
        if self._handlers is None:
            from ..routes.mcp_remote import TOOL_HANDLERS
            self._handlers = TOOL_HANDLERS
        return self._handlers

    def extract_mcp_calls(self, text: str) -> List[Tuple[str, str, Dict[str, Any]]]:
        """
        Extract MCP calls from text.

        Returns: List of (full_match, tool_name, arguments)
        """
        calls = []

        # @mcp.call() patterns
        for match in MCP_CALL_PATTERN.finditer(text):
            tool_name = match.group(1)
            args_str = match.group(2) or '{}'
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError:
                args = {}
            calls.append((match.group(0), tool_name, args))

        # Slash commands: /mcp ollama.list, /triforce status, etc.
        for match in SLASH_COMMAND_PATTERN.finditer(text):
            prefix = match.group(1).lower()
            command = match.group(2)
            extra = match.group(3) or ''

            # Build tool name
            if prefix == 'mcp':
                tool_name = command
            elif prefix == 'ollama':
                tool_name = f'ollama.{command}'
            elif prefix == 'tristar':
                tool_name = f'tristar.{command}'
            else:
                tool_name = command

            # Parse extra as JSON or key=value pairs
            args = {}
            if extra:
                extra = extra.strip()
                if extra.startswith('{'):
                    try:
                        args = json.loads(extra)
                    except json.JSONDecodeError:
                        pass
                else:
                    # key=value pairs
                    for part in extra.split():
                        if '=' in part:
                            k, v = part.split('=', 1)
                            args[k] = v

            calls.append((match.group(0), tool_name, args))

        # Natural language patterns
        for pattern, tool_name, args_builder in NATURAL_API_PATTERNS:
            match = pattern.search(text)
            if match:
                if callable(args_builder):
                    args = args_builder(match)
                else:
                    args = args_builder
                calls.append((match.group(0), tool_name, args))

        return calls

    async def execute_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a single MCP tool call."""
        handlers = await self._get_handlers()

        handler = handlers.get(tool_name)
        if not handler:
            # Try with prefixes
            for prefix in ['ollama.', 'tristar.', 'codebase.', 'cli-agents.']:
                handler = handlers.get(f'{prefix}{tool_name}')
                if handler:
                    break

        if not handler:
            return {
                'error': f"Tool '{tool_name}' not found",
                'available_tools': list(handlers.keys())[:20],
            }

        try:
            result = await handler(arguments)
            return {'tool': tool_name, 'result': result, 'success': True}
        except Exception as e:
            logger.error(f"MCP call failed: {tool_name} - {e}")
            return {'tool': tool_name, 'error': str(e), 'success': False}

    async def process_message(
        self,
        message: str,
        execute: bool = True,
        inject_results: bool = True,
        source: str = "chat",
    ) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Process a message, extract and optionally execute MCP calls.
        ALL detected commands are logged to TriForce Central Logger for
        idle agents to process.

        Args:
            message: The input message
            execute: Whether to execute the calls
            inject_results: Whether to inject results into the message
            source: Source identifier (user, mesh-ai, agent-id)

        Returns:
            (processed_message, results)
        """
        calls = self.extract_mcp_calls(message)

        if not calls:
            return message, []

        results = []
        processed = message

        for full_match, tool_name, args in calls:
            # ===== CENTRAL LOGGING - Log ALL detected commands =====
            if _HAS_CENTRAL_LOGGING and central_logger:
                await central_logger.log(
                    category="mcp_call",
                    message=f"MCP Command detected: {tool_name}",
                    level="info",
                    source=f"mcp_filter:{source}",
                    metadata={
                        "tool": tool_name,
                        "arguments": args,
                        "full_match": full_match,
                        "execute": execute,
                        "source": source,
                        "pending": not execute,  # If not executed, mark as pending for idle agents
                    }
                )

            if execute:
                result = await self.execute_call(tool_name, args)
                results.append(result)

                # Log execution result
                if _HAS_CENTRAL_LOGGING and central_logger:
                    await central_logger.log(
                        category="mcp_call",
                        message=f"MCP Command executed: {tool_name} -> {'success' if result.get('success') else 'error'}",
                        level="info" if result.get('success') else "warning",
                        source=f"mcp_filter:{source}",
                        metadata={
                            "tool": tool_name,
                            "success": result.get('success', False),
                            "error": result.get('error'),
                        }
                    )

                if inject_results:
                    # Replace the call with the result
                    if result.get('success'):
                        result_text = f"\n[MCP_RESULT:{tool_name}]\n```json\n{json.dumps(result.get('result', {}), indent=2, ensure_ascii=False)}\n```\n"
                    else:
                        result_text = f"\n[MCP_ERROR:{tool_name}] {result.get('error', 'Unknown error')}\n"
                    processed = processed.replace(full_match, result_text, 1)
            else:
                # Just mark as detected (logged above with pending=True)
                results.append({'tool': tool_name, 'args': args, 'detected': True})

        return processed, results

    def filter_mcp_from_display(self, text: str) -> str:
        """
        Remove MCP calls from text for display purposes.
        The calls are still tracked but not shown to users.
        """
        # Remove @mcp.call() patterns
        text = MCP_CALL_PATTERN.sub('', text)

        # Remove slash commands
        text = SLASH_COMMAND_PATTERN.sub('', text)

        # Remove [MCP_RESULT:...] blocks
        text = re.sub(r'\[MCP_RESULT:[^\]]+\].*?```\s*', '', text, flags=re.DOTALL)

        # Remove [MCP_ERROR:...] lines
        text = re.sub(r'\[MCP_ERROR:[^\]]+\][^\n]*\n?', '', text)

        # Clean up multiple newlines
        text = re.sub(r'\n{3,}', '\n\n', text)

        return text.strip()

    async def enhance_with_api_data(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Enhance a message with relevant API data.
        Detects questions about the system and fetches relevant info.
        """
        enhancements = []

        # Check for questions about models
        if re.search(r'(?:which|what|list|available)\s+models?', message, re.I):
            result = await self.execute_call('ollama.list', {})
            if result.get('success'):
                models = result.get('result', {}).get('models', [])
                model_names = [m.get('name') for m in models[:10]]
                enhancements.append(f"[System Info: Available models: {', '.join(model_names)}]")

        # Check for questions about system status
        if re.search(r'(?:system|tristar|backend)\s+(?:status|health)', message, re.I):
            result = await self.execute_call('tristar.status', {})
            if result.get('success'):
                status = result.get('result', {})
                services = status.get('services', {})
                active = [k for k, v in services.items() if v == 'active']
                enhancements.append(f"[System Info: Active services: {', '.join(active)}]")

        # Check for questions about agents
        if re.search(r'(?:which|what|list|available)\s+agents?', message, re.I):
            result = await self.execute_call('tristar.agents', {})
            if result.get('success'):
                agents = result.get('result', {}).get('agents', [])
                agent_ids = [a.get('id', 'unknown') for a in agents[:10]]
                enhancements.append(f"[System Info: Configured agents: {', '.join(agent_ids)}]")

        if enhancements:
            return message + '\n\n' + '\n'.join(enhancements)

        return message


# Singleton instance
mcp_filter = MCPFilter()


# ============================================================================
# Convenience functions
# ============================================================================

async def filter_and_execute(message: str) -> Tuple[str, List[Dict[str, Any]]]:
    """Process message and execute MCP calls."""
    return await mcp_filter.process_message(message)


def remove_mcp_display(text: str) -> str:
    """Remove MCP commands from display text."""
    return mcp_filter.filter_mcp_from_display(text)


async def enhance_message(message: str) -> str:
    """Enhance message with API data."""
    return await mcp_filter.enhance_with_api_data(message)


# ============================================================================
# Mesh AI Role-Based Command Filtering
# ============================================================================

class MeshCommandFilter:
    """
    Role-based MCP command filtering for Mesh AI agents.

    - Lead agents: coordination/read access only, never implicit admin
    - Worker agents: explicit allow-list; dangerous commands queued
    - Reviewer agents: read-only access
    - Unknown roles/commands: denied by default
    """

    # Commands that Workers can execute directly
    WORKER_ALLOWED = {
        # Read operations
        'ollama.list', 'ollama.ps', 'ollama.show', 'ollama.health',
        'tristar.status', 'tristar.agents', 'tristar.prompts.list',
        'tristar.settings', 'tristar.logs',
        'codebase.structure', 'codebase.file', 'codebase.search',
        'codebase.routes', 'codebase.services',
        'memory.search', 'tristar.memory.search',
        'mesh.status', 'mesh.agents', 'mesh.tasks',
        'queue.status', 'queue.agents',
        # Safe LLM operations
        'chat', 'llm.invoke', 'tristar.models',
    }

    # Leads coordinate but do not inherit mutation/admin authority.
    LEAD_ALLOWED = WORKER_ALLOWED | {
        'queue.get', 'llm.consensus', 'llm.broadcast',
    }

    # Commands that must be queued for Workers
    WORKER_QUEUE = {
        # Write operations
        'file.write', 'file.delete', 'file.create',
        'git.commit', 'git.push', 'git.merge', 'git.reset',
        'shell.exec', 'bash.run', 'exec.',
        'code.execute', 'python.run', 'node.run',
        'memory.store', 'tristar.memory.store',
        'ollama.pull', 'ollama.delete', 'ollama.create', 'ollama.copy',
        'posts.create', 'media.upload',
        'tristar.prompts.set', 'tristar.prompts.delete',
        'tristar.settings.set',
        'cli-agents.start', 'cli-agents.stop', 'cli-agents.restart',
    }

    # Commands that are denied for Workers
    WORKER_DENIED = {
        'admin.', 'system.shutdown', 'config.delete',
        'secrets.', 'credentials.', 'auth.token',
    }

    # Reviewer can only read
    REVIEWER_ALLOWED = {
        'ollama.list', 'ollama.ps', 'ollama.show', 'ollama.health',
        'tristar.status', 'tristar.agents', 'tristar.prompts.list',
        'tristar.settings', 'tristar.logs',
        'codebase.structure', 'codebase.file', 'codebase.search',
        'codebase.routes', 'codebase.services',
        'memory.search', 'tristar.memory.search',
        'mesh.status', 'mesh.agents', 'mesh.tasks',
        'queue.status', 'queue.agents', 'queue.get',
    }

    def __init__(self):
        self._audit_log: List[Dict[str, Any]] = []

    async def filter_command(
        self,
        command: str,
        agent_id: str,
        agent_role: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Filter an MCP command based on agent role.

        Returns:
            {
                "action": "allow" | "queue" | "deny",
                "reason": str,
                "queued_id": Optional[str]  # If queued
            }
        """
        from datetime import datetime, timezone

        result = {
            "command": command,
            "agent_id": agent_id,
            "agent_role": agent_role,
            "action": "allow",
            "reason": "",
            "queued_id": None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # A role string supplied by an agent is not authority. Only the two
        # reserved internal principals may ever exercise this legacy admin path.
        if agent_role == "admin":
            if agent_id not in {"tristar_kernel", "system"}:
                result["action"] = "deny"
                result["reason"] = "Admin authority cannot be self-asserted by an agent"
                self._log_audit(result)
                return result
            result["reason"] = "Trusted internal system principal"
            self._log_audit(result)
            return result

        if agent_role == "lead":
            if command in self.LEAD_ALLOWED:
                result["reason"] = "Lead coordination command explicitly allowed"
            else:
                result["action"] = "deny"
                result["reason"] = "Lead role is coordination-only and has no implicit mutation authority"
            self._log_audit(result)
            return result

        # Check denied commands first
        for denied in self.WORKER_DENIED:
            if command.startswith(denied):
                result["action"] = "deny"
                result["reason"] = f"Command pattern '{denied}' is denied for {agent_role}"
                self._log_audit(result)
                return result

        # Check role-specific permissions
        if agent_role == "worker":
            return await self._filter_worker(command, params, result)
        elif agent_role == "reviewer":
            return await self._filter_reviewer(command, result)
        else:
            # Unknown role - default deny
            result["action"] = "deny"
            result["reason"] = f"Unknown role: {agent_role}"
            self._log_audit(result)
            return result

    async def _filter_worker(
        self,
        command: str,
        params: Dict[str, Any],
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Filter commands for Worker role."""
        # Check if explicitly allowed
        if command in self.WORKER_ALLOWED:
            result["reason"] = "Command is in worker allowed list"
            self._log_audit(result)
            return result

        # Check if should be queued
        for queue_pattern in self.WORKER_QUEUE:
            if command.startswith(queue_pattern) or command == queue_pattern:
                # Queue the command
                from .mesh_coordinator import queue_mcp_command
                cmd = await queue_mcp_command(
                    source=result["agent_id"],
                    command=command,
                    params=params,
                )
                result["action"] = "queue"
                result["reason"] = f"Command queued for TriForce execution"
                result["queued_id"] = cmd.id
                self._log_audit(result)
                return result

        # Unknown worker commands are denied. New tools must be classified
        # explicitly instead of silently inheriting execution authority.
        result["action"] = "deny"
        result["reason"] = "Command is not explicitly allowed or queued for worker role"
        self._log_audit(result)
        return result

    async def _filter_reviewer(
        self,
        command: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Filter commands for Reviewer role (read-only)."""
        if command in self.REVIEWER_ALLOWED:
            result["reason"] = "Command is in reviewer allowed list"
            self._log_audit(result)
            return result

        # All other commands denied for reviewers
        result["action"] = "deny"
        result["reason"] = "Reviewers have read-only access"
        self._log_audit(result)
        return result

    def _log_audit(self, result: Dict[str, Any]):
        """Log filter decision."""
        self._audit_log.append(result.copy())
        # Keep only last 500 entries
        if len(self._audit_log) > 500:
            self._audit_log = self._audit_log[-250:]

        # Log level based on action
        if result["action"] == "deny":
            logger.warning(f"MESH DENIED: {result['command']} from {result['agent_id']}")
        elif result["action"] == "queue":
            logger.info(f"MESH QUEUED: {result['command']} -> {result['queued_id']}")

    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent audit entries."""
        return self._audit_log[-limit:]


# Singleton for Mesh filtering
mesh_command_filter = MeshCommandFilter()


# ============================================================================
# Mesh Filter MCP Tools
# ============================================================================

MESH_FILTER_TOOLS = [
    {
        "name": "mesh_filter_check",
        "description": "Prüft ob ein MCP Command für einen Mesh Agent erlaubt ist",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "MCP Command Name"},
                "agent_id": {"type": "string", "description": "Agent ID"},
                "agent_role": {"type": "string", "enum": ["lead", "worker", "reviewer", "admin"]},
            },
            "required": ["command", "agent_id", "agent_role"],
        },
    },
    {
        "name": "mesh_filter_audit",
        "description": "Zeigt das Audit-Log der Mesh Filter-Entscheidungen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 100},
            },
        },
    },
]


async def handle_mesh_filter_check(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_filter_check tool."""
    command = params.get("command")
    agent_id = params.get("agent_id")
    agent_role = params.get("agent_role")

    if not all([command, agent_id, agent_role]):
        raise ValueError("'command', 'agent_id', and 'agent_role' are required")

    return await mesh_command_filter.filter_command(
        command=command,
        agent_id=agent_id,
        agent_role=agent_role,
        params={},
    )


async def handle_mesh_filter_audit(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handle mesh_filter_audit tool."""
    limit = params.get("limit", 100)
    return {
        "audit_log": mesh_command_filter.get_audit_log(limit),
        "total_logged": len(mesh_command_filter._audit_log),
    }


MESH_FILTER_HANDLERS = {
    "mesh_filter_check": handle_mesh_filter_check,
    "mesh_filter_audit": handle_mesh_filter_audit,
}
