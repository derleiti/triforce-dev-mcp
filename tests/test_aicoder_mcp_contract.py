"""Dependency-light contract tests for the external ai-coder MCP profile."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest


_MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "utils" / "mcp_security.py"
_SPEC = importlib.util.spec_from_file_location("aicoder_mcp_security_contract", _MODULE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_SECURITY = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SECURITY)

_NORMALIZER_PATH = Path(__file__).resolve().parents[1] / "app" / "utils" / "tool_normalizer.py"
_NORMALIZER_SPEC = importlib.util.spec_from_file_location(
    "aicoder_tool_normalizer_contract", _NORMALIZER_PATH,
)
assert _NORMALIZER_SPEC is not None and _NORMALIZER_SPEC.loader is not None
_NORMALIZER = importlib.util.module_from_spec(_NORMALIZER_SPEC)
_NORMALIZER_SPEC.loader.exec_module(_NORMALIZER)

AI_CODER_TOOL_ALLOWLIST = _SECURITY.AI_CODER_TOOL_ALLOWLIST
EXTERNAL_TOOL_ALLOWLIST_FULL = _SECURITY.EXTERNAL_TOOL_ALLOWLIST_FULL
filter_tools_for_external = _SECURITY.filter_tools_for_external
is_tool_allowed = _SECURITY.is_tool_allowed


class FakeRequest:
    def __init__(self, profile="ai-coder", *, full_access=False):
        self.headers = {
            "X-Client-Profile": profile,
            "X-Forwarded-For": "203.0.113.20",
        }
        self.client = SimpleNamespace(host="203.0.113.20")
        self.state = SimpleNamespace(mcp_auth_full_access=full_access)


class AiCoderMcpContractTests(unittest.TestCase):
    def test_ai_coder_profile_is_identity_not_extra_allowlist(self):
        self.assertEqual(AI_CODER_TOOL_ALLOWLIST, EXTERNAL_TOOL_ALLOWLIST_FULL)

    def test_ai_coder_external_catalog_matches_normal_external_catalog(self):
        catalog = [
            {"name": "health", "x_scope": "global"},
            {"name": "service_status", "x_scope": "global"},
            {"name": "mail_read", "x_scope": "triforce_auth"},
            {"name": "shell", "x_scope": "triforce_admin"},
            {"name": "memory_clear", "x_scope": "triforce_admin"},
        ]
        ai_visible = filter_tools_for_external(catalog, request=FakeRequest())
        normal_visible = filter_tools_for_external(catalog, request=FakeRequest(profile=""))
        self.assertEqual(ai_visible, normal_visible)
        visible = {tool["name"]: tool for tool in ai_visible}
        self.assertIn("service_status", visible)
        self.assertTrue(visible["mail_read"]["x_requires_auth"])
        self.assertNotIn("shell", visible)
        self.assertNotIn("memory_clear", visible)

    def test_privileged_tools_still_require_backend_full_access(self):
        external = FakeRequest()
        full = FakeRequest(full_access=True)
        for name in ("shell", "task_runner", "service_control", "code_edit", "memory_clear"):
            with self.subTest(tool=name):
                self.assertFalse(is_tool_allowed(name, external))
                self.assertTrue(is_tool_allowed(name, full))

    def test_nonprivileged_operator_tools_are_not_denied_by_ai_coder_profile(self):
        request = FakeRequest()
        for name in ("health", "service_status", "code_read", "memory_search"):
            with self.subTest(tool=name):
                self.assertTrue(is_tool_allowed(name, request))

    def test_canonical_names_survive_legacy_alias_normalization(self):
        request = FakeRequest(profile="")
        self.assertTrue(is_tool_allowed("specialist", request))
        self.assertTrue(is_tool_allowed("crawl", request))

    def test_backend_readonly_metadata_matches_client_approval_semantics(self):
        for name in ("health", "service_status", "code_read", "memory_search"):
            self.assertTrue(_NORMALIZER.is_readonly_tool(name), name)
        self.assertFalse(_NORMALIZER.is_readonly_tool("crawl"))
        self.assertFalse(_NORMALIZER.is_readonly_tool("memory_store"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
