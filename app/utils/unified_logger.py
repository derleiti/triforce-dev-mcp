"""
Unified Logger v2.1
Zentrales Log für alle TriForce Komponenten
Fix: Keine Duplikate durch propagate=False
"""

import json
import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

from .log_formatters import TriForceConsoleFormatter, redact_sensitive

UNIFIED_LOG_PATH = Path(os.environ.get("TRIFORCE_LOG_DIR", str(Path(__file__).parent.parent.parent / "logs"))) / "unified.log"
UNIFIED_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

class UnifiedFormatter(logging.Formatter):
    def format(self, record):
        name = record.name
        if name.startswith("ailinux."):
            name = name[8:]
        if len(name) > 25:
            name = name[:22] + "..."
        record.short_name = name.ljust(25)
        return redact_sensitive(super().format(record))

LOG_FORMAT = '%(asctime)s|%(levelname)-7s|%(short_name)s|%(message)s'
LOG_DATEFMT = '%Y-%m-%d %H:%M:%S'

_initialized = False
_unified_handler = None

def setup_unified_logging():
    global _initialized, _unified_handler
    if _initialized:
        return str(UNIFIED_LOG_PATH)
    
    # Single file handler
    _unified_handler = RotatingFileHandler(
        UNIFIED_LOG_PATH,
        maxBytes=50*1024*1024,
        backupCount=5,
        encoding='utf-8'
    )
    _unified_handler.setLevel(logging.DEBUG)
    _unified_handler.setFormatter(UnifiedFormatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    
    # Single stdout handler  
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(TriForceConsoleFormatter(include_source=True))
    
    # Nur zum ailinux Root Logger hinzufügen
    ailinux_logger = logging.getLogger("ailinux")
    ailinux_logger.setLevel(logging.DEBUG)
    ailinux_logger.addHandler(_unified_handler)
    ailinux_logger.addHandler(stdout_handler)
    ailinux_logger.propagate = False  # Verhindert Duplikate
    
    # Andere Logger mit propagate=False
    other_loggers = [
        "server_federation",
        "mcp_ws_server",
    ]
    for name in other_loggers:
        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(_unified_handler)
        logger.addHandler(stdout_handler)
        logger.propagate = False
    
    _initialized = True
    ailinux_logger.info("=" * 60)
    ailinux_logger.info("UNIFIED_LOG v2.1 | %s + stdout", UNIFIED_LOG_PATH)
    ailinux_logger.info("=" * 60)
    return str(UNIFIED_LOG_PATH)

def tool_result_error(result):
    """Return an error message when a successful handler call contains a structured failure."""
    payload = result
    if isinstance(result, str):
        text = result.strip()
        if not (text.startswith("{") and text.endswith("}")):
            return None
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("timed_out") is True:
        return "execution timed out"
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        detail = payload.get("stderr") or payload.get("errors") or payload.get("error")
        return f"process exited with code {exit_code}" + (f": {str(detail)[:240]}" if detail else "")
    if payload.get("success") is False or payload.get("ok") is False:
        detail = payload.get("error") or payload.get("errors") or payload.get("message") or "structured failure"
        return str(detail)[:300]
    status = str(payload.get("status") or "").strip().lower()
    if status in {"error", "failed", "failure"}:
        return str(payload.get("error") or payload.get("message") or status)[:300]
    error = payload.get("error")
    if error in (None, "", False):
        return None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("detail") or error)[:300]
    return str(error)[:300]

def log_tool_call(tool_name: str, params: dict, result=None, error=None):
    """Audit a tool call without copying commands, prompts, stdout or credentials into logs."""
    logger = logging.getLogger("ailinux.mcp.tools")
    structured_error = error or tool_result_error(result)
    safe_meta = {"tool_name": tool_name}
    if isinstance(result, dict):
        for key in ("exit_code", "timed_out", "elapsed_ms"):
            if key in result:
                safe_meta["duration_ms" if key == "elapsed_ms" else key] = result.get(key)
    if structured_error:
        logger.error("TOOL_CALL | %s | ERROR | %s", tool_name, str(structured_error)[:300], extra=safe_meta)
    else:
        logger.info("TOOL_CALL | %s | OK | result_type=%s", tool_name, type(result).__name__, extra=safe_meta)
