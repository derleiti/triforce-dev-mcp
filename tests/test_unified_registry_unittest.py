import unittest
from pathlib import Path

class TestUnifiedRegistry(unittest.TestCase):
    def test_unified_registry_file_exists(self):
        self.assertTrue(Path("app/mcp/tool_registry_unified.py").exists())

    def test_routes_mcp_uses_unified_registry(self):
        text = Path("app/routes/mcp.py").read_text(encoding="utf-8")
        self.assertIn("get_canonical_all_tools", text)
        self.assertIn("filter_tools_for_profile", text)
        self.assertIn('inventory = str(params.get("inventory", "core"))', text)
        self.assertIn("resolve_tool_name_for_call", text)

    def test_examples_prompt_and_docs_exist(self):
        self.assertTrue(Path("docs/mcp_registry_unification_update.md").exists())
        self.assertTrue(Path("prompts/nova_safe_writer_registry_v1.txt").exists())

if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestCanonicalMcpSurface(unittest.TestCase):
    def test_default_surface_stays_small(self):
        import asyncio
        from app.routes.mcp import handle_tools_list
        payload = asyncio.run(handle_tools_list({}))
        names = {tool["name"] for tool in payload["tools"]}
        self.assertLessEqual(payload["count"], 40)
        self.assertIn("shell", names)
        self.assertIn("memory_history", names)
        self.assertIn("code_search", names)

    def test_full_surface_is_canonical_and_bounded(self):
        import asyncio
        from app.mcp.tool_registry_unified import CANONICAL_TOOL_NAMES
        from app.routes.mcp import handle_tools_list
        payload = asyncio.run(handle_tools_list({"inventory": "all"}))
        names = [tool["name"] for tool in payload["tools"]]
        self.assertLessEqual(payload["count"], 80)
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(set(names).issubset(CANONICAL_TOOL_NAMES))

    def test_broken_and_duplicate_tools_are_not_advertised(self):
        import asyncio
        from app.routes.mcp import handle_tools_list
        payload = asyncio.run(handle_tools_list({"inventory": "all"}))
        names = {tool["name"] for tool in payload["tools"]}
        retired = {
            "hive_compress", "hive_recall", "hive_stats",
            "agent_chat_list", "agent_chat_read", "agent_chat_stream",
            "agent_chat_summary", "agent_chat_cleanup",
            "model_performance", "model_recommend",
            "redis_cleanup", "router_dashboard", "router_select",
            "group_chat_status", "flarum_discussion",
            "logs_errors", "logs_stats", "mcp_telemetry", "git_ops",
            "image_search", "ollama_run", "ollama_list",
            "code_read", "code_patch", "wp_publish_post",
            "n8n_workflow_create",
        }
        self.assertFalse(names & retired, names & retired)

class TestCanonicalMcpCompatibilityDelegates(unittest.TestCase):
    def test_service_tools_list_matches_route_exactly(self):
        import asyncio
        from app.routes.mcp import handle_tools_list as route_list
        from app.services.mcp_service import handle_tools_list as service_list

        for inventory in ("core", "all", "memory", "filesystem"):
            params = {"inventory": inventory}
            route_payload = asyncio.run(route_list(params))
            service_payload = asyncio.run(service_list(params))
            self.assertEqual(service_payload, route_payload)

    def test_service_tools_call_uses_canonical_dispatcher(self):
        import asyncio
        from unittest.mock import AsyncMock, patch
        from app.services.mcp_service import handle_tools_call as service_call

        params = {"name": "status", "arguments": {}}
        expected = {"content": [{"type": "text", "text": "canonical"}], "isError": False}
        canonical = AsyncMock(return_value=expected)
        with patch("app.routes.mcp.handle_tools_call", canonical):
            result = asyncio.run(service_call(params))
        self.assertEqual(result, expected)
        canonical.assert_awaited_once_with(params)

    def test_service_initialize_uses_central_version(self):
        import asyncio
        from app.config import VERSION
        from app.services.mcp_service import handle_initialize

        payload = asyncio.run(handle_initialize({"clientInfo": {"name": "compat-test"}}))
        self.assertEqual(payload["serverInfo"]["version"], VERSION)
