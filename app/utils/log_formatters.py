"""Human-friendly, structured TriForce/Uvicorn log formatters.

Console output is intentionally optimized for operators: fixed columns, subsystem
badges, severity/status colours, request details, useful context ids and source
locations. File output can use the same formatter with colours disabled.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from collections.abc import Mapping
from typing import Any

from app.utils.redaction import is_secret_key

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"

_LEVEL_STYLE = {
    logging.DEBUG: "\033[38;5;45m",
    logging.INFO: "\033[38;5;82m",
    logging.WARNING: "\033[38;5;220m",
    logging.ERROR: "\033[38;5;196m",
    logging.CRITICAL: "\033[1;37;41m",
}
_LEVEL_LABEL = {
    logging.DEBUG: "DBG",
    logging.INFO: "INF",
    logging.WARNING: "WRN",
    logging.ERROR: "ERR",
    logging.CRITICAL: "CRT",
}
_CATEGORY_STYLE = {
    "HTTP": "\033[38;5;39m",
    "UVICORN": "\033[38;5;45m",
    "MCP": "\033[38;5;213m",
    "API": "\033[38;5;51m",
    "AUTH": "\033[38;5;214m",
    "SECURITY": "\033[38;5;196m",
    "AGENT": "\033[38;5;141m",
    "AI": "\033[38;5;111m",
    "MEMORY": "\033[38;5;114m",
    "DB": "\033[38;5;75m",
    "DOCKER": "\033[38;5;208m",
    "SYSTEM": "\033[38;5;250m",
    "APP": "\033[38;5;252m",
}
_METHOD_STYLE = {
    "GET": "\033[38;5;45m",
    "POST": "\033[38;5;82m",
    "PUT": "\033[38;5;220m",
    "PATCH": "\033[38;5;214m",
    "DELETE": "\033[38;5;196m",
    "OPTIONS": "\033[38;5;141m",
    "HEAD": "\033[38;5;75m",
}


def _env_color_enabled() -> bool:
    """Resolve console colour policy while preserving the old coloured default."""
    if os.environ.get("NO_COLOR") is not None:
        return False
    value = str(os.environ.get("TRIFORCE_LOG_COLOR", "always")).strip().lower()
    return value not in {"0", "false", "no", "never", "off"}


def _paint(text: str, style: str, enabled: bool) -> str:
    if not enabled or not style:
        return text
    return f"{style}{text}{_RESET}"


def redact_sensitive(value: Any) -> str:
    """Redact credentials from arbitrary log text without mutating application data."""
    text = str(value)
    key = r"(?:pair_code|handoff_code|resume_token|workspace_token|workspace_context|access_token|refresh_token|session_token|api_key|apikey|password|passwd|secret|credential|token)"
    # Authorization schemes need to be handled before generic key/value masking.
    text = re.sub(r"(?i)(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+", r"\1[REDACTED]", text)
    # URL query strings.
    text = re.sub(rf"(?i)([?&]{key}=)[^&#\s]+", lambda m: f"{m.group(1)}[REDACTED]", text)
    # Dict/JSON/logfmt values surrounded by quotes.
    text = re.sub(
        rf"(?i)((?:['\"]?){key}(?:['\"]?)\s*[:=]\s*['\"])[^'\"]*(['\"])",
        lambda m: f"{m.group(1)}[REDACTED]{m.group(2)}",
        text,
    )
    # Remaining unquoted key/value forms.
    text = re.sub(
        rf"(?i)(\b{key}\b\s*[:=]\s*)(?!\[REDACTED\])[^\s,;&}}\]]+",
        lambda m: f"{m.group(1)}[REDACTED]",
        text,
    )
    return text


_PRIVATE_LOG_KEYS = {
    "workspace_context",
    "openai/organization",
    "openai/session",
    "openai/subject",
    "openai/userlocation",
}


def sanitize_log_data(value: Any, *, key: str | None = None) -> Any:
    """Return a recursively sanitized copy suitable for persistent logs.

    Credential-shaped keys use the shared default-deny secret classifier.
    ``workspace_context`` is additionally treated as private correlation data: it
    may be non-secret by protocol, but persisting it can disclose user/workspace
    identity or routing context.
    """
    if key is not None and (is_secret_key(key) or key.lower() in _PRIVATE_LOG_KEYS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {
            str(child_key): sanitize_log_data(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_log_data(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive(value)
    return value


class RedactingFormatter(logging.Formatter):
    """Plain file formatter that applies the same credential redaction as the console."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_sensitive(super().format(record))


def _status_style(status: int) -> str:
    if status >= 500:
        return "\033[1;38;5;196m"
    if status >= 400:
        return "\033[38;5;220m"
    if status >= 300:
        return "\033[38;5;45m"
    if status >= 200:
        return "\033[38;5;82m"
    return "\033[38;5;250m"


