"""
RBAC Service v2.60 - Role-Based Access Control for TriForce

Provides granular permission management with 20 permissions across 5 roles.
"""

from enum import Enum
from dataclasses import dataclass
from typing import Set, Dict, Optional, List


class Permission(str, Enum):
    """All available permissions (20)"""
    # Memory Permissions
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_DELETE = "memory:delete"
    MEMORY_ADMIN = "memory:admin"

    # Code Execution Permissions
    CODE_EXEC = "code:exec"
    CODE_LINT = "code:lint"
    DEPS_INSTALL = "deps:install"
    TESTS_RUN = "tests:run"

    # Git Permissions
    GIT_READ = "git:read"
    GIT_WRITE = "git:write"
    GIT_BRANCH = "git:branch"

    # File Permissions
    FILE_READ = "file:read"
    FILE_WRITE = "file:write"
    FILE_DELETE = "file:delete"

    # LLM Mesh Permissions
    LLM_CALL = "llm:call"
    LLM_BROADCAST = "llm:broadcast"
    LLM_CONSENSUS = "llm:consensus"

    # System Permissions
    AUDIT_READ = "audit:read"
    AUDIT_WRITE = "audit:write"
    HEALTH_CHECK = "health:check"

    # Admin Permission (grants all)
    ADMIN_FULL = "admin:full"


class Role(str, Enum):
    """Available roles (5)"""
    ADMIN = "admin"      # Full access
    LEAD = "lead"        # Coordination, LLM calls, audit
    WORKER = "worker"    # Code execution, file ops, git
    REVIEWER = "reviewer"  # Read-only + lint + review
    READER = "reader"    # Minimal read-only access


# Role -> Permissions mapping
ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.ADMIN: set(Permission),  # All permissions

    Role.LEAD: {
        Permission.MEMORY_READ,
        Permission.MEMORY_WRITE,
        Permission.FILE_READ,
        Permission.GIT_READ,
        Permission.LLM_CALL,
        Permission.LLM_BROADCAST,
        Permission.LLM_CONSENSUS,
        Permission.AUDIT_READ,
        Permission.AUDIT_WRITE,
        Permission.HEALTH_CHECK,
    },

    Role.WORKER: {
        Permission.MEMORY_READ,
        Permission.MEMORY_WRITE,
        Permission.CODE_EXEC,
        Permission.CODE_LINT,
        Permission.DEPS_INSTALL,
        Permission.TESTS_RUN,
        Permission.FILE_READ,
        Permission.FILE_WRITE,
        Permission.GIT_READ,
        Permission.GIT_WRITE,
        Permission.GIT_BRANCH,
        Permission.LLM_CALL,
        Permission.HEALTH_CHECK,
    },

    Role.REVIEWER: {
        Permission.MEMORY_READ,
        Permission.CODE_LINT,
        Permission.FILE_READ,
        Permission.GIT_READ,
        Permission.LLM_CALL,
        Permission.AUDIT_READ,
        Permission.HEALTH_CHECK,
    },

    Role.READER: {
        Permission.MEMORY_READ,
        Permission.FILE_READ,
        Permission.GIT_READ,
        Permission.HEALTH_CHECK,
    },
}


# LLM -> Role mapping (default roles for known LLMs)
LLM_ROLES: Dict[str, Role] = {
    # System roles (full admin access)
    "tristar_kernel": Role.ADMIN,
    "system": Role.ADMIN,

    # Lead roles
    "gemini": Role.LEAD,
    "kimi": Role.LEAD,

    # Worker roles
    "deepseek": Role.WORKER,
    "qwen": Role.WORKER,
    "glm": Role.WORKER,
    "minimax": Role.WORKER,
    "claude": Role.WORKER,

    # Reviewer roles
    "cogito": Role.REVIEWER,
    "mistral": Role.REVIEWER,
    "codex": Role.REVIEWER,

    # Named AI assistants are coordinators, never implicit administrators.
    "nova": Role.LEAD,
}


