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
            "code_patch", "wp_publish_post",
            "n8n_workflow_create",
        }
        # code_read is an ALIAS of file_read, not a retired tool: the browser
        # surface (READ_TOOLS) and the shipped Android client 2.90.11
        # (readCaps) both advertise it, and execute() maps it onto the same
        # implementation. Dropping it from the registry would break those
        # clients, so it stays until the tool registry work (P7) retires the
        # alias deliberately.
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

class TestCanonicalRegistryAudit(unittest.TestCase):
    def test_inventory_covers_every_canonical_tool_with_required_p7_fields(self):
        from app.mcp.tool_registry_audit import build_canonical_inventory
        from app.mcp.tool_registry_unified import get_canonical_all_tools

        records = build_canonical_inventory(events=[])
        self.assertEqual(
            {item["name"] for item in records},
            {tool["name"] for tool in get_canonical_all_tools()},
        )
        required = {
            "name", "schema", "category", "effect", "privilege", "platforms",
            "required_capabilities", "duplicate_of", "latency_ms", "error_rate",
            "usage", "source", "classification",
        }
        for item in records:
            self.assertTrue(required.issubset(item), item["name"])
            self.assertIn(item["category"].split(".", 1)[0], {
                "core", "share", "workspace", "compute", "dev", "system",
                "service", "process", "network", "security", "display", "input",
                "container", "logs", "package", "hardware", "custom",
            })

    def test_known_code_read_compatibility_alias_is_explicit(self):
        from app.mcp.tool_registry_audit import build_canonical_inventory

        by_name = {item["name"]: item for item in build_canonical_inventory(events=[])}
        self.assertEqual(by_name["code_read"]["classification"], "ALIAS")
        self.assertEqual(by_name["code_read"]["duplicate_of"], "file_read")
        self.assertEqual(by_name["file_read"]["classification"], "KEEP")

    def test_metrics_are_measured_not_invented(self):
        from app.mcp.tool_registry_audit import build_canonical_inventory

        events = [
            {"tool": "file_read", "latency_ms": 10, "status": "success"},
            {"tool": "file_read", "latency_ms": 30, "status": "error"},
        ]
        by_name = {item["name"]: item for item in build_canonical_inventory(events=events)}
        self.assertEqual(by_name["file_read"]["usage"], 2)
        self.assertEqual(by_name["file_read"]["latency_ms"], 20.0)
        self.assertEqual(by_name["file_read"]["error_rate"], 0.5)
        self.assertEqual(by_name["status"]["usage"], 0)
        self.assertIsNone(by_name["status"]["latency_ms"])
        self.assertIsNone(by_name["status"]["error_rate"])

    def test_toolset_hash_ignores_volatile_metrics(self):
        from app.mcp.tool_registry_audit import build_canonical_inventory, toolset_descriptor

        empty = build_canonical_inventory(events=[])
        measured = build_canonical_inventory(events=[
            {"tool": "file_read", "latency_ms": 12.5, "status": "success"},
        ])
        self.assertEqual(toolset_descriptor(empty), toolset_descriptor(measured))
        self.assertEqual(toolset_descriptor(empty)["count"], len(empty))


    def test_explicit_read_only_hints_agree_with_runtime_effect(self):
        from app.mcp.tool_registry_audit import build_canonical_inventory
        from app.mcp.tool_registry_unified import get_canonical_all_tools

        audit = {item["name"]: item for item in build_canonical_inventory(events=[])}
        for tool in get_canonical_all_tools():
            annotations = tool.get("annotations") or {}
            if "readOnlyHint" not in annotations:
                continue
            expected_read = audit[tool["name"]]["effect"] == "read"
            self.assertEqual(bool(annotations["readOnlyHint"]), expected_read, tool["name"])


    def test_share_mutations_are_never_classified_read_safe(self):
        from app.mcp.runtime_registry import RuntimeToolRegistry
        from app.services.share_manifest import CLIPBOARD_WRITE_TOOLS, WORKSPACE_WRITE_TOOLS

        registry = RuntimeToolRegistry()
        mutations = set(WORKSPACE_WRITE_TOOLS) | set(CLIPBOARD_WRITE_TOOLS) | {"workspace_pair"}
        for name in sorted(mutations):
            defaults = registry._policy_defaults(name, "workspace", "test")
            self.assertFalse(defaults["read_only"], name)
            self.assertEqual(registry._classify_tool(name, defaults), "write_scoped", name)
        self.assertTrue(registry._policy_defaults("workspace_clear", "workspace", "test")["destructive"])

    def test_mixed_or_destructive_admin_tools_use_strongest_policy(self):
        from app.mcp.runtime_registry import RuntimeToolRegistry

        registry = RuntimeToolRegistry()
        docker = registry._policy_defaults("docker_stack", "admin", "test")
        self.assertFalse(docker["read_only"])
        self.assertTrue(docker["destructive"])
        self.assertEqual(registry._classify_tool("docker_stack", docker), "write_privileged")

        history = registry._policy_defaults("memory_history", "memory", "test")
        self.assertFalse(history["read_only"])
        self.assertEqual(registry._classify_tool("memory_history", history), "write_privileged")


    def test_tools_list_exposes_hash_for_exact_visible_contract(self):
        import asyncio
        from app.mcp.tool_registry_audit import TOOLSET_VERSION, advertised_toolset_descriptor
        from app.routes.mcp import handle_tools_list

        payload = asyncio.run(handle_tools_list({"inventory": "all"}))
        self.assertEqual(payload["toolset"]["version"], TOOLSET_VERSION)
        self.assertEqual(payload["toolset"]["count"], payload["count"])
        self.assertEqual(payload["toolset"], advertised_toolset_descriptor(payload["tools"]))

    def test_visible_toolset_hash_is_order_independent_but_contract_sensitive(self):
        from copy import deepcopy
        from app.mcp.tool_registry_audit import advertised_toolset_descriptor

        tools = [
            {"name": "b", "description": "B", "inputSchema": {"type": "object"}},
            {"name": "a", "description": "A", "inputSchema": {"type": "object"}},
        ]
        original = advertised_toolset_descriptor(tools)
        self.assertEqual(original, advertised_toolset_descriptor(list(reversed(tools))))
        changed = deepcopy(tools)
        changed[0]["description"] = "changed"
        self.assertNotEqual(original["hash"], advertised_toolset_descriptor(changed)["hash"])
