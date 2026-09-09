"""
Central Logging v3.0 - Komprimiert & Optimiert
==============================================
Logs: ./triforce/logs/ AND /triforce/logs/
- all.log, auth.log, mcp.log, api.log, llm.log, agents.log, errors.log
- /triforce/logs/triforce-error-debug/{error,debug,warning}.log
"""
import logging
import os
import sys
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Directories
_BASE = Path(__file__).parent.parent.parent
LOG_DIR = Path(os.environ.get("TRIFORCE_LOG_DIR", str(_BASE / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)
SYSTEM_LOG_DIR = LOG_DIR
ERROR_DEBUG_DIR = SYSTEM_LOG_DIR / "triforce-error-debug"

# Config
FMT = "%(asctime)s|%(levelname)-8s|%(name)-25s|%(message)s"
FMT_DETAIL = "%(asctime)s|%(levelname)-8s|%(name)-25s|%(filename)s:%(lineno)d|%(message)s"
DATE_FMT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES, BACKUP = 50 * 1024 * 1024, 10

# State
_init = {"central": False, "debug": False}


class ColorFormatter(logging.Formatter):
    """Formatter with ANSI colors for console."""
    C = {10: '\033[36m', 20: '\033[32m', 30: '\033[33m', 40: '\033[31m', 50: '\033[35m'}
    R = '\033[0m'

    def format(self, r):
        return f"{self.C.get(r.levelno, '')}{super().format(r)}{self.R}"


class LevelFilter(logging.Filter):
    """Filter for exact log level matching."""
    def __init__(self, level):
        super().__init__()
        self.level = level

    def filter(self, record):
        return record.levelno == self.level


@lru_cache(maxsize=32)
def _handler(path: str, level: int = logging.DEBUG) -> RotatingFileHandler:
    """Cached rotating file handler factory."""
    h = RotatingFileHandler(path, maxBytes=MAX_BYTES, backupCount=BACKUP, encoding='utf-8')
    h.setLevel(level)
    h.setFormatter(logging.Formatter(FMT_DETAIL, DATE_FMT))
    return h


def _add_category_logger(name: str, filename: str) -> None:
    """Setup category logger with file handler."""
    logger = logging.getLogger(f"ailinux.{name}")
    logger.addHandler(_handler(str(LOG_DIR / filename)))
    logger.propagate = True


def setup_central_logging(console_level: int = logging.INFO, enable_console: bool = True) -> None:
    """Initialize central logging. Call once at startup."""
    if _init["central"]:
        return

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # Core handlers
    root.addHandler(_handler(str(LOG_DIR / "all.log")))
    root.addHandler(_handler(str(LOG_DIR / "errors.log"), logging.ERROR))

    # Console
    if enable_console:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(console_level)
        console.setFormatter(ColorFormatter(FMT, DATE_FMT))
        root.addHandler(console)

    # Category loggers
    for name, file in [
        ("auth", "auth.log"), ("mcp", "mcp.log"), ("api", "api.log"),
        ("llm", "llm.log"), ("agents", "agents.log"), ("security", "security.log"),
        ("system", "system.log"), ("tristar", "tristar.log"), ("triforce", "triforce.log")
    ]:
        _add_category_logger(name, file)

    # Third-party
    for name, file in [("uvicorn", "uvicorn.log"), ("uvicorn.access", "access.log")]:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.addHandler(_handler(str(LOG_DIR / file)))
        lg.propagate = True

    logging.getLogger("fastapi").propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _init["central"] = True
    _setup_error_debug(root)

    root.info("=" * 50)
    root.info(f"TriForce Logging v3.0 | {LOG_DIR} | {ERROR_DEBUG_DIR}")
    root.info("=" * 50)


def _setup_error_debug(root: logging.Logger) -> None:
    """Setup error.log, debug.log, warning.log in /triforce/logs/triforce-error-debug/"""
    if _init["debug"]:
        return

    try:
        ERROR_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        target = ERROR_DEBUG_DIR
    except PermissionError:
        target = LOG_DIR / "error-debug"
        target.mkdir(parents=True, exist_ok=True)
        logging.warning(f"Using fallback: {target}")
    except Exception as e:
        logging.error(f"Error/debug setup failed: {e}")
        return

    # Error handler
    err_h = RotatingFileHandler(target / "error.log", maxBytes=MAX_BYTES, backupCount=BACKUP, encoding='utf-8')
    err_h.setLevel(logging.ERROR)
    err_h.setFormatter(logging.Formatter(FMT_DETAIL, DATE_FMT))
    root.addHandler(err_h)

    # Debug handler (DEBUG only)
    dbg_h = RotatingFileHandler(target / "debug.log", maxBytes=MAX_BYTES, backupCount=BACKUP, encoding='utf-8')
    dbg_h.setLevel(logging.DEBUG)
    dbg_h.setFormatter(logging.Formatter(FMT_DETAIL, DATE_FMT))
    dbg_h.addFilter(LevelFilter(logging.DEBUG))
    root.addHandler(dbg_h)

    # Warning handler (WARNING only)
    warn_h = RotatingFileHandler(target / "warning.log", maxBytes=MAX_BYTES, backupCount=BACKUP, encoding='utf-8')
    warn_h.setLevel(logging.WARNING)
    warn_h.setFormatter(logging.Formatter(FMT_DETAIL, DATE_FMT))
    warn_h.addFilter(LevelFilter(logging.WARNING))
    root.addHandler(warn_h)

    _init["debug"] = True
    logging.getLogger("ailinux.system").info(f"Error/Debug logging: {target}")


def get_logger(name: str) -> logging.Logger:
    """Get logger with ailinux prefix."""
    if not _init["central"]:
        setup_central_logging()
    return logging.getLogger(f"ailinux.{name}" if not name.startswith("ailinux.") else name)


# Convenience loggers
get_auth_logger = lambda: get_logger("auth")
get_mcp_logger = lambda: get_logger("mcp")
get_api_logger = lambda: get_logger("api")
get_llm_logger = lambda: get_logger("llm")
get_agents_logger = lambda: get_logger("agents")
get_security_logger = lambda: get_logger("security")
get_system_logger = lambda: get_logger("system")
get_tristar_logger = lambda: get_logger("tristar")
get_triforce_logger = lambda: get_logger("triforce")
