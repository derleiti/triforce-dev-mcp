"""
TriStar Chain Meta Manager v2.80 - Chain State Persistence

Manages chain metadata and state:
- Chain configuration and state persistence
- Workspace management for chains and projects
- Chain history and logs
- PostgreSQL integration for production (optional)
- File-based storage for development
"""

import asyncio
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.paths import TRISTAR_DIR
from typing import Dict, List, Any, Optional
import logging
import aiofiles

logger = logging.getLogger("ailinux.tristar.chain_meta")


class ChainState(str, Enum):
    """Chain execution states"""
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ChainMeta:
    """Chain metadata"""
    chain_id: str
    project_id: str
    user_prompt: str
    state: ChainState

    # Timestamps
    created_at: str
    updated_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    # Configuration
    max_cycles: int = 10
    autoprompt_profile: Optional[str] = None
    autoprompt_override: Optional[str] = None
    aggressive: bool = False

    # Execution state
    current_cycle: int = 0
    total_cycles: int = 0

    # Results
    final_output: Optional[str] = None
    error: Optional[str] = None

    # Metrics
    total_tokens: int = 0
    total_execution_ms: float = 0.0

    # Workspace
    workspace_path: Optional[str] = None

    # Tags and metadata
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainMeta":
        data = data.copy()
        data["state"] = ChainState(data["state"])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ProjectMeta:
    """Project metadata"""
    project_id: str
    name: str
    description: str = ""

    # Timestamps
    created_at: str = ""
    updated_at: str = ""

    # Workspace
    workspace_path: Optional[str] = None

    # Chain stats
    total_chains: int = 0
    completed_chains: int = 0
    failed_chains: int = 0

    # Configuration
    default_autoprompt: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProjectMeta":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ChainMetaManager:
    """
    Manages chain and project metadata with file-based persistence.

    Directory structure:
    /var/tristar/
    ├── projects/
    │   └── {project_id}/
    │       ├── project.json           # Project metadata
    │       ├── chains/
    │       │   └── {timestamp}/
    │       │       ├── config.json    # Chain config
    │       │       ├── state.json     # Chain state
    │       │       ├── cycle_001.json
    │       │       ├── cycle_002.json
    │       │       └── result.json
    │       └── workspace/             # Project workspace
    ├── logs/                          # System logs
    └── reports/                       # Generated reports
    """

    def __init__(
        self,
        base_dir: str | Path = TRISTAR_DIR,
    ):
        self.base_dir = Path(base_dir)
        self.projects_dir = self.base_dir / "projects"
        self.logs_dir = self.base_dir / "logs"
        self.reports_dir = self.base_dir / "reports"

        # Create directories
        for d in [self.projects_dir, self.logs_dir, self.reports_dir]:
            d.mkdir(parents=True, exist_ok=True)

        # In-memory cache
        self._chains: Dict[str, ChainMeta] = {}
        self._projects: Dict[str, ProjectMeta] = {}
        self._lock = asyncio.Lock()

    # Project management

    async def create_project(
        self,
        project_id: str,
        name: str,
        description: str = "",
        default_autoprompt: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ProjectMeta:
        """Create a new project"""
        now = datetime.now(timezone.utc).isoformat()

        project_dir = self.projects_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "chains").mkdir(exist_ok=True)
        (project_dir / "workspace").mkdir(exist_ok=True)

        project = ProjectMeta(
            project_id=project_id,
            name=name,
            description=description,
            created_at=now,
            updated_at=now,
            workspace_path=str(project_dir / "workspace"),
            default_autoprompt=default_autoprompt,
            tags=tags or [],
        )

        await self._save_project(project)
        self._projects[project_id] = project

        logger.info(f"Created project {project_id}")
        return project

    async def get_project(self, project_id: str) -> Optional[ProjectMeta]:
        """Get project metadata"""
        if project_id in self._projects:
            return self._projects[project_id]

        project = await self._load_project(project_id)
        if project:
            self._projects[project_id] = project
        return project

    async def list_projects(self) -> List[ProjectMeta]:
        """List all projects"""
        projects = []
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                project = await self.get_project(project_dir.name)
                if project:
                    projects.append(project)
        return projects

    async def delete_project(self, project_id: str) -> bool:
        """Delete a project and all its chains"""
        project_dir = self.projects_dir / project_id
        if not project_dir.exists():
            return False

        import shutil
        shutil.rmtree(project_dir)

        if project_id in self._projects:
            del self._projects[project_id]

        # Remove chains from cache
        chains_to_remove = [
            cid for cid, chain in self._chains.items()
            if chain.project_id == project_id
        ]
        for cid in chains_to_remove:
            del self._chains[cid]

        logger.info(f"Deleted project {project_id}")
        return True

    # Chain management

    async def create_chain(
        self,
        chain_id: str,
        project_id: str,
        user_prompt: str,
        max_cycles: int = 10,
        autoprompt_profile: Optional[str] = None,
        autoprompt_override: Optional[str] = None,
        aggressive: bool = False,
    ) -> ChainMeta:
        """Create a new chain"""
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")

        # Ensure project exists
        project = await self.get_project(project_id)
        if not project:
            project = await self.create_project(project_id, f"Project {project_id}")

        # Create chain directory
        chain_dir = self.projects_dir / project_id / "chains" / timestamp
        chain_dir.mkdir(parents=True, exist_ok=True)

        chain = ChainMeta(
            chain_id=chain_id,
            project_id=project_id,
            user_prompt=user_prompt,
            state=ChainState.CREATED,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            max_cycles=max_cycles,
            autoprompt_profile=autoprompt_profile,
            autoprompt_override=autoprompt_override,
            aggressive=aggressive,
            workspace_path=str(chain_dir),
        )

        await self._save_chain(chain)
        self._chains[chain_id] = chain

        # Update project stats
        project.total_chains += 1
        project.updated_at = now.isoformat()
        await self._save_project(project)

        logger.info(f"Created chain {chain_id} in project {project_id}")
        return chain

    async def get_chain(self, chain_id: str) -> Optional[ChainMeta]:
        """Get chain metadata"""
        return self._chains.get(chain_id)

    async def update_chain_state(
        self,
        chain_id: str,
        state: ChainState,
        current_cycle: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Optional[ChainMeta]:
        """Update chain state"""
        chain = self._chains.get(chain_id)
        if not chain:
            return None

        chain.state = state
        chain.updated_at = datetime.now(timezone.utc).isoformat()

        if current_cycle is not None:
            chain.current_cycle = current_cycle

        if state == ChainState.RUNNING and not chain.started_at:
            chain.started_at = chain.updated_at

        if state in (ChainState.COMPLETED, ChainState.FAILED, ChainState.CANCELLED):
            chain.completed_at = chain.updated_at

        if error:
            chain.error = error

        await self._save_chain(chain)

        # Update project stats if completed/failed
        if state in (ChainState.COMPLETED, ChainState.FAILED):
            project = await self.get_project(chain.project_id)
            if project:
                if state == ChainState.COMPLETED:
                    project.completed_chains += 1
                else:
                    project.failed_chains += 1
                project.updated_at = chain.updated_at
                await self._save_project(project)

        return chain

    async def list_chains(
        self,
        project_id: Optional[str] = None,
        state: Optional[ChainState] = None,
        limit: int = 100,
    ) -> List[ChainMeta]:
        """List chains with optional filters"""
        chains = list(self._chains.values())

        if project_id:
            chains = [c for c in chains if c.project_id == project_id]

        if state:
            chains = [c for c in chains if c.state == state]

        # Sort by created_at descending
        chains.sort(key=lambda c: c.created_at, reverse=True)

        return chains[:limit]

    # Workspace management

    async def get_workspace_path(self, project_id: str) -> Path:
        """Get workspace path for a project"""
        return self.projects_dir / project_id / "workspace"

    async def get_chain_workspace(self, chain_id: str) -> Optional[Path]:
        """Get workspace path for a chain"""
        chain = self._chains.get(chain_id)
        if chain and chain.workspace_path:
            return Path(chain.workspace_path)
        return None

    async def write_to_workspace(
        self,
        project_id: str,
        filename: str,
        content: str,
    ) -> Path:
        """Write file to project workspace"""
        workspace = await self.get_workspace_path(project_id)
        workspace.mkdir(parents=True, exist_ok=True)

        filepath = workspace / filename
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(content)

        return filepath

    async def read_from_workspace(
        self,
        project_id: str,
        filename: str,
    ) -> Optional[str]:
        """Read file from project workspace"""
        workspace = await self.get_workspace_path(project_id)
        filepath = workspace / filename

        if not filepath.exists():
            return None

        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            return await f.read()

    async def list_workspace_files(self, project_id: str) -> List[str]:
        """List files in project workspace"""
        workspace = await self.get_workspace_path(project_id)
        if not workspace.exists():
            return []

        return [f.name for f in workspace.iterdir() if f.is_file()]

    # Persistence helpers

    async def _save_project(self, project: ProjectMeta):
        """Save project metadata to disk"""
        project_dir = self.projects_dir / project.project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        filepath = project_dir / "project.json"
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(json.dumps(project.to_dict(), indent=2, ensure_ascii=False))

    async def _load_project(self, project_id: str) -> Optional[ProjectMeta]:
        """Load project metadata from disk"""
        filepath = self.projects_dir / project_id / "project.json"
        if not filepath.exists():
            return None

        try:
            async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
                return ProjectMeta.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load project {project_id}: {e}")
            return None

    async def _save_chain(self, chain: ChainMeta):
        """Save chain state to disk"""
        if not chain.workspace_path:
            return

        filepath = Path(chain.workspace_path) / "state.json"
        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(json.dumps(chain.to_dict(), indent=2, ensure_ascii=False))

    async def _load_chain(self, workspace_path: str) -> Optional[ChainMeta]:
        """Load chain state from disk"""
        filepath = Path(workspace_path) / "state.json"
        if not filepath.exists():
            return None

        try:
            async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                data = json.loads(await f.read())
                return ChainMeta.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load chain from {workspace_path}: {e}")
            return None

    # Reports

    async def generate_chain_report(self, chain_id: str) -> Optional[Dict[str, Any]]:
        """Generate a report for a chain"""
        chain = self._chains.get(chain_id)
        if not chain:
            return None

        workspace = Path(chain.workspace_path) if chain.workspace_path else None
        cycles = []

        if workspace:
            for cycle_file in sorted(workspace.glob("cycle_*.json")):
                try:
                    async with aiofiles.open(cycle_file, "r") as f:
                        cycles.append(json.loads(await f.read()))
                except Exception:
                    pass

        report = {
            "chain_id": chain.chain_id,
            "project_id": chain.project_id,
            "user_prompt": chain.user_prompt,
            "state": chain.state.value,
            "created_at": chain.created_at,
            "completed_at": chain.completed_at,
            "total_cycles": len(cycles),
            "max_cycles": chain.max_cycles,
            "cycles": cycles,
            "final_output": chain.final_output,
            "error": chain.error,
            "metrics": {
                "total_tokens": chain.total_tokens,
                "total_execution_ms": chain.total_execution_ms,
            },
        }

        # Save report
        report_file = self.reports_dir / f"report_{chain_id}.json"
        async with aiofiles.open(report_file, "w", encoding="utf-8") as f:
            await f.write(json.dumps(report, indent=2, ensure_ascii=False))

        return report


# Singleton instance
chain_meta_manager = ChainMetaManager()
