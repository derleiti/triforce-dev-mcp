"""
TriStar Chain Engine v2.80 - Multi-Cycle LLM Chain Orchestration

Implements the core chain workflow:
1. User → Kernel (receives user prompt)
2. Kernel → Lead (Gemini) analyzes, researches, builds agent plan
3. Kernel → Mesh Agents (Claude, Codex, DeepSeek, Qwen, etc.)
4. Lead (Gemini) consolidates results
5. Next Cycle → until max_cycles or [CHAIN_DONE]

All chain data persisted to /var/tristar/projects/{project_id}/chains/{timestamp}/
"""

import asyncio
import uuid
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
from enum import Enum
from pathlib import Path

from app.paths import TRISTAR_DIR
import json
import logging

logger = logging.getLogger("ailinux.tristar.chain_engine")


class ChainStatus(str, Enum):
    """Chain execution status"""
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ChainCycle:
    """A single cycle in the chain"""
    cycle_number: int
    started_at: str
    completed_at: Optional[str] = None
    status: str = "running"

    # Lead analysis
    lead_analysis: Optional[str] = None
    lead_plan: Optional[Dict[str, Any]] = None

    # Agent delegations
    agent_tasks: List[Dict[str, Any]] = field(default_factory=list)
    agent_results: Dict[str, Any] = field(default_factory=dict)

    # Consolidation
    consolidation: Optional[str] = None
    next_action: Optional[str] = None  # "continue" | "done" | "error"

    # Metrics
    execution_time_ms: float = 0.0
    tokens_used: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainCycle":
        return cls(**data)


@dataclass
class ChainResult:
    """Final result of a chain execution"""
    chain_id: str
    project_id: str
    user_prompt: str
    status: ChainStatus

    # Execution details
    started_at: str
    completed_at: Optional[str] = None
    total_cycles: int = 0
    max_cycles: int = 10

    # Cycles
    cycles: List[ChainCycle] = field(default_factory=list)

    # Final output
    final_output: Optional[str] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    # Metrics
    total_execution_time_ms: float = 0.0
    total_tokens_used: int = 0

    # Errors
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["cycles"] = [c.to_dict() if isinstance(c, ChainCycle) else c for c in self.cycles]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChainResult":
        data = data.copy()
        data["status"] = ChainStatus(data["status"])
        data["cycles"] = [ChainCycle.from_dict(c) if isinstance(c, dict) else c for c in data.get("cycles", [])]
        return cls(**data)