def log_category(name: str) -> str:
    """Map a logger name to a stable operator-facing subsystem badge."""
    lower = str(name or "").lower()
    if lower.startswith("uvicorn.access"):
        return "HTTP"
    if lower.startswith("uvicorn"):
        return "UVICORN"
    if "security" in lower:
        return "SECURITY"
    if "auth" in lower or "oauth" in lower:
        return "AUTH"
    if "mcp" in lower:
        return "MCP"
    if "agent" in lower or "tristar" in lower or "swarm" in lower:
        return "AGENT"
    if any(token in lower for token in ("llm", "model", "provider", "ollama", "openrouter", "gemini", "mistral")):
        return "AI"
    if "memory" in lower or "redis" in lower:
        return "MEMORY"
    if any(token in lower for token in ("database", "sql", "maria", "postgres")):
        return "DB"
    if any(token in lower for token in ("docker", "container")):
        return "DOCKER"
    if "api" in lower or "route" in lower or "fastapi" in lower:
        return "API"
    if any(token in lower for token in ("system", "service", "hardware", "startup")):
        return "SYSTEM"
    return "APP"


class TriForceConsoleFormatter(logging.Formatter):
    """Structured operator formatter with special handling for Uvicorn access logs."""

    def __init__(self, *, use_color: bool | None = None, include_source: bool = True) -> None:
        super().__init__()
        self.use_color = _env_color_enabled() if use_color is None else bool(use_color)
        self.include_source = include_source

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        dt = datetime.fromtimestamp(record.created)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    @staticmethod
    def _short_logger(name: str) -> str:
        value = str(name or "root")
        if value.startswith("ailinux."):
            value = value[8:]
        if len(value) > 27:
            value = "..." + value[-24:]
        return value

    @staticmethod
    def _context(record: logging.LogRecord) -> list[str]:
        parts: list[str] = []
        for key, label in (
            ("trace_id", "trace"),
            ("request_id", "req"),
            ("session_id", "sess"),
            ("agent_id", "agent"),
            ("tool_name", "tool"),
            ("tool_call_id", "call"),
            ("failure_type", "failure"),
            ("provider", "provider"),
            ("fallback", "fallback"),
            ("duration_ms", "ms"),
            ("exit_code", "exit"),
            ("timed_out", "timeout"),
        ):
            value = getattr(record, key, None)
            if value not in (None, ""):
                text = str(value)
                if len(text) > 32:
                    text = text[:29] + "..."
                parts.append(f"{label}={text}")
        return parts

    def _prefix(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record)
        level = _LEVEL_LABEL.get(record.levelno, record.levelname[:3].upper())
        category = log_category(record.name)
        logger_name = self._short_logger(record.name)
        timestamp = _paint(timestamp, _DIM, self.use_color)
        level = _paint(f"{level:>3}", _LEVEL_STYLE.get(record.levelno, ""), self.use_color)
        category = _paint(f"{category:<8}", _CATEGORY_STYLE.get(category, ""), self.use_color)
        logger_name = _paint(f"{logger_name:<27}", _BOLD, self.use_color)
        pid = _paint(f"pid={record.process}", _DIM, self.use_color)
        return f"{timestamp} │ {level} │ {category} │ {logger_name} │ {pid}"

    def _access_message(self, record: logging.LogRecord) -> str | None:
        args: Any = record.args
        if record.name != "uvicorn.access" or not isinstance(args, tuple) or len(args) < 5:
            return None
        client, method, path, http_version, raw_status = args[:5]
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            return None
        method_text = str(method).upper()
        method_field = _paint(f"{method_text:<7}", _METHOD_STYLE.get(method_text, ""), self.use_color)
        status_field = _paint(str(status), _status_style(status), self.use_color)
        client_field = _paint(str(client), _DIM, self.use_color)
        safe_path = redact_sensitive(path)
        return f"{client_field} │ {method_field} {safe_path} HTTP/{http_version} │ status={status_field}"

    def format(self, record: logging.LogRecord) -> str:
        prefix = self._prefix(record)
        access_message = self._access_message(record)
        message = access_message if access_message is not None else redact_sensitive(record.getMessage())

        context = self._context(record)
        if context:
            message = f"{' '.join(context)} │ {message}"

        if self.include_source and access_message is None:
            source = f"{record.filename}:{record.lineno}"
            message = f"{message}  {_paint('↳ ' + source, _DIM, self.use_color)}"

        # Keep multi-line tracebacks/readable payloads visually attached to the first line.
        lines = str(message).splitlines() or [""]
        rendered = prefix + " │ " + lines[0]
        continuation = "\n".join(f"{' ' * 4}│ {line}" for line in lines[1:])
        if continuation:
            rendered += "\n" + continuation

        if record.exc_info:
            exc = redact_sensitive(self.formatException(record.exc_info))
            rendered += "\n" + "\n".join(f"    │ {line}" for line in exc.splitlines())
        if record.stack_info:
            rendered += "\n" + "\n".join(f"    │ {line}" for line in self.formatStack(record.stack_info).splitlines())
        return rendered
