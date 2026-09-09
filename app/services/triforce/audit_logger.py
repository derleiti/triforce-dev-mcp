"""
Audit Logger v2.60 - With WebSocket Live Streaming

Provides comprehensive audit logging for all TriForce operations:
- Tool calls
- LLM-to-LLM calls
- Security events
- System events

Supports:
- File-based JSONL logging with daily rotation
- In-memory buffer for recent events
- WebSocket live streaming to clients
"""

import asyncio
import json
import uuid
from datetime import datetime
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Dict, Any, List, Set
from pathlib import Path

from app.paths import LOG_DIR
import logging

logger = logging.getLogger("ailinux.triforce.audit")


class AuditLevel(str, Enum):
    """Audit log levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SECURITY = "security"


@dataclass
class AuditEntry:
    """A single audit log entry"""
    timestamp: str
    trace_id: str
    session_id: str
    llm_id: str
    action: str
    level: AuditLevel

    # Optional fields
    tool_name: Optional[str] = None
    target_llm: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    result_status: Optional[str] = None
    execution_time_ms: Optional[float] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        data["level"] = self.level.value
        return data

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEntry":
        """Create from dictionary"""
        data = data.copy()
        if "level" in data:
            data["level"] = AuditLevel(data["level"])
        return cls(**data)


class AuditLogger:
    """Audit logging service with WebSocket streaming"""

    def __init__(
        self,
        log_dir: str | None = None,
        buffer_size: int = 1000,
        flush_threshold: int = 100
    ):
        self.log_dir = Path(log_dir) if log_dir is not None else LOG_DIR
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.buffer_size = buffer_size
        self.flush_threshold = flush_threshold

        self._buffer: List[AuditEntry] = []
        self._websockets: Set[Any] = set()
        self._lock = asyncio.Lock()

    async def log(
        self,
        llm_id: str,
        action: str,
        level: AuditLevel = AuditLevel.INFO,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs
    ) -> AuditEntry:
        """Log an audit entry"""
        entry = AuditEntry(
            timestamp=datetime.utcnow().isoformat() + "Z",
            trace_id=trace_id or str(uuid.uuid4()),
            session_id=session_id or "unknown",
            llm_id=llm_id,
            action=action,
            level=level,
            **kwargs
        )

        async with self._lock:
            self._buffer.append(entry)

            # Keep buffer size limited
            if len(self._buffer) > self.buffer_size:
                self._buffer = self._buffer[-self.buffer_size:]

            # Flush to disk if threshold reached
            if len(self._buffer) >= self.flush_threshold:
                await self._flush()

        # Broadcast to WebSocket clients
        await self._broadcast(entry)

        # Also log to Python logger
        log_func = getattr(logger, level.value if level.value != "security" else "warning")
        log_func(f"[{entry.trace_id[:8]}] {llm_id}: {action}")

        return entry

    async def log_tool_call(
        self,
        llm_id: str,
        tool_name: str,
        params: Dict[str, Any],
        result_status: str,
        execution_time_ms: float,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> AuditEntry:
        """Log a tool call"""
        level = AuditLevel.ERROR if error_message else AuditLevel.INFO

        # Sanitize params (remove sensitive data)
        safe_params = self._sanitize_params(params)

        return await self.log(
            llm_id=llm_id,
            action="tool_call",
            level=level,
            trace_id=trace_id,
            session_id=session_id,
            tool_name=tool_name,
            params=safe_params,
            result_status=result_status,
            execution_time_ms=execution_time_ms,
            error_message=error_message
        )

    async def log_llm_call(
        self,
        caller_llm: str,
        target_llm: str,
        prompt_preview: str,
        result_status: str,
        execution_time_ms: float,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        error_message: Optional[str] = None
    ) -> AuditEntry:
        """Log an LLM-to-LLM call"""
        level = AuditLevel.ERROR if error_message else AuditLevel.INFO

        return await self.log(
            llm_id=caller_llm,
            action="llm_call",
            level=level,
            trace_id=trace_id,
            session_id=session_id,
            target_llm=target_llm,
            params={"prompt_preview": prompt_preview[:200]},
            result_status=result_status,
            execution_time_ms=execution_time_ms,
            error_message=error_message
        )

    async def log_security_event(
        self,
        llm_id: str,
        event_type: str,
        details: Dict[str, Any],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AuditEntry:
        """Log a security event"""
        return await self.log(
            llm_id=llm_id,
            action=f"security:{event_type}",
            level=AuditLevel.SECURITY,
            trace_id=trace_id,
            session_id=session_id,
            metadata=details
        )

    async def log_rbac_denied(
        self,
        llm_id: str,
        tool_name: str,
        required_permission: str,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AuditEntry:
        """Log an RBAC denial"""
        return await self.log_security_event(
            llm_id=llm_id,
            event_type="rbac_denied",
            details={
                "tool_name": tool_name,
                "required_permission": required_permission,
            },
            trace_id=trace_id,
            session_id=session_id
        )

    async def log_cycle_detected(
        self,
        llm_id: str,
        call_chain: List[str],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AuditEntry:
        """Log a detected call cycle"""
        return await self.log_security_event(
            llm_id=llm_id,
            event_type="cycle_detected",
            details={"call_chain": call_chain},
            trace_id=trace_id,
            session_id=session_id
        )

    async def log_rate_limited(
        self,
        llm_id: str,
        current_rate: int,
        limit: int,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> AuditEntry:
        """Log a rate limit hit"""
        return await self.log(
            llm_id=llm_id,
            action="rate_limited",
            level=AuditLevel.WARNING,
            trace_id=trace_id,
            session_id=session_id,
            metadata={"current_rate": current_rate, "limit": limit}
        )

    def _sanitize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from params"""
        if not params:
            return {}

        sensitive_keys = {"password", "api_key", "secret", "token", "credential"}
        safe = {}

        for key, value in params.items():
            if any(s in key.lower() for s in sensitive_keys):
                safe[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 500:
                safe[key] = value[:500] + "...[truncated]"
            else:
                safe[key] = value

        return safe

    async def _flush(self):
        """Flush buffer to disk"""
        if not self._buffer:
            return

        today = datetime.utcnow().strftime("%Y-%m-%d")
        log_file = self.log_dir / f"audit_{today}.jsonl"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                for entry in self._buffer:
                    f.write(entry.to_json() + "\n")

            logger.debug(f"Flushed {len(self._buffer)} audit entries to {log_file}")
        except Exception as e:
            logger.error(f"Failed to flush audit log: {e}")

    async def force_flush(self):
        """Force flush all buffered entries"""
        async with self._lock:
            await self._flush()
            self._buffer.clear()

    # WebSocket management
    def register_websocket(self, ws):
        """Register a WebSocket for live streaming"""
        self._websockets.add(ws)
        logger.info(f"WebSocket registered, total: {len(self._websockets)}")

    def unregister_websocket(self, ws):
        """Unregister a WebSocket"""
        self._websockets.discard(ws)
        logger.info(f"WebSocket unregistered, total: {len(self._websockets)}")

    async def _broadcast(self, entry: AuditEntry):
        """Broadcast entry to all WebSocket clients"""
        if not self._websockets:
            return

        message = entry.to_json()
        dead_sockets = set()

        for ws in self._websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead_sockets.add(ws)

        # Remove dead sockets
        for ws in dead_sockets:
            self._websockets.discard(ws)

    # Query methods
    def get_recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent audit entries from buffer"""
        entries = self._buffer[-limit:]
        return [e.to_dict() for e in entries]

    def get_by_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all entries for a trace ID"""
        return [
            e.to_dict() for e in self._buffer
            if e.trace_id == trace_id
        ]

    def get_by_llm(self, llm_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get entries for a specific LLM"""
        entries = [e for e in self._buffer if e.llm_id == llm_id]
        return [e.to_dict() for e in entries[-limit:]]

    def get_security_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent security events"""
        entries = [e for e in self._buffer if e.level == AuditLevel.SECURITY]
        return [e.to_dict() for e in entries[-limit:]]

    def get_errors(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent errors"""
        entries = [
            e for e in self._buffer
            if e.level in (AuditLevel.ERROR, AuditLevel.CRITICAL)
        ]
        return [e.to_dict() for e in entries[-limit:]]

    async def read_log_file(self, date: str) -> List[Dict[str, Any]]:
        """Read entries from a specific date's log file"""
        log_file = self.log_dir / f"audit_{date}.jsonl"

        if not log_file.exists():
            return []

        entries = []
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
        except Exception as e:
            logger.error(f"Failed to read log file {log_file}: {e}")

        return entries


# Singleton instance
audit_logger = AuditLogger()
