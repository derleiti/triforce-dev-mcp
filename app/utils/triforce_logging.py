"""
TriForce Centralized Logging v2.81

Collects ALL logs from the AILinux backend and posts them to TriForce:
- API Traffic (requests/responses)
- Python logging messages
- LLM calls
- Tool executions
- Errors and exceptions
- Security events

This enables TriStar to access all system logs for analysis and debugging.
"""

import asyncio
import os
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.paths import LOG_DIR
from typing import Any, Dict, List, Optional, Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("ailinux.triforce.central")


class LogCategory(str, Enum):
    """Log categories for filtering"""
    API_REQUEST = "api_request"
    API_RESPONSE = "api_response"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    MCP_CALL = "mcp_call"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"
    SECURITY = "security"
    SYSTEM = "system"
    AGENT = "agent"
    MEMORY = "memory"
    CHAIN = "chain"


class LogLevel(str, Enum):
    """Log severity levels"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class TriForceLogEntry:
    """A unified log entry for TriForce"""
    timestamp: str
    trace_id: str
    category: LogCategory
    level: LogLevel
    source: str  # Which component generated this log
    message: str

    # Optional fields
    session_id: Optional[str] = None
    agent_id: Optional[str] = None
    request_id: Optional[str] = None

    # API Traffic fields
    method: Optional[str] = None
    path: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    client_ip: Optional[str] = None
    user_agent: Optional[str] = None
    request_size: Optional[int] = None
    response_size: Optional[int] = None

    # LLM fields
    model: Optional[str] = None
    provider: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None

    # Tool/MCP fields
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    tool_result: Optional[str] = None

    # Error fields
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None

    # Extra metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values"""
        data = asdict(self)
        # Handle both Enum and string values for category/level
        data["category"] = self.category.value if hasattr(self.category, 'value') else str(self.category)
        data["level"] = self.level.value if hasattr(self.level, 'value') else str(self.level)
        # Remove None values for cleaner output
        return {k: v for k, v in data.items() if v is not None}

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict(), ensure_ascii=False, default=str)