class ChainEngine:
    """
    Core chain execution engine.

    Orchestrates the chain workflow:
    1. User prompt → Lead LLM (Gemini) for analysis
    2. Lead creates agent plan
    3. Mesh agents execute tasks in parallel
    4. Lead consolidates results
    5. Repeat until done or max_cycles
    """

    def __init__(
        self,
        workspace_base: str | Path = TRISTAR_DIR / "projects",
        default_lead: str = "gemini",
        default_max_cycles: int = 10,
    ):
        self.workspace_base = Path(workspace_base)
        self.workspace_base.mkdir(parents=True, exist_ok=True)

        self.default_lead = default_lead
        self.default_max_cycles = default_max_cycles

        # Active chains
        self._active_chains: Dict[str, ChainResult] = {}
        self._lock = asyncio.Lock()

    async def start_chain(
        self,
        user_prompt: str,
        project_id: Optional[str] = None,
        max_cycles: Optional[int] = None,
        autoprompt_profile: Optional[str] = None,
        autoprompt_override: Optional[str] = None,
        aggressive: bool = False,
        trace_id: Optional[str] = None,
    ) -> ChainResult:
        """
        Start a new chain execution.

        Args:
            user_prompt: The user's prompt/task
            project_id: Project ID (auto-generated if not provided)
            max_cycles: Maximum cycles (default: 10)
            autoprompt_profile: AutoPrompt profile to use
            autoprompt_override: Ad-hoc autoprompt override
            aggressive: Enable aggressive mode (more parallel tasks)
            trace_id: Trace ID for audit logging

        Returns:
            ChainResult with initial state
        """
        chain_id = f"chain_{uuid.uuid4().hex[:12]}"
        project_id = project_id or f"proj_{uuid.uuid4().hex[:8]}"
        max_cycles = max_cycles or self.default_max_cycles
        trace_id = trace_id or str(uuid.uuid4())

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%d_%H%M%S")

        # Create workspace
        chain_dir = self.workspace_base / project_id / "chains" / timestamp
        chain_dir.mkdir(parents=True, exist_ok=True)

        # Initialize chain result
        result = ChainResult(
            chain_id=chain_id,
            project_id=project_id,
            user_prompt=user_prompt,
            status=ChainStatus.RUNNING,
            started_at=now.isoformat(),
            max_cycles=max_cycles,
        )

        # Store chain config
        config = {
            "chain_id": chain_id,
            "project_id": project_id,
            "user_prompt": user_prompt,
            "max_cycles": max_cycles,
            "autoprompt_profile": autoprompt_profile,
            "autoprompt_override": autoprompt_override,
            "aggressive": aggressive,
            "trace_id": trace_id,
            "started_at": now.isoformat(),
            "workspace": str(chain_dir),
        }

        with open(chain_dir / "config.json", "w") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)

        # Register active chain
        async with self._lock:
            self._active_chains[chain_id] = result

        logger.info(f"Started chain {chain_id} for project {project_id}")

        # Start chain execution in background
        asyncio.create_task(self._execute_chain(
            result=result,
            chain_dir=chain_dir,
            autoprompt_profile=autoprompt_profile,
            autoprompt_override=autoprompt_override,
            aggressive=aggressive,
            trace_id=trace_id,
        ))

        return result

    async def _execute_chain(
        self,
        result: ChainResult,
        chain_dir: Path,
        autoprompt_profile: Optional[str],
        autoprompt_override: Optional[str],
        aggressive: bool,
        trace_id: str,
    ):
        """Execute the chain cycles"""
        from .cycle_engine import cycle_engine
        from .autoprompt import autoprompt_manager

        try:
            start_time = time.time()

            # Get autoprompt
            autoprompt = await autoprompt_manager.get_merged_prompt(
                project_id=result.project_id,
                profile=autoprompt_profile,
                override=autoprompt_override,
            )

            # Execute cycles
            current_context = result.user_prompt

            for cycle_num in range(1, result.max_cycles + 1):
                cycle_start = time.time()

                cycle = ChainCycle(
                    cycle_number=cycle_num,
                    started_at=datetime.now(timezone.utc).isoformat(),
                )

                try:
                    # Execute cycle
                    cycle_result = await cycle_engine.execute_cycle(
                        prompt=current_context,
                        autoprompt=autoprompt,
                        project_id=result.project_id,
                        cycle_number=cycle_num,
                        aggressive=aggressive,
                        trace_id=trace_id,
                    )

                    # Update cycle
                    cycle.lead_analysis = cycle_result.lead_analysis
                    cycle.lead_plan = cycle_result.agent_plan
                    cycle.agent_tasks = cycle_result.agent_tasks
                    cycle.agent_results = cycle_result.agent_results
                    cycle.consolidation = cycle_result.consolidation
                    cycle.next_action = cycle_result.next_action
                    cycle.tokens_used = cycle_result.tokens_used
                    cycle.status = "completed"
                    cycle.completed_at = datetime.now(timezone.utc).isoformat()
                    cycle.execution_time_ms = (time.time() - cycle_start) * 1000

                    # Add cycle to result
                    result.cycles.append(cycle)
                    result.total_cycles = cycle_num
                    result.total_tokens_used += cycle.tokens_used

                    # Save cycle
                    cycle_file = chain_dir / f"cycle_{cycle_num:03d}.json"
                    with open(cycle_file, "w") as f:
                        json.dump(cycle.to_dict(), f, indent=2, ensure_ascii=False)

                    # Check if done
                    if cycle_result.next_action == "done" or "[CHAIN_DONE]" in (cycle_result.consolidation or ""):
                        result.final_output = cycle_result.consolidation
                        result.status = ChainStatus.COMPLETED
                        break

                    # Update context for next cycle
                    current_context = cycle_result.consolidation or current_context

                except Exception as e:
                    cycle.status = "failed"
                    cycle.completed_at = datetime.now(timezone.utc).isoformat()
                    cycle.execution_time_ms = (time.time() - cycle_start) * 1000
                    result.cycles.append(cycle)
                    result.error = str(e)
                    result.status = ChainStatus.FAILED
                    logger.error(f"Chain {result.chain_id} cycle {cycle_num} failed: {e}")
                    break

            # Finalize
            result.completed_at = datetime.now(timezone.utc).isoformat()
            result.total_execution_time_ms = (time.time() - start_time) * 1000

            if result.status == ChainStatus.RUNNING:
                # Max cycles reached
                result.status = ChainStatus.COMPLETED
                if result.cycles:
                    result.final_output = result.cycles[-1].consolidation

            # Save final result
            with open(chain_dir / "result.json", "w") as f:
                json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)

            logger.info(f"Chain {result.chain_id} completed with status {result.status.value}")

        except Exception as e:
            result.status = ChainStatus.FAILED
            result.error = str(e)
            result.completed_at = datetime.now(timezone.utc).isoformat()
            logger.error(f"Chain {result.chain_id} failed: {e}")

        finally:
            async with self._lock:
                self._active_chains[result.chain_id] = result

    async def get_chain_status(self, chain_id: str) -> Optional[ChainResult]:
        """Get current status of a chain"""
        async with self._lock:
            return self._active_chains.get(chain_id)

    async def cancel_chain(self, chain_id: str) -> bool:
        """Cancel a running chain"""
        async with self._lock:
            if chain_id in self._active_chains:
                self._active_chains[chain_id].status = ChainStatus.CANCELLED
                self._active_chains[chain_id].completed_at = datetime.now(timezone.utc).isoformat()
                return True
        return False

    async def pause_chain(self, chain_id: str) -> bool:
        """Pause a running chain"""
        async with self._lock:
            if chain_id in self._active_chains:
                if self._active_chains[chain_id].status == ChainStatus.RUNNING:
                    self._active_chains[chain_id].status = ChainStatus.PAUSED
                    return True
        return False

    async def resume_chain(self, chain_id: str) -> bool:
        """Resume a paused chain"""
        async with self._lock:
            if chain_id in self._active_chains:
                if self._active_chains[chain_id].status == ChainStatus.PAUSED:
                    self._active_chains[chain_id].status = ChainStatus.RUNNING
                    return True
        return False

    async def list_chains(
        self,
        project_id: Optional[str] = None,
        status: Optional[ChainStatus] = None,
    ) -> List[Dict[str, Any]]:
        """List all chains, optionally filtered"""
        async with self._lock:
            chains = list(self._active_chains.values())

        if project_id:
            chains = [c for c in chains if c.project_id == project_id]

        if status:
            chains = [c for c in chains if c.status == status]

        return [
            {
                "chain_id": c.chain_id,
                "project_id": c.project_id,
                "status": c.status.value,
                "total_cycles": c.total_cycles,
                "max_cycles": c.max_cycles,
                "started_at": c.started_at,
                "completed_at": c.completed_at,
            }
            for c in chains
        ]

    async def get_chain_logs(
        self,
        chain_id: str,
        cycle_number: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Get logs for a chain or specific cycle"""
        result = await self.get_chain_status(chain_id)
        if not result:
            return []

        if cycle_number:
            cycles = [c for c in result.cycles if c.cycle_number == cycle_number]
        else:
            cycles = result.cycles

        return [c.to_dict() if isinstance(c, ChainCycle) else c for c in cycles]


# Singleton instance
chain_engine = ChainEngine()
