from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..services.node_registry import node_registry
from ..utils.tool_normalizer import normalize_tool_name

Handler = Callable[[Dict[str, Any]], Awaitable[Any]]


@dataclass
class RuntimeToolEntry:
    name: str
    tool_id: str
    spec: Dict[str, Any]
    handler: Optional[Handler] = None
    aliases: List[str] | None = None
    inventory: str = "misc"
    classification: str = "read_safe"
    supports_prepare: bool = False
    supports_preview: bool = False
    supports_apply: bool = True
    read_only: bool = False
    destructive: bool = False
    open_world: bool = False
    client_visible: bool = True
    remote_visible: bool = True
    requires_file_write: bool = False
    requires_shell: bool = False
    path_sensitive: bool = False
    min_tier: str = "free"
    source: str = "runtime"


class RuntimeToolRegistry:
    def __init__(self) -> None:
        self._entries: Dict[str, RuntimeToolEntry] = {}
        self._aliases: Dict[str, str] = {}
        self._loaded = False
        self._load_errors: List[str] = []

    def _policy_defaults(self, name: str, inventory: str, source: str) -> Dict[str, Any]:
        open_world = name in {"search", "web_search", "smart_search", "multi_search", "crawl", "crawl_url", "crawl_site", "fetch", "image_search"}
        requires_file_write = name in {"file_write", "codebase_edit", "code_edit", "code_patch", "file_ops"}
        requires_shell = name in {"shell", "bash_exec", "tristar_shell_exec", "task_runner", "binary_exec", "custom_exec", "remote_admin"}
        destructive = name in {"shell", "task_runner", "binary_exec", "remote_admin", "ollama_delete", "memory_clear", "queue_clear"}
        read_only = not (requires_file_write or requires_shell or destructive or name in {"prompt_set", "config_set", "service_control", "container_control", "package_manager", "vault_add", "mail_send", "wp_create_draft", "wp_update_post", "flarum_post_create", "flarum_post_edit", "flarum_discussion_create", "flarum_admin_request", "notify_send", "notify_clear", "notify_read", "idle_assign"})
        min_tier = "enterprise" if (requires_file_write or requires_shell or destructive) else "free"
        path_sensitive = name in {"file_read", "file_write", "file_ops", "code_read", "code_edit", "code_patch", "codebase_edit", "codebase_file"}
        client_visible = name not in {"initialize", "tools/list", "tools/call"}
        remote_visible = name not in {"initialize", "tools/list", "tools/call"}
        return {
            "inventory": inventory,
            "read_only": read_only,
            "destructive": destructive,
            "open_world": open_world,
            "client_visible": client_visible,
            "remote_visible": remote_visible,
            "requires_file_write": requires_file_write,
            "requires_shell": requires_shell,
            "path_sensitive": path_sensitive,
            "min_tier": min_tier,
        }

    def _classify_tool(self, name: str, defaults: Dict[str, Any]) -> str:
        if name in {"initialize", "tools/list", "tools/call", "prompts/list", "prompts/get", "resources/list", "resources/read"}:
            return "internal_only"
        if name in {"shell", "bash_exec", "tristar_shell_exec", "task_runner", "binary_exec", "custom_exec", "remote_admin", "remote_exec"}:
            return "exec_privileged"
        if name in {"config_set", "prompt_set", "service_control", "container_control", "package_manager", "vault_add", "restart", "restart_backend", "restart_agent", "flarum_admin_request"}:
            return "write_privileged"
        if name in {"file_write", "file_ops", "code_edit", "code_patch", "codebase_edit", "codebase_create", "wp_create_draft", "wp_update_post", "mail_send", "flarum_post_create", "flarum_post_edit", "flarum_discussion_create", "notify_send", "notify_clear", "notify_read", "idle_assign"}:
            return "write_scoped"
        if name in {"debug", "debug_mcp_request", "tool_introspect", "binary_list", "template_list", "task_reference", "check_compatibility"}:
            return "preview_safe"
        if defaults.get("read_only", False):
            return "read_safe"
        return "write_scoped"

    def _capability_defaults(self, name: str, classification: str) -> Dict[str, bool]:
        supports_preview = classification in {"preview_safe", "write_scoped", "write_privileged", "exec_privileged"}
        supports_prepare = classification in {"write_scoped", "write_privileged", "exec_privileged"}
        supports_apply = classification != "internal_only"
        if name in {"code_edit", "code_patch", "codebase_edit", "file_ops"}:
            supports_preview = True
            supports_prepare = True
        return {
            "supports_preview": supports_preview,
            "supports_prepare": supports_prepare,
            "supports_apply": supports_apply,
        }

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.refresh()

    def refresh(self) -> None:
        self._entries = {}
        self._aliases = {}
        self._load_errors = []
        self._load_from_main()
        self._load_from_remote()
        self._loaded = True

    def _register_entry(self, spec: Dict[str, Any], handler: Optional[Handler], *, aliases: Optional[List[str]] = None, inventory: Optional[str] = None, source: str = "runtime") -> None:
        name = normalize_tool_name(spec.get("name", ""))
        if not name:
            return
        item = deepcopy(spec)
        item["name"] = name
        if "inputSchema" not in item:
            item["inputSchema"] = {"type": "object", "properties": {}, "required": []}
        inv = inventory or item.get("x_inventory") or "misc"
        defaults = self._policy_defaults(name, inv, source)
        classification = self._classify_tool(name, defaults)
        capabilities = self._capability_defaults(name, classification)
        annotations = item.get("annotations") or {}
        annotations.setdefault("readOnlyHint", defaults["read_only"])
        annotations.setdefault("destructiveHint", defaults["destructive"])
        annotations.setdefault("openWorldHint", defaults["open_world"])
        annotations.setdefault("idempotentHint", not defaults["destructive"])
        annotations.setdefault("title", item.get("description", name)[:80])
        item["annotations"] = annotations
        item["x_inventory"] = inv
        item["x_tool_id"] = name
        item["x_classification"] = classification
        item["x_prepare_supported"] = capabilities["supports_prepare"]
        item["x_preview_supported"] = capabilities["supports_preview"]
        item["x_apply_supported"] = capabilities["supports_apply"]
        entry = RuntimeToolEntry(
            name=name,
            tool_id=name,
            spec=item,
            handler=handler,
            aliases=aliases or [],
            inventory=inv,
            classification=classification,
            supports_prepare=capabilities["supports_prepare"],
            supports_preview=capabilities["supports_preview"],
            supports_apply=capabilities["supports_apply"],
            read_only=annotations.get("readOnlyHint", defaults["read_only"]),
            destructive=annotations.get("destructiveHint", defaults["destructive"]),
            open_world=annotations.get("openWorldHint", defaults["open_world"]),
            client_visible=defaults["client_visible"],
            remote_visible=defaults["remote_visible"],
            requires_file_write=defaults["requires_file_write"],
            requires_shell=defaults["requires_shell"],
            path_sensitive=defaults["path_sensitive"],
            min_tier=defaults["min_tier"],
            source=source,
        )
        existing = self._entries.get(name)
        if existing and existing.handler and not handler:
            entry.handler = existing.handler
        self._entries[name] = entry
        for alias in aliases or []:
            alias_name = normalize_tool_name(alias)
            if alias_name and alias_name != name:
                self._aliases[alias_name] = name

    def _load_from_main(self) -> None:
        try:
            from ..routes import mcp as mcp_routes
            from ..mcp.tool_registry_unified import get_unified_tools, get_inventory_map, resolve_tool_name_for_call
            tools = get_unified_tools()
            inventory_map = get_inventory_map(tools)
            inv_by_tool = {tool: inv for inv, items in inventory_map.items() for tool in items}
            handlers = getattr(mcp_routes, "MCP_HANDLERS", {})
            for tool in tools:
                original_name = normalize_tool_name(tool.get("name", ""))
                canonical = resolve_tool_name_for_call(original_name)
                aliases: List[str] = []
                cloned = deepcopy(tool)
                if canonical != original_name:
                    aliases.append(original_name)
                    cloned["name"] = canonical
                self._register_entry(cloned, handlers.get(canonical) or handlers.get(original_name), aliases=aliases, inventory=inv_by_tool.get(canonical) or inv_by_tool.get(original_name), source="main")
            for name, handler in handlers.items():
                canonical = resolve_tool_name_for_call(name)
                entry = self._entries.get(canonical)
                aliases = [name] if canonical != normalize_tool_name(name) else []
                if entry:
                    self._register_entry(entry.spec, handler, aliases=list(set((entry.aliases or []) + aliases)), inventory=entry.inventory, source=entry.source)
                elif canonical not in {"initialize", "tools/list", "tools/call", "prompts/list", "prompts/get", "resources/list", "resources/read"}:
                    stub = {"name": canonical, "description": f"Runtime handler for {canonical}", "inputSchema": {"type": "object", "properties": {}, "required": []}}
                    self._register_entry(stub, handler, aliases=aliases, inventory="misc", source="main")
        except Exception as exc:
            self._load_errors.append(f"main:{exc}")

    def _load_from_remote(self) -> None:
        try:
            from ..routes import mcp_remote
            from ..mcp.tool_registry_unified import resolve_tool_name_for_call
            remote_tools: List[Dict[str, Any]] = []
            try:
                remote_tools.extend(mcp_remote.get_tools())
            except Exception:
                pass
            remote_tools.extend(getattr(mcp_remote, "_V4_ALIAS_TOOLS", []))
            handlers = getattr(mcp_remote, "TOOL_HANDLERS", {})
            seen = set()
            for tool in remote_tools:
                original_name = normalize_tool_name(tool.get("name", ""))
                if not original_name or original_name in seen:
                    continue
                seen.add(original_name)
                canonical = resolve_tool_name_for_call(original_name)
                aliases = [original_name] if canonical != original_name else []
                cloned = deepcopy(tool)
                cloned["name"] = canonical
                self._register_entry(cloned, handlers.get(canonical) or handlers.get(original_name), aliases=aliases, inventory=cloned.get("x_inventory") or "misc", source="remote")
            for name, handler in handlers.items():
                canonical = resolve_tool_name_for_call(name)
                entry = self._entries.get(canonical)
                aliases = [name] if canonical != normalize_tool_name(name) else []
                if entry:
                    if not entry.handler:
                        self._register_entry(entry.spec, handler, aliases=list(set((entry.aliases or []) + aliases)), inventory=entry.inventory, source=entry.source)
                elif canonical not in {"initialize", "tools/list", "tools/call"}:
                    stub = {"name": canonical, "description": f"Remote runtime handler for {canonical}", "inputSchema": {"type": "object", "properties": {}, "required": []}}
                    self._register_entry(stub, handler, aliases=aliases, inventory="misc", source="remote")
        except Exception as exc:
            self._load_errors.append(f"remote:{exc}")

    def resolve_alias(self, name: str) -> str:
        self._ensure_loaded()
        normalized = normalize_tool_name(name)
        return self._aliases.get(normalized, normalized)

    def get_entry(self, name: str) -> Optional[RuntimeToolEntry]:
        self._ensure_loaded()
        return self._entries.get(self.resolve_alias(name))

    def resolve_entry(self, name: str) -> Optional[RuntimeToolEntry]:
        return self.get_entry(name)

    def get_handler(self, name: str) -> Optional[Handler]:
        entry = self.get_entry(name)
        return entry.handler if entry else None

    def list_tools(self, *, include_aliases: bool = False, inventory: Optional[str] = None, remote_only: bool = False, client_only: bool = False, node_id: Optional[str] = None, tier: Optional[str] = None) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        profile = node_registry.get_profile(node_id)
        out: List[Dict[str, Any]] = []
        for _, entry in sorted(self._entries.items()):
            if remote_only and not entry.remote_visible:
                continue
            if client_only and not entry.client_visible:
                continue
            if inventory and inventory not in {"all", "*", ""} and entry.inventory != inventory:
                continue
            if tier in {"free", "demo", "software"} and entry.min_tier != "free":
                continue
            if profile == "restricted" and not entry.read_only:
                continue
            tool = deepcopy(entry.spec)
            tool["x_tool_id"] = entry.tool_id
            tool["x_classification"] = entry.classification
            tool["x_prepare_supported"] = entry.supports_prepare
            tool["x_preview_supported"] = entry.supports_preview
            tool["x_apply_supported"] = entry.supports_apply
            if include_aliases:
                tool["x_aliases"] = sorted(set(entry.aliases or []))
            out.append(tool)
        return out

    def resolve_client_profile(self, *, node_id: Optional[str] = None, request_meta: Optional[Dict[str, Any]] = None, tier: Optional[str] = None) -> str:
        profile = node_registry.get_profile(node_id)
        if profile == "restricted":
            return "restricted"

        # Check if request is from internal/trusted source
        source_ip = (request_meta or {}).get("source_ip", "")
        auth_method = (request_meta or {}).get("auth_method", "")
        user = (request_meta or {}).get("user", "")

        # Internal: localhost, WireGuard mesh, Docker bridge
        if source_ip and (
            source_ip in ("127.0.0.1", "::1", "10.10.0.1", "10.10.0.2", "10.10.0.3")
            or source_ip.startswith("172.18.") or source_ip.startswith("172.19.")
        ):
            return "internal_full"
        if auth_method == "basic" and user in ("zombie", "admin"):
            return "internal_full"

        # Tier-based access
        tier_lower = (tier or "free").lower()
        if tier_lower in ("unlimited", "admin"):
            return "internal_full"
        if tier_lower in ("pro", "paid", "subscriber"):
            return "authenticated"

        # Fallback: source_ip is now injected from FastAPI request.
        # If still empty (legacy/internal code path), default to authenticated.
        if not source_ip:
            return "authenticated"

        return "restricted"

    def evaluate_policy(self, entry: RuntimeToolEntry, *, client_profile: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        classification = entry.classification
        if classification == "internal_only":
            return {"decision": "blocked", "reason": "tool is internal_only", "suggested_mode": None}
        if client_profile == "internal_full":
            return {"decision": "allow", "reason": "internal profile allows execution", "suggested_mode": None}
        if classification in {"read_safe", "preview_safe"}:
            return {"decision": "allow", "reason": "safe tool class", "suggested_mode": None}
        if classification == "write_scoped":
            return {"decision": "preview_only", "reason": "profile does not allow direct write execution", "suggested_mode": "preview"}
        if classification in {"write_privileged", "exec_privileged"}:
            return {"decision": "blocked", "reason": "privileged tool class requires internal_full profile", "suggested_mode": "preview" if entry.supports_preview else None}
        return {"decision": "blocked", "reason": "tool policy fallback block", "suggested_mode": None}

    def build_policy_response(self, entry: RuntimeToolEntry, *, client_profile: str, policy: Dict[str, Any], arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        decision = policy.get("decision", "blocked")
        payload = {
            "ok": False,
            "tool": entry.name,
            "tool_id": entry.tool_id,
            "classification": entry.classification,
            "client_profile": client_profile,
            "decision": decision,
            "reason": policy.get("reason"),
            "suggested_mode": policy.get("suggested_mode"),
            "preview_available": bool(entry.supports_preview),
            "prepare_available": bool(entry.supports_prepare),
            "apply_available": bool(entry.supports_apply),
            "arguments_echo": arguments or {},
            "inventory": entry.inventory,
            "source": entry.source,
        }
        if decision == "preview_only":
            payload["error_type"] = "write_requires_preview"
            payload["message"] = "Execution blocked; preview flow required by server policy."
        else:
            payload["error_type"] = "blocked_write"
            payload["message"] = "Execution blocked by server policy."
        return payload

    def introspect_tool(self, name: str, *, node_id: Optional[str] = None, request_meta: Optional[Dict[str, Any]] = None, tier: Optional[str] = None) -> Dict[str, Any]:
        entry = self.resolve_entry(name)
        if not entry:
            return {"ok": False, "error": "tool_not_found", "name": name}
        profile = self.resolve_client_profile(node_id=node_id, request_meta=request_meta, tier=tier)
        policy = self.evaluate_policy(entry, client_profile=profile)
        return {
            "ok": True,
            "tool": entry.name,
            "tool_id": entry.tool_id,
            "aliases": sorted(set(entry.aliases or [])),
            "inventory": entry.inventory,
            "classification": entry.classification,
            "client_visible": entry.client_visible,
            "remote_visible": entry.remote_visible,
            "read_only": entry.read_only,
            "destructive": entry.destructive,
            "requires_file_write": entry.requires_file_write,
            "requires_shell": entry.requires_shell,
            "supports_prepare": entry.supports_prepare,
            "supports_preview": entry.supports_preview,
            "supports_apply": entry.supports_apply,
            "source": entry.source,
            "client_profile": profile,
            "policy": policy,
        }

    def get_inventory_map(self, **kwargs: Any) -> Dict[str, List[str]]:
        tools = self.list_tools(**kwargs)
        result: Dict[str, List[str]] = {}
        for tool in tools:
            inv = tool.get("x_inventory") or "misc"
            result.setdefault(inv, []).append(tool["name"])
        return {k: sorted(v) for k, v in sorted(result.items())}

    def get_client_tools(self, tier: str, node_id: Optional[str] = None) -> List[str]:
        return [tool["name"] for tool in self.list_tools(client_only=True, tier=tier, node_id=node_id)]

    async def call(self, name: str, arguments: Dict[str, Any]) -> Any:
        handler = self.get_handler(name)
        if not handler:
            raise ValueError(f"Tool '{name}' not found in runtime registry")
        return await handler(arguments)

    def health_report(self) -> Dict[str, Any]:
        self._ensure_loaded()
        return {
            "tools": len(self._entries),
            "aliases": len(self._aliases),
            "load_errors": list(self._load_errors),
            "nodes": {k: v.__dict__ for k, v in node_registry.list_nodes().items()},
        }


_registry = RuntimeToolRegistry()


def get_runtime_registry() -> RuntimeToolRegistry:
    return _registry