class TriForceLogHandler(logging.Handler):
    """
    Python logging handler that forwards all logs to TriForce.
    Attach this to the root logger to capture all Python logs.
    
    v2.82: Added noise filtering to reduce log volume by ~80%
    """

    # Patterns to filter out (high-frequency, low-value logs)
    NOISE_PATTERNS = {
        # Health checks and heartbeats
        "GET /health",
        "GET /healthz", 
        "GET /ready",
        "health check",
        "health probe",  # v2.82.1: Added
        "heartbeat",
        # High-frequency internal calls
        "tools/list",  # Called after every tool call by Anthropic
        "prompts/list",
        "sse_disconnect",  # v2.82.1: Session disconnects are noise
        # Uvicorn access logs for filtered paths
        "/health HTTP",
        "/metrics HTTP",
        "/favicon.ico",
    }
    
    # Logger names to completely skip (too noisy)
    SKIP_LOGGERS = {
        "uvicorn.access",  # We log API requests separately via middleware
        "httpcore",
        "httpx",
        "asyncio",
    }

    def __init__(self, central_logger: "TriForceCentralLogger"):
        super().__init__()
        self.central_logger = central_logger
        self._filtered_count = 0  # Track how many we filter

    def _should_filter(self, record: logging.LogRecord) -> bool:
        """Check if this log record should be filtered out"""
        # Skip certain loggers entirely
        if any(record.name.startswith(skip) for skip in self.SKIP_LOGGERS):
            return True
        
        # Skip DEBUG level unless it's an error/security context
        if record.levelno <= logging.DEBUG:
            # Allow debug logs for errors, security, agents
            important_contexts = {"error", "security", "agent", "chain", "memory"}
            if not any(ctx in record.name.lower() for ctx in important_contexts):
                return True
        
        # Check message against noise patterns
        msg = record.getMessage().lower()
        for pattern in self.NOISE_PATTERNS:
            if pattern.lower() in msg:
                self._filtered_count += 1
                return True
        
        return False

    def emit(self, record: logging.LogRecord):
        """Handle a log record"""
        try:
            # v2.82: Filter noise before processing
            if self._should_filter(record):
                return
            
            # Map Python log levels to our levels
            level_map = {
                logging.DEBUG: LogLevel.DEBUG,
                logging.INFO: LogLevel.INFO,
                logging.WARNING: LogLevel.WARNING,
                logging.ERROR: LogLevel.ERROR,
                logging.CRITICAL: LogLevel.CRITICAL,
            }
            level = level_map.get(record.levelno, LogLevel.INFO)

            # Determine category based on logger name
            category = LogCategory.INFO
            if "error" in record.name.lower() or record.levelno >= logging.ERROR:
                category = LogCategory.ERROR
            elif "security" in record.name.lower():
                category = LogCategory.SECURITY
            elif "agent" in record.name.lower():
                category = LogCategory.AGENT
            elif "memory" in record.name.lower():
                category = LogCategory.MEMORY
            elif "chain" in record.name.lower():
                category = LogCategory.CHAIN
            elif "mcp" in record.name.lower():
                category = LogCategory.MCP_CALL
            elif record.levelno == logging.WARNING:
                category = LogCategory.WARNING
            elif record.levelno == logging.DEBUG:
                category = LogCategory.DEBUG

            # Create log entry
            entry = TriForceLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                trace_id=getattr(record, 'trace_id', str(uuid.uuid4())[:12]),
                category=category,
                level=level,
                source=record.name,
                message=record.getMessage(),
                metadata={
                    "filename": record.filename,
                    "lineno": record.lineno,
                    "funcName": record.funcName,
                }
            )

            # Add exception info if present
            if record.exc_info:
                import traceback
                entry.error_type = record.exc_info[0].__name__ if record.exc_info[0] else None
                entry.error_message = str(record.exc_info[1]) if record.exc_info[1] else None
                entry.stack_trace = ''.join(traceback.format_exception(*record.exc_info))

            # Queue for async processing
            self.central_logger.queue_log(entry)

        except Exception:
            # Don't let logging errors break the application
            pass


