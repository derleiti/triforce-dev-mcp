"""
TriStar Chain Logger v2.80 - Chain Execution Logging and Reporting

Provides comprehensive logging for chain executions:
- Per-chain JSONL logs
- Real-time WebSocket streaming
- Report generation
- Metrics collection
"""

import asyncio
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from app.paths import TRISTAR_DIR
from typing import Dict, List, Any, Optional, Set
from enum import Enum
import logging

logger = logging.getLogger("ailinux.tristar.chain_logger")


class ChainLogLevel(str, Enum):
    """Log levels for chain events"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CYCLE = "cycle"
    AGENT = "agent"
    RESULT = "result"


@dataclass
class ChainLogEntry:
    """A single chain log entry"""
    timestamp: str
    chain_id: str
    level: str
    event: str
    message: str
    cycle: Optional[int] = None
    agent: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class ChainLogger:
    """
    Logger for chain executions.

    Features:
    - Per-chain log files
    - In-memory buffer for recent entries
    - WebSocket streaming support
    - Report generation
    """

    def __init__(
        self,
        log_dir: str | Path = TRISTAR_DIR / "logs",
        buffer_size: int = 1000,
    ):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.buffer_size = buffer_size
        self._buffers: Dict[str, List[ChainLogEntry]] = {}
        self._websockets: Set[Any] = set()
        self._lock = asyncio.Lock()

    async def log(
        self,
        chain_id: str,
        event: str,
        message: str,
        level: ChainLogLevel = ChainLogLevel.INFO,
        cycle: Optional[int] = None,
        agent: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[float] = None,
    ) -> ChainLogEntry:
        """Log a chain event"""
        entry = ChainLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            chain_id=chain_id,
            level=level.value,
            event=event,
            message=message,
            cycle=cycle,
            agent=agent,
            data=data,
            duration_ms=duration_ms,
        )

        async with self._lock:
            # Add to buffer
            if chain_id not in self._buffers:
                self._buffers[chain_id] = []
            self._buffers[chain_id].append(entry)

            # Trim buffer
            if len(self._buffers[chain_id]) > self.buffer_size:
                self._buffers[chain_id] = self._buffers[chain_id][-self.buffer_size:]

            # Write to file
            await self._write_to_file(chain_id, entry)

        # Broadcast to WebSockets
        await self._broadcast(entry)

        # Python logging
        log_func = getattr(logger, level.value if level.value in ("debug", "info", "warning", "error") else "info")
        log_func(f"[{chain_id}] {event}: {message}")

        return entry

    async def log_cycle_start(
        self,
        chain_id: str,
        cycle: int,
    ):
        """Log cycle start"""
        await self.log(
            chain_id=chain_id,
            event="cycle_start",
            message=f"Starting cycle {cycle}",
            level=ChainLogLevel.CYCLE,
            cycle=cycle,
        )

    async def log_cycle_end(
        self,
        chain_id: str,
        cycle: int,
        status: str,
        duration_ms: float,
    ):
        """Log cycle end"""
        await self.log(
            chain_id=chain_id,
            event="cycle_end",
            message=f"Cycle {cycle} {status}",
            level=ChainLogLevel.CYCLE,
            cycle=cycle,
            data={"status": status},
            duration_ms=duration_ms,
        )

    async def log_agent_call(
        self,
        chain_id: str,
        cycle: int,
        agent: str,
        task_id: str,
        prompt_preview: str,
    ):
        """Log agent call"""
        await self.log(
            chain_id=chain_id,
            event="agent_call",
            message=f"Calling {agent} for {task_id}",
            level=ChainLogLevel.AGENT,
            cycle=cycle,
            agent=agent,
            data={"task_id": task_id, "prompt_preview": prompt_preview[:200]},
        )

    async def log_agent_result(
        self,
        chain_id: str,
        cycle: int,
        agent: str,
        task_id: str,
        success: bool,
        duration_ms: float,
    ):
        """Log agent result"""
        await self.log(
            chain_id=chain_id,
            event="agent_result",
            message=f"{agent} {task_id}: {'success' if success else 'failed'}",
            level=ChainLogLevel.AGENT,
            cycle=cycle,
            agent=agent,
            data={"task_id": task_id, "success": success},
            duration_ms=duration_ms,
        )

    async def log_chain_complete(
        self,
        chain_id: str,
        status: str,
        total_cycles: int,
        total_duration_ms: float,
    ):
        """Log chain completion"""
        await self.log(
            chain_id=chain_id,
            event="chain_complete",
            message=f"Chain {status} after {total_cycles} cycles",
            level=ChainLogLevel.RESULT,
            data={
                "status": status,
                "total_cycles": total_cycles,
            },
            duration_ms=total_duration_ms,
        )

    async def log_error(
        self,
        chain_id: str,
        error: str,
        cycle: Optional[int] = None,
        agent: Optional[str] = None,
    ):
        """Log an error"""
        await self.log(
            chain_id=chain_id,
            event="error",
            message=error,
            level=ChainLogLevel.ERROR,
            cycle=cycle,
            agent=agent,
        )

    async def _write_to_file(self, chain_id: str, entry: ChainLogEntry):
        """Write entry to chain log file"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self.log_dir / f"chain_{chain_id}_{today}.jsonl"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
        except Exception as e:
            logger.error(f"Failed to write log: {e}")

    async def _broadcast(self, entry: ChainLogEntry):
        """Broadcast entry to WebSocket clients"""
        if not self._websockets:
            return

        message = entry.to_json()
        dead = set()

        for ws in self._websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead.add(ws)

        for ws in dead:
            self._websockets.discard(ws)

    def register_websocket(self, ws):
        """Register WebSocket for streaming"""
        self._websockets.add(ws)

    def unregister_websocket(self, ws):
        """Unregister WebSocket"""
        self._websockets.discard(ws)

    def get_chain_logs(
        self,
        chain_id: str,
        limit: int = 100,
        level: Optional[ChainLogLevel] = None,
    ) -> List[Dict[str, Any]]:
        """Get logs for a chain"""
        entries = self._buffers.get(chain_id, [])

        if level:
            entries = [e for e in entries if e.level == level.value]

        return [e.to_dict() for e in entries[-limit:]]

    async def generate_report(self, chain_id: str) -> Dict[str, Any]:
        """Generate a report for a chain"""
        entries = self._buffers.get(chain_id, [])

        if not entries:
            return {"chain_id": chain_id, "error": "No logs found"}

        # Analyze logs
        cycles = []
        agents_used = set()
        errors = []
        total_duration = 0.0

        current_cycle = None
        cycle_start_time = None

        for entry in entries:
            if entry.event == "cycle_start":
                current_cycle = entry.cycle
                cycle_start_time = entry.timestamp

            elif entry.event == "cycle_end":
                if entry.duration_ms:
                    cycles.append({
                        "cycle": entry.cycle,
                        "status": entry.data.get("status") if entry.data else None,
                        "duration_ms": entry.duration_ms,
                    })

            elif entry.event == "agent_call":
                if entry.agent:
                    agents_used.add(entry.agent)

            elif entry.event == "error":
                errors.append({
                    "message": entry.message,
                    "cycle": entry.cycle,
                    "agent": entry.agent,
                })

            elif entry.event == "chain_complete":
                if entry.duration_ms:
                    total_duration = entry.duration_ms

        # Build report
        report = {
            "chain_id": chain_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {
                "total_cycles": len(cycles),
                "agents_used": list(agents_used),
                "total_errors": len(errors),
                "total_duration_ms": total_duration,
            },
            "cycles": cycles,
            "errors": errors,
            "timeline": [e.to_dict() for e in entries[:50]],  # First 50 events
        }

        # Save report
        report_file = self.log_dir.parent / "reports" / f"report_{chain_id}.json"
        report_file.parent.mkdir(exist_ok=True)

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        return report


# Singleton instance
chain_logger = ChainLogger()
