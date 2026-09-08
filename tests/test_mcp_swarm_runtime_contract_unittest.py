import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.routes import mcp as route_mcp


class McpSwarmRuntimeContractTests(unittest.TestCase):
    def test_v5_group_chat_and_agent_tools_have_runtime_handlers(self):
        payload = asyncio.run(route_mcp.handle_tools_list({"inventory": "all"}))
        exported = {tool["name"] for tool in payload["tools"]}
        required = {
            "group_chat_create",
            "group_chat_ask",
            "group_chat_read",
            "group_chat_consolidate",
            "group_chat_assign",
            "agents",
            "agent_call",
            "agent_broadcast",
            "agent_start",
            "agent_stop",
        }

        missing_exports = required - exported
        missing_handlers = required - set(route_mcp.MCP_HANDLERS)

        self.assertEqual(missing_exports, set())
        self.assertEqual(missing_handlers, set())

    def test_tools_call_dispatches_group_chat_create(self):
        result = asyncio.run(route_mcp.handle_tools_call({
            "name": "group_chat_create",
            "arguments": {
                "topic": "contract smoke test",
                "participants": ["claude-web", "chatgpt-web"],
            },
        }))

        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertIn("session", payload)
        self.assertEqual(payload["session"]["topic"], "contract smoke test")

    def test_direct_agent_call_accepts_legacy_agent_and_command_aliases(self):
        fake_controller = AsyncMock()
        fake_controller.call_agent.return_value = {"status": "success", "response": "OK"}

        with patch("app.services.tristar.agent_controller.agent_controller", fake_controller):
            result = asyncio.run(route_mcp.handle_cli_agents_call({
                "agent": "gemini-mcp",
                "command": "ping",
                "timeout": 7,
            }))

        self.assertEqual(result["status"], "success")
        fake_controller.call_agent.assert_awaited_once_with("gemini-mcp", "ping", timeout=7)

    def test_direct_agent_broadcast_accepts_command_and_agents_aliases(self):
        fake_controller = AsyncMock()
        fake_controller.broadcast.return_value = {"broadcast": True, "results": {}}

        with patch("app.services.tristar.agent_controller.agent_controller", fake_controller):
            result = asyncio.run(route_mcp.handle_cli_agents_broadcast({
                "command": "ping all",
                "agents": ["gemini-mcp"],
            }))

        self.assertTrue(result["broadcast"])
        fake_controller.broadcast.assert_awaited_once_with("ping all", agent_ids=["gemini-mcp"])

    def test_tools_call_dispatches_agent_broadcast_message_without_command_error(self):
        fake_controller = AsyncMock()
        fake_controller.broadcast.return_value = {"broadcast": True, "results": {}}

        with patch("app.services.tristar.agent_controller.agent_controller", fake_controller):
            result = asyncio.run(route_mcp.handle_tools_call({
                "name": "agent_broadcast",
                "arguments": {
                    "message": "contract smoke",
                    "agent_ids": ["gemini-mcp"],
                },
            }))

        self.assertFalse(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload["broadcast"])
        fake_controller.broadcast.assert_awaited_once_with("contract smoke", agent_ids=["gemini-mcp"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
