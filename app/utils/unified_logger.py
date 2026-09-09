"""
Unified Logger v2.1
Zentrales Log für alle TriForce Komponenten
Fix: Keine Duplikate durch propagate=False
"""

import logging
import os
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

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
        return super().format(record)

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
    stdout_handler.setFormatter(UnifiedFormatter(LOG_FORMAT, datefmt=LOG_DATEFMT))
    
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

def log_tool_call(tool_name: str, params: dict, result=None, error=None):
    logger = logging.getLogger("ailinux.mcp.tools")
    if error:
        logger.error(f"TOOL_CALL | {tool_name} | ERROR: {error}")
    else:
        result_preview = str(result)[:100] + "..." if len(str(result)) > 100 else str(result)
        logger.info(f"TOOL_CALL | {tool_name} | OK | {result_preview}")
