"""Authority policy for humans, clients and AI participants.

Roles describe what a principal may attempt. They are NOT self-asserted model
metadata and never grant more authority than the server policy. In particular,
AI roles cannot mint authority, manage roles/policy, or turn advisory messages
into executable assignments without a validated delegation lease.
"""
from __future__ import annotations

from enum import Enum
from typing import Final


class AuthorityRole(str, Enum):
    # Human authority. These must ultimately be bound to a verified human/tenant
    # identity and a step-up capable management channel, never to model output.
    HUMAN_OWNER = "human_owner"
    HUMAN_ADMIN = "human_admin"
    HUMAN_SECURITY_ADMIN = "human_security_admin"
    HUMAN_MANAGER = "human_manager"
    HUMAN_OPERATOR = "human_operator"
    HUMAN_DEVELOPER = "human_developer"
    HUMAN_CONTRIBUTOR = "human_contributor"
    HUMAN_MEMBER = "human_member"
    HUMAN_VIEWER = "human_viewer"

    # Non-human principals. Even AI_COORDINATOR is not an administrator.
    AI_COORDINATOR = "ai_coordinator"
    AI_WORKER = "ai_worker"
    AI_REVIEWER = "ai_reviewer"
    AI_OBSERVER = "ai_observer"

    # Generic application/service endpoint. It has communication authority only
    # until an explicit server-side policy grants a narrower service capability.
    CLIENT_MEMBER = "client_member"
    SERVICE = "service"


HUMAN_AUTHORITY_ROLES: Final[frozenset[AuthorityRole]] = frozenset({
    AuthorityRole.HUMAN_OWNER, AuthorityRole.HUMAN_ADMIN, AuthorityRole.HUMAN_SECURITY_ADMIN,
    AuthorityRole.HUMAN_MANAGER, AuthorityRole.HUMAN_OPERATOR, AuthorityRole.HUMAN_DEVELOPER,
    AuthorityRole.HUMAN_CONTRIBUTOR, AuthorityRole.HUMAN_MEMBER, AuthorityRole.HUMAN_VIEWER,
})

AI_AUTHORITY_ROLES: Final[frozenset[AuthorityRole]] = frozenset({
    AuthorityRole.AI_COORDINATOR,
    AuthorityRole.AI_WORKER,
    AuthorityRole.AI_REVIEWER,
    AuthorityRole.AI_OBSERVER,
})

ROLE_MANAGERS: Final[frozenset[AuthorityRole]] = frozenset({
    AuthorityRole.HUMAN_OWNER,
    AuthorityRole.HUMAN_ADMIN,
})

ROOT_DELEGATORS: Final[frozenset[AuthorityRole]] = frozenset({
    AuthorityRole.HUMAN_OWNER,
    AuthorityRole.HUMAN_ADMIN,
    AuthorityRole.HUMAN_MANAGER,
    AuthorityRole.HUMAN_OPERATOR,
})

# Message kinds other than task are communication/advisory. A task is the only
# Shared Notify speech act that can represent an executable assignment.
ADVISORY_MESSAGE_KINDS: Final[frozenset[str]] = frozenset({
    "human_chat", "request", "review", "coordination", "ai_optimization",
    "brainstorm", "handoff",
})
MCP_REQUEST_MESSAGE_KINDS: Final[frozenset[str]] = frozenset({"mcp_rpc_request"})
MCP_SERVICE_MESSAGE_KINDS: Final[frozenset[str]] = frozenset({"mcp_rpc_result", "mcp_rpc_error", "mcp_share_announce"})

# Safe default send matrix. Receiving still depends on presence gates.
ROLE_MESSAGE_KINDS: Final[dict[AuthorityRole, frozenset[str]]] = {
    AuthorityRole.HUMAN_OWNER: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS | {"task"},
    AuthorityRole.HUMAN_ADMIN: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS | {"task"},
    AuthorityRole.HUMAN_SECURITY_ADMIN: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS | {"task"},
    AuthorityRole.HUMAN_MANAGER: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS | {"task"},
    AuthorityRole.HUMAN_OPERATOR: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS | {"task"},
    AuthorityRole.HUMAN_DEVELOPER: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS,
    AuthorityRole.HUMAN_CONTRIBUTOR: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS,
    AuthorityRole.HUMAN_MEMBER: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS,
    AuthorityRole.HUMAN_VIEWER: frozenset({"human_chat", "request"}),
    AuthorityRole.AI_COORDINATOR: ADVISORY_MESSAGE_KINDS,
    AuthorityRole.AI_WORKER: ADVISORY_MESSAGE_KINDS,
    AuthorityRole.AI_REVIEWER: ADVISORY_MESSAGE_KINDS,
    AuthorityRole.AI_OBSERVER: frozenset({"request", "review", "coordination", "ai_optimization", "brainstorm"}),
    AuthorityRole.CLIENT_MEMBER: ADVISORY_MESSAGE_KINDS | MCP_REQUEST_MESSAGE_KINDS,
    AuthorityRole.SERVICE: frozenset({"request", "coordination", "handoff"}) | MCP_SERVICE_MESSAGE_KINDS,
}

# These capabilities must never be granted by an AI-authored message or an AI
# role alone. They require a human-authority policy/approval path.
HUMAN_ONLY_AUTHORITIES: Final[frozenset[str]] = frozenset({
    "authority:manage",
    "policy:manage",
    "credential:read",
    "credential:export",
    "billing:manage",
    "production:deploy",
    "production:service-control",
    "host:admin",
    "identity:impersonate",
})