# Tool -> Required Permission mapping
TOOL_PERMISSIONS: Dict[str, Permission] = {
    # Memory tools
    "memory_recall": Permission.MEMORY_READ,
    "memory_store": Permission.MEMORY_WRITE,
    "memory_update": Permission.MEMORY_WRITE,
    "memory_history": Permission.MEMORY_READ,

    # Code tools
    "code_exec": Permission.CODE_EXEC,
    "deps_install": Permission.DEPS_INSTALL,
    "code_lint": Permission.CODE_LINT,
    "run_tests": Permission.TESTS_RUN,

    # Git tools
    "git_status": Permission.GIT_READ,
    "git_diff": Permission.GIT_READ,
    "git_commit": Permission.GIT_WRITE,
    "git_branch": Permission.GIT_BRANCH,

    # File tools
    "file_read": Permission.FILE_READ,
    "file_write": Permission.FILE_WRITE,

    # Mesh tools
    "llm_call": Permission.LLM_CALL,
    "llm_broadcast": Permission.LLM_BROADCAST,
    "llm_consensus": Permission.LLM_CONSENSUS,
    "llm_delegate": Permission.LLM_CALL,

    # System tools
    "web_search": Permission.HEALTH_CHECK,
    "audit_log": Permission.AUDIT_WRITE,
    "health_check": Permission.HEALTH_CHECK,

    # Workspace tools
    "triforce_read": Permission.FILE_READ,
    "triforce_write": Permission.FILE_WRITE,
    "triforce_init": Permission.HEALTH_CHECK,
    "tools_index": Permission.HEALTH_CHECK,
}


@dataclass
class RBACContext:
    """Context for RBAC checks"""
    llm_id: str
    role: Role
    permissions: Set[Permission]
    session_id: Optional[str] = None
    project_id: Optional[str] = None

    def has_permission(self, permission: Permission) -> bool:
        """Check if context has a specific permission"""
        if Permission.ADMIN_FULL in self.permissions:
            return True
        return permission in self.permissions

    def can_use_tool(self, tool_name: str) -> bool:
        """Check if context can use a specific tool"""
        required = TOOL_PERMISSIONS.get(tool_name)
        if not required:
            return False
        return self.has_permission(required)


class RBACService:
    """RBAC Service for permission checks"""

    def __init__(self):
        self._custom_roles: Dict[str, Role] = {}
        self._custom_permissions: Dict[str, Set[Permission]] = {}

    def get_llm_role(self, llm_id: str) -> Role:
        """Get the role for an LLM"""
        llm_key = llm_id.lower().split("/")[-1].split(":")[0]

        # Check custom roles first
        if llm_key in self._custom_roles:
            return self._custom_roles[llm_key]

        # Check default roles
        return LLM_ROLES.get(llm_key, Role.READER)

    def get_permissions(self, role: Role) -> Set[Permission]:
        """Get permissions for a role"""
        base_permissions = ROLE_PERMISSIONS.get(role, set())

        # Add custom permissions if any
        if role.value in self._custom_permissions:
            return base_permissions | self._custom_permissions[role.value]

        return base_permissions

    def can_use_tool(self, llm_id: str, tool_name: str) -> bool:
        """Check if an LLM can use a specific tool"""
        role = self.get_llm_role(llm_id)
        permissions = self.get_permissions(role)

        # Admin has full access
        if Permission.ADMIN_FULL in permissions:
            return True

        # Check tool permission
        required = TOOL_PERMISSIONS.get(tool_name)
        return required in permissions if required else False

    def can_call_llm(self, caller: str, target: str) -> bool:
        """Check if caller LLM can call target LLM"""
        permissions = self.get_permissions(self.get_llm_role(caller))
        return Permission.LLM_CALL in permissions or Permission.ADMIN_FULL in permissions

    def get_available_tools(self, llm_id: str) -> List[str]:
        """Get list of tools available to an LLM"""
        return [
            tool for tool in TOOL_PERMISSIONS
            if self.can_use_tool(llm_id, tool)
        ]

    def create_context(
        self,
        llm_id: str,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None
    ) -> RBACContext:
        """Create an RBAC context for an LLM"""
        role = self.get_llm_role(llm_id)
        permissions = self.get_permissions(role)

        return RBACContext(
            llm_id=llm_id,
            role=role,
            permissions=permissions,
            session_id=session_id,
            project_id=project_id
        )

    def set_llm_role(self, llm_id: str, role: Role):
        """Override the role for a specific LLM"""
        self._custom_roles[llm_id.lower()] = role

    def add_role_permission(self, role: Role, permission: Permission):
        """Add additional permission to a role"""
        if role.value not in self._custom_permissions:
            self._custom_permissions[role.value] = set()
        self._custom_permissions[role.value].add(permission)

    def get_role_summary(self) -> Dict[str, List[str]]:
        """Get summary of all roles and their permissions"""
        return {
            role.value: [p.value for p in self.get_permissions(role)]
            for role in Role
        }


# Singleton instance
rbac_service = RBACService()