class TriForceCentralLogger:
    """
    Central logging service that collects all logs and posts to TriForce.
    Supports:
    - In-memory buffer with configurable size
    - File-based JSONL logging with daily rotation
    - WebSocket live streaming
    - Async batch posting to TriForce API
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        buffer_size: int = 10000,
        flush_interval: float = 5.0,  # seconds
        flush_threshold: int = 100,   # entries
    ):
        # Default: ./logs/central
        if log_dir is None:
            self.log_dir = LOG_DIR / "central"
        else:
            self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        self.buffer_size = buffer_size
        self.flush_interval = flush_interval
        self.flush_threshold = flush_threshold

        self._buffer: deque = deque(maxlen=buffer_size)
        self._pending: List[TriForceLogEntry] = []
        self._websockets: Set[Any] = set()
        self._lock = asyncio.Lock()
        self._flush_task: Optional[asyncio.Task] = None
        self._running = False

        # Statistics
        self._stats = {
            "total_logged": 0,
            "total_flushed": 0,
            "errors": 0,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }

    async def start(self):
        """Start the background flush task"""
        if self._running:
            return
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        logger.info("TriForce Central Logger started")

    async def stop(self):
        """Stop the logger and flush remaining entries"""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.force_flush()
        logger.info("TriForce Central Logger stopped")

    def queue_log(self, entry: TriForceLogEntry):
        """Queue a log entry (thread-safe, sync)"""
        self._buffer.append(entry)
        self._pending.append(entry)
        self._stats["total_logged"] += 1

    async def log(
        self,
        category: str | LogCategory,
        message: str,
        level: str | LogLevel = "info",
        source: str = "unknown",
        trace_id: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log an entry asynchronously.

        Args:
            category: Log category (string or LogCategory enum)
            message: Log message
            level: Log level (string or LogLevel enum)
            source: Source component identifier
            trace_id: Optional trace ID for correlation
            **kwargs: Additional fields (metadata, agent_id, etc.)
        """
        # Convert string to enum if needed (or keep as string for flexibility)
        if isinstance(category, str):
            try:
                category = LogCategory(category)
            except ValueError:
                pass  # Keep as string if not a valid enum value

        if isinstance(level, str):
            try:
                level = LogLevel(level)
            except ValueError:
                pass  # Keep as string if not a valid enum value

        entry = TriForceLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            trace_id=trace_id or str(uuid.uuid4())[:12],
            category=category,
            level=level,
            source=source,
            message=message,
            **kwargs
        )

        async with self._lock:
            self._buffer.append(entry)
            self._pending.append(entry)
            self._stats["total_logged"] += 1

            # Flush if threshold reached
            if len(self._pending) >= self.flush_threshold:
                await self._flush()

        # Broadcast to WebSocket clients
        await self._broadcast(entry)

        return entry

    async def log_api_request(
        self,
        method: str,
        path: str,
        status_code: int,
        latency_ms: float,
        trace_id: Optional[str] = None,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        request_size: Optional[int] = None,
        response_size: Optional[int] = None,
        error_message: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log an API request/response"""
        level = LogLevel.ERROR if status_code >= 500 else (
            LogLevel.WARNING if status_code >= 400 else LogLevel.INFO
        )

        return await self.log(
            category=LogCategory.API_REQUEST,
            level=level,
            source="api",
            message=f"{method} {path} -> {status_code} ({latency_ms:.1f}ms)",
            trace_id=trace_id,
            method=method,
            path=path,
            status_code=status_code,
            latency_ms=latency_ms,
            client_ip=client_ip,
            user_agent=user_agent,
            request_size=request_size,
            response_size=response_size,
            error_message=error_message,
            **kwargs
        )

    async def log_llm_call(
        self,
        model: str,
        provider: str,
        latency_ms: float,
        tokens_in: Optional[int] = None,
        tokens_out: Optional[int] = None,
        trace_id: Optional[str] = None,
        error_message: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log an LLM call"""
        level = LogLevel.ERROR if error_message else LogLevel.INFO

        return await self.log(
            category=LogCategory.LLM_CALL,
            level=level,
            source="llm",
            message=f"{provider}/{model} ({latency_ms:.1f}ms, {tokens_in or 0}→{tokens_out or 0} tokens)",
            trace_id=trace_id,
            model=model,
            provider=provider,
            latency_ms=latency_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            error_message=error_message,
            **kwargs
        )

    async def log_tool_call(
        self,
        tool_name: str,
        latency_ms: float,
        result_status: str = "success",
        trace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        tool_params: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log a tool/MCP call"""
        level = LogLevel.ERROR if error_message else LogLevel.INFO
        category = LogCategory.MCP_CALL if "mcp" in tool_name.lower() else LogCategory.TOOL_CALL

        # Sanitize params
        safe_params = self._sanitize_params(tool_params) if tool_params else None

        return await self.log(
            category=category,
            level=level,
            source="tool",
            message=f"{tool_name} -> {result_status} ({latency_ms:.1f}ms)",
            trace_id=trace_id,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_params=safe_params,
            tool_result=result_status,
            latency_ms=latency_ms,
            error_message=error_message,
            **kwargs
        )

    async def log_error(
        self,
        source: str,
        error_type: str,
        error_message: str,
        stack_trace: Optional[str] = None,
        trace_id: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log an error"""
        return await self.log(
            category=LogCategory.ERROR,
            level=LogLevel.ERROR,
            source=source,
            message=f"{error_type}: {error_message}",
            trace_id=trace_id,
            error_type=error_type,
            error_message=error_message,
            stack_trace=stack_trace,
            **kwargs
        )

    async def log_security_event(
        self,
        event_type: str,
        details: Dict[str, Any],
        trace_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        **kwargs
    ) -> TriForceLogEntry:
        """Log a security event"""
        return await self.log(
            category=LogCategory.SECURITY,
            level=LogLevel.WARNING,
            source="security",
            message=f"Security event: {event_type}",
            trace_id=trace_id,
            agent_id=agent_id,
            metadata=details,
            **kwargs
        )

    def _sanitize_params(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove sensitive data from params"""
        if not params:
            return {}

        sensitive_keys = {"password", "api_key", "secret", "token", "credential", "auth"}
        safe = {}

        for key, value in params.items():
            if any(s in key.lower() for s in sensitive_keys):
                safe[key] = "[REDACTED]"
            elif isinstance(value, str) and len(value) > 500:
                safe[key] = value[:500] + "...[truncated]"
            elif isinstance(value, dict):
                safe[key] = self._sanitize_params(value)
            else:
                safe[key] = value

        return safe

    async def _periodic_flush(self):
        """Periodically flush logs to disk"""
        while self._running:
            try:
                await asyncio.sleep(self.flush_interval)
                async with self._lock:
                    if self._pending:
                        await self._flush()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["errors"] += 1
                logger.error(f"Error in periodic flush: {e}")

    async def _flush(self):
        """Flush pending entries to disk"""
        if not self._pending:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_file = self.log_dir / f"triforce_{today}.jsonl"

        try:
            entries_to_flush = self._pending.copy()
            self._pending.clear()

            with open(log_file, "a", encoding="utf-8") as f:
                for entry in entries_to_flush:
                    f.write(entry.to_json() + "\n")

            self._stats["total_flushed"] += len(entries_to_flush)
            logger.debug(f"Flushed {len(entries_to_flush)} log entries to {log_file}")

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"Failed to flush logs: {e}")
            # Put entries back for retry
            self._pending.extend(entries_to_flush)

    async def force_flush(self):
        """Force flush all pending entries"""
        async with self._lock:
            await self._flush()

    # WebSocket management
    def register_websocket(self, ws):
        """Register a WebSocket for live streaming"""
        self._websockets.add(ws)

    def unregister_websocket(self, ws):
        """Unregister a WebSocket"""
        self._websockets.discard(ws)

    async def _broadcast(self, entry: TriForceLogEntry):
        """Broadcast entry to WebSocket clients"""
        if not self._websockets:
            return

        message = entry.to_json()
        dead_sockets = set()

        for ws in self._websockets:
            try:
                await ws.send_text(message)
            except Exception:
                dead_sockets.add(ws)

        for ws in dead_sockets:
            self._websockets.discard(ws)

    # Query methods
    def get_recent(self, limit: int = 100, category: Optional[LogCategory] = None) -> List[Dict[str, Any]]:
        """Get recent log entries"""
        entries = list(self._buffer)
        if category:
            entries = [e for e in entries if e.category == category]
        return [e.to_dict() for e in entries[-limit:]]

    def get_by_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Get all entries for a trace ID"""
        return [e.to_dict() for e in self._buffer if e.trace_id == trace_id]

    def get_errors(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent errors"""
        entries = [e for e in self._buffer if e.level in (LogLevel.ERROR, LogLevel.CRITICAL)]
        return [e.to_dict() for e in entries[-limit:]]

    def get_api_traffic(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent API traffic"""
        entries = [e for e in self._buffer if e.category == LogCategory.API_REQUEST]
        return [e.to_dict() for e in entries[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """Get logger statistics"""
        category_counts: Dict[str, int] = {}
        level_counts: Dict[str, int] = {}
        for entry in self._buffer:
            category = entry.category.value if hasattr(entry.category, "value") else str(entry.category)
            level = entry.level.value if hasattr(entry.level, "value") else str(entry.level)
            category_counts[category] = category_counts.get(category, 0) + 1
            level_counts[level] = level_counts.get(level, 0) + 1

        try:
            started_at = datetime.fromisoformat(self._stats["started_at"])
            uptime_seconds = max(
                0.0,
                (datetime.now(timezone.utc) - started_at).total_seconds(),
            )
        except (KeyError, TypeError, ValueError):
            uptime_seconds = 0.0

        return {
            **self._stats,
            # `errors` historically counts internal flush/processing failures.
            # Keep it for compatibility and expose an unambiguous name as well.
            "internal_errors": self._stats["errors"],
            "error_entries": sum(
                level_counts.get(level, 0) for level in ("error", "critical")
            ),
            "category_counts": category_counts,
            "level_counts": level_counts,
            "buffer_size": len(self._buffer),
            "pending_flush": len(self._pending),
            "websocket_clients": len(self._websockets),
            "uptime_seconds": round(uptime_seconds, 3),
        }

    async def read_log_file(self, date: str) -> List[Dict[str, Any]]:
        """Read entries from a specific date's log file"""
        log_file = self.log_dir / f"triforce_{date}.jsonl"

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


class TriForceLoggingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware that logs all API traffic to TriForce.
    """

    def __init__(self, app, central_logger: TriForceCentralLogger):
        super().__init__(app)
        self.central_logger = central_logger

        # Paths to exclude from logging (health checks, metrics)
        self.exclude_paths = {
            "/health",
            "/healthz",
            "/ready",
            "/metrics",
            "/favicon.ico",
        }

    async def dispatch(self, request: Request, call_next):
        # Skip excluded paths
        if request.url.path in self.exclude_paths:
            return await call_next(request)

        # Generate trace ID
        trace_id = request.headers.get("X-Trace-ID") or \
                   request.headers.get("X-Correlation-ID") or \
                   str(uuid.uuid4())[:12]

        # Store in request state
        request.state.trace_id = trace_id

        # Get request info
        client_ip = request.client.host if request.client else None
        user_agent = request.headers.get("User-Agent")

        # Try to get request size
        request_size = None
        if "content-length" in request.headers:
            try:
                request_size = int(request.headers["content-length"])
            except ValueError:
                pass

        start_time = time.time()
        error_message = None
        response = None

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            error_message = str(e)
            status_code = 500
            raise
        finally:
            latency_ms = (time.time() - start_time) * 1000

            # Get response size if available
            response_size = None
            if response and hasattr(response, 'headers') and "content-length" in response.headers:
                try:
                    response_size = int(response.headers["content-length"])
                except (ValueError, AttributeError):
                    pass

            # Log to TriForce (fire and forget)
            asyncio.create_task(
                self.central_logger.log_api_request(
                    method=request.method,
                    path=request.url.path,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    trace_id=trace_id,
                    client_ip=client_ip,
                    user_agent=user_agent,
                    request_size=request_size,
                    response_size=response_size,
                    error_message=error_message,
                )
            )

        return response


# Singleton instance
central_logger = TriForceCentralLogger()


class MultiFileLogger:
    """
    Logs to multiple categorized files:
    - mcpserver.log - All MCP protocol traffic
    - v1.log - REST API /v1/ traffic
    - triforce.log - TriForce system events
    - tristar.log - TriStar agent events
    - aichat.log - AI chat/LLM calls
    - errors.log - All errors consolidated
    """

    def __init__(self, log_dir: Optional[str] = None):
        # Default: ./logs
        if log_dir is None:
            self.log_dir = LOG_DIR
        else:
            self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Create subdirectories
        (self.log_dir / "mcp").mkdir(exist_ok=True)
        (self.log_dir / "api").mkdir(exist_ok=True)
        (self.log_dir / "agents").mkdir(exist_ok=True)
        (self.log_dir / "system").mkdir(exist_ok=True)

        # File handles cache
        self._files = {}
        self._lock = asyncio.Lock()

    def _get_dated_path(self, subdir: str, name: str) -> Path:
        """Get dated log file path"""
        today = datetime.now().strftime("%Y-%m-%d")
        return self.log_dir / subdir / f"{name}_{today}.log"

    async def log_mcp(self, method: str, params: Any, result: Any, latency_ms: float, error: str = None):
        """Log MCP protocol traffic"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "mcp",
            "method": method,
            "params": str(params)[:500] if params else None,
            "result_size": len(str(result)) if result else 0,
            "latency_ms": round(latency_ms, 2),
            "error": error,
        }
        await self._write("mcp", "mcpserver", entry)
    
    async def log_mcp_tool_call(
        self, 
        tool_name: str, 
        params: Dict[str, Any], 
        result_status: str,
        latency_ms: float,
        caller: str = "unknown",
        result_preview: str = None,
        error: str = None
    ):
        """
        Log MCP tool calls to dedicated mcp_calls.jsonl
        v2.82: Unified MCP call logging - single source of truth
        
        Args:
            tool_name: Name of the tool called
            params: Tool parameters (will be sanitized)
            result_status: "success" or "error"
            latency_ms: Execution time
            caller: Who initiated the call (claude, gemini, user, etc.)
            result_preview: Optional truncated result preview
            error: Error message if failed
        """
        # Sanitize sensitive params
        safe_params = {}
        sensitive_keys = {"password", "api_key", "secret", "token", "credential", "auth"}
        for k, v in (params or {}).items():
            if any(s in k.lower() for s in sensitive_keys):
                safe_params[k] = "[REDACTED]"
            elif isinstance(v, str) and len(v) > 200:
                safe_params[k] = v[:200] + "..."
            else:
                safe_params[k] = v
        
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "caller": caller,
            "params": safe_params,
            "status": result_status,
            "latency_ms": round(latency_ms, 2),
            "result_preview": (result_preview[:300] + "...") if result_preview and len(result_preview) > 300 else result_preview,
            "error": error,
        }
        await self._write("mcp", "mcp_calls", entry)

    async def log_v1_api(self, method: str, path: str, status: int, latency_ms: float, client: str = None):
        """Log REST API /v1/ traffic"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "v1_api",
            "method": method,
            "path": path,
            "status": status,
            "latency_ms": round(latency_ms, 2),
            "client": client,
        }
        await self._write("api", "v1", entry)

    async def log_triforce(self, event: str, details: Dict[str, Any] = None):
        """Log TriForce system events"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "triforce",
            "event": event,
            "details": details,
        }
        await self._write("system", "triforce", entry)

    async def log_tristar(self, agent_id: str, action: str, details: Dict[str, Any] = None):
        """Log TriStar agent events"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "tristar",
            "agent_id": agent_id,
            "action": action,
            "details": details,
        }
        await self._write("agents", "tristar", entry)

    async def log_aichat(self, model: str, provider: str, tokens_in: int, tokens_out: int,
                         latency_ms: float, error: str = None):
        """Log AI/LLM chat calls"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "aichat",
            "model": model,
            "provider": provider,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "latency_ms": round(latency_ms, 2),
            "error": error,
        }
        await self._write("api", "aichat", entry)

    async def log_error(self, source: str, error_type: str, message: str, trace: str = None):
        """Log errors to consolidated error log"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "error",
            "source": source,
            "error_type": error_type,
            "message": message,
            "trace": trace[:1000] if trace else None,
        }
        await self._write("system", "errors", entry)

    async def _write(self, subdir: str, name: str, entry: Dict[str, Any]):
        """Write entry to log file"""
        async with self._lock:
            try:
                path = self._get_dated_path(subdir, name)
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            except Exception as e:
                logger.error(f"Failed to write to {name} log: {e}")


# Multi-file logger instance
multi_logger = MultiFileLogger()


def setup_triforce_logging():
    """
    Set up TriForce logging by attaching the handler to the root logger.
    Call this during application startup.
    """
    # Create handler
    handler = TriForceLogHandler(central_logger)
    handler.setLevel(logging.DEBUG)

    # Attach to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)

    # Also attach to key loggers
    key_loggers = [
        "ailinux",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastapi",
    ]

    for name in key_loggers:
        log = logging.getLogger(name)
        log.addHandler(handler)

    logger.info("TriForce logging handler attached to root logger")

    return handler