# Ordered authority ladder. The numeric level is useful for coarse policy
# thresholds, while the role still determines *which* actions are permitted.
# Higher level never bypasses role-specific denials or human-only invariants.
AUTHORITY_LEVELS: Final[dict[AuthorityRole, int]] = {
    AuthorityRole.AI_OBSERVER: 10,
    AuthorityRole.HUMAN_VIEWER: 10,
    AuthorityRole.CLIENT_MEMBER: 20,
    AuthorityRole.HUMAN_MEMBER: 20,
    AuthorityRole.HUMAN_CONTRIBUTOR: 30,
    AuthorityRole.AI_REVIEWER: 30,
    AuthorityRole.AI_WORKER: 40,
    AuthorityRole.HUMAN_DEVELOPER: 50,
    AuthorityRole.AI_COORDINATOR: 50,
    AuthorityRole.SERVICE: 50,
    AuthorityRole.HUMAN_OPERATOR: 60,
    AuthorityRole.HUMAN_MANAGER: 70,
    AuthorityRole.HUMAN_SECURITY_ADMIN: 80,
    AuthorityRole.HUMAN_ADMIN: 90,
    AuthorityRole.HUMAN_OWNER: 100,
}

WP_ROLE_TO_AUTHORITY: Final[dict[str, AuthorityRole]] = {
    "subscriber": AuthorityRole.HUMAN_MEMBER,
    "contributor": AuthorityRole.HUMAN_CONTRIBUTOR,
    "author": AuthorityRole.HUMAN_CONTRIBUTOR,
    "editor": AuthorityRole.HUMAN_OPERATOR,
    "administrator": AuthorityRole.HUMAN_ADMIN,
}


def authority_level(role: str | AuthorityRole) -> int:
    return AUTHORITY_LEVELS.get(parse_authority_role(role), 0)


def authority_from_wordpress(*, wp_roles: object, can_admin: bool = False, is_owner: bool = False, explicit_role: str = "") -> AuthorityRole:
    """Translate trusted WordPress account state into human authority.

    This accepts only server-observed WordPress state. Callers must not pass
    client-supplied role strings. `is_owner` is reserved for the configured
    primary AILinux owner identity.
    """
    if is_owner:
        return AuthorityRole.HUMAN_OWNER
    if explicit_role:
        try:
            explicit = parse_authority_role(explicit_role)
        except PermissionError:
            explicit = None
        # Owner cannot be delegated through WordPress user meta. All other human
        # roles are valid only because this value came from the trusted WP backend.
        if explicit in HUMAN_AUTHORITY_ROLES and explicit is not AuthorityRole.HUMAN_OWNER:
            return explicit
    roles = {str(item).strip().lower() for item in (wp_roles or []) if str(item).strip()} if isinstance(wp_roles, (list, tuple, set)) else set()
    if can_admin or "administrator" in roles:
        return AuthorityRole.HUMAN_ADMIN
    best = AuthorityRole.HUMAN_MEMBER
    for wp_role in roles:
        candidate = WP_ROLE_TO_AUTHORITY.get(wp_role)
        if candidate and authority_level(candidate) > authority_level(best):
            best = candidate
    return best

def default_role_for_kind(kind: str) -> AuthorityRole:
    kind = str(kind or "").strip().lower()
    if kind == "human":
        return AuthorityRole.HUMAN_MEMBER
    if kind in {"ai", "agent", "model"}:
        return AuthorityRole.AI_OBSERVER
    if kind in {"service", "mcp"}:
        return AuthorityRole.SERVICE
    return AuthorityRole.CLIENT_MEMBER


def parse_authority_role(value: str | AuthorityRole) -> AuthorityRole:
    if isinstance(value, AuthorityRole):
        return value
    try:
        return AuthorityRole(str(value or "").strip().lower())
    except ValueError as exc:
        raise PermissionError("unknown authority role") from exc


def is_ai_role(role: str | AuthorityRole) -> bool:
    return parse_authority_role(role) in AI_AUTHORITY_ROLES


def can_manage_roles(role: str | AuthorityRole) -> bool:
    return parse_authority_role(role) in ROLE_MANAGERS


def can_issue_root_delegation(role: str | AuthorityRole) -> bool:
    return parse_authority_role(role) in ROOT_DELEGATORS


def message_authority(kind: str) -> str:
    return "assignment" if str(kind or "").strip().lower() == "task" else "advisory"


def authorize_message(
    role: str | AuthorityRole,
    kind: str,
    *,
    delegated_task: bool = False,
) -> None:
    """Fail closed when a principal attempts a Shared Notify speech act.

    AI task assignment is intentionally disabled by role alone. A future/active
    delegation lease may set ``delegated_task=True`` only after server-side
    validation that the lease descends from human authority and has not expired.
    """
    parsed = parse_authority_role(role)
    kind = str(kind or "").strip().lower()
    allowed = ROLE_MESSAGE_KINDS.get(parsed, frozenset())
    if kind == "task" and parsed in AI_AUTHORITY_ROLES:
        if delegated_task and parsed == AuthorityRole.AI_COORDINATOR:
            return
        raise PermissionError("AI task assignment requires a validated human-derived delegation")
    if kind not in allowed:
        raise PermissionError(f"authority role '{parsed.value}' cannot send '{kind}' messages")
