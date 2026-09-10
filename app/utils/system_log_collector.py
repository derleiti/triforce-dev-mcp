"""
TriForce System Log Collector v1.0
==================================

Collects and forwards system logs to /triforce/logs/:
- Kernel logs (dmesg, journald kernel)
- System services (journald)
- Installed apps (nginx, docker, mysql, etc.)
- Hardware/GPU logs

Provides:
- /triforce/logs/triforce-error-debug/error.log - Errors only
- /triforce/logs/triforce-error-debug/debug.log - Debug info only
- /triforce/logs/system/ - System service logs
- /triforce/logs/kernel/ - Kernel logs
- /triforce/logs/apps/ - Application logs
"""

import asyncio
import logging
import subprocess
import shutil
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any
from logging.handlers import RotatingFileHandler
import json
import os

logger = logging.getLogger("ailinux.system.collector")

# Base directories
# FIX 2026-07-11: war `.../ "triforce" / "logs"` -> ergab den Doppelpfad
# ~/triforce/triforce/logs (Split-Brain mit dem echten ~/triforce/logs).
# parent.parent.parent ist bereits der Projektroot ~/triforce.
BACKEND_LOG_BASE = Path(os.environ.get("TRIFORCE_LOG_DIR", str(Path(__file__).parent.parent.parent / "logs")))
TRIFORCE_LOG_BASE = BACKEND_LOG_BASE

# Ensure directories exist
ERROR_DEBUG_DIR = TRIFORCE_LOG_BASE / "triforce-error-debug"
SYSTEM_LOG_DIR = TRIFORCE_LOG_BASE / "system"
KERNEL_LOG_DIR = TRIFORCE_LOG_BASE / "kernel"
APPS_LOG_DIR = TRIFORCE_LOG_BASE / "apps"

# Log format
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class SystemLogCollector:
    """
    Collects system logs from various sources and writes them to /triforce/logs/
    """

    def __init__(self):
        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._error_handler: Optional[RotatingFileHandler] = None
        self._debug_handler: Optional[RotatingFileHandler] = None
        self._initialized = False

        # Known log sources
        self.log_sources = {
            "kernel": {
                "command": ["journalctl", "-k", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": KERNEL_LOG_DIR,
                "filename": "kernel.log"
            },
            "systemd": {
                "command": ["journalctl", "-n", "200", "--no-pager", "-o", "short-iso"],
                "output_dir": SYSTEM_LOG_DIR,
                "filename": "systemd.log"
            },
            "nginx": {
                "command": ["journalctl", "-u", "nginx", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "nginx.log"
            },
            "docker": {
                "command": ["journalctl", "-u", "docker", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "docker.log"
            },
            "mysql": {
                "command": ["journalctl", "-u", "mysql", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "mysql.log"
            },
            "redis": {
                "command": ["journalctl", "-u", "redis", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "redis.log"
            },
            "ollama": {
                "command": ["journalctl", "-u", "ollama", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "ollama.log"
            },
            "ailinux-backend": {
                "command": ["journalctl", "-u", "ailinux-backend", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": APPS_LOG_DIR,
                "filename": "ailinux-backend.log"
            },
            "syslog": {
                "command": ["journalctl", "-p", "err..emerg", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": SYSTEM_LOG_DIR,
                "filename": "syslog-errors.log"
            },
            "boot": {
                "command": ["journalctl", "-b", "-n", "100", "--no-pager", "-o", "short-iso"],
                "output_dir": SYSTEM_LOG_DIR,
                "filename": "boot.log"
            },
        }

    def _ensure_directories(self):
        """Create all required directories"""
        for dir_path in [ERROR_DEBUG_DIR, SYSTEM_LOG_DIR, KERNEL_LOG_DIR, APPS_LOG_DIR]:
            dir_path.mkdir(parents=True, exist_ok=True)

    def _setup_handlers(self):
        """Setup error.log and debug.log handlers"""
        if self._initialized:
            return

        self._ensure_directories()

        # Error handler - only ERROR and above
        error_file = ERROR_DEBUG_DIR / "error.log"
        self._error_handler = RotatingFileHandler(
            error_file,
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=10,
            encoding='utf-8'
        )
        self._error_handler.setLevel(logging.ERROR)
        self._error_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

        # Debug handler - only DEBUG level
        debug_file = ERROR_DEBUG_DIR / "debug.log"
        self._debug_handler = RotatingFileHandler(
            debug_file,
            maxBytes=50 * 1024 * 1024,  # 50 MB
            backupCount=10,
            encoding='utf-8'
        )
        self._debug_handler.setLevel(logging.DEBUG)
        self._debug_handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

        # Add filter to debug handler to only capture DEBUG level
        class DebugOnlyFilter(logging.Filter):
            def filter(self, record):
                return record.levelno == logging.DEBUG

        self._debug_handler.addFilter(DebugOnlyFilter())

        # Do not attach to root logger to avoid pollution/loops
        # Handlers are used explicitly by _extract_and_log_errors
        
        self._initialized = True
        logger.info(f"System log collector initialized - error.log and debug.log in {ERROR_DEBUG_DIR}")

    async def collect_initial_logs(self) -> Dict[str, Any]:
        """
        Collect initial system logs at startup.
        Called once when backend starts.
        """
        self._setup_handlers()
        results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources_collected": [],
            "errors": []
        }

        for source_name, source_config in self.log_sources.items():
            try:
                output = await self._run_command(source_config["command"])
                if output:
                    output_dir = source_config["output_dir"]
                    output_dir.mkdir(parents=True, exist_ok=True)

                    output_file = output_dir / source_config["filename"]

                    # Append with timestamp header
                    with open(output_file, "a", encoding="utf-8") as f:
                        f.write(f"\n{'='*60}\n")
                        f.write(f"=== Log Collection: {datetime.now().isoformat()} ===\n")
                        f.write(f"{'='*60}\n")
                        f.write(output)
                        f.write("\n")

                    results["sources_collected"].append(source_name)
                    logger.debug(f"Collected logs from {source_name}")

                    # Also check for errors in the output and log them
                    self._extract_and_log_errors(source_name, output)

            except Exception as e:
                error_msg = f"Failed to collect {source_name}: {e}"
                results["errors"].append(error_msg)
                logger.warning(error_msg)

        logger.info(f"Initial log collection complete: {len(results['sources_collected'])} sources")
        return results

    def _extract_and_log_errors(self, source: str, log_content: str):
        """Extract errors from log content and write to error.log"""
        error_patterns = [
            r"(?i)\berror\b",
            r"(?i)\bfailed\b",
            r"(?i)\bcrash",
            r"(?i)\bpanic\b",
            r"(?i)\bcritical\b",
            r"(?i)\bsegfault\b",
            r"(?i)\bkilled\b",
            r"(?i)\bexception\b",
            r"(?i)\bout of memory\b",
        ]

        # Use a specific logger that doesn't propagate to avoid loops
        # This logger is only for writing to the error.log file
        error_logger = logging.getLogger("system.collector.errors")
        error_logger.propagate = False
        
        # Attach our handlers if not already attached
        if self._error_handler and not any(h == self._error_handler for h in error_logger.handlers):
            error_logger.addHandler(self._error_handler)

        for line in log_content.split("\n"):
            # A line that carries an explicit level field decides by that level.
            # Keyword matching alone misfires on the logger NAME: "uvicorn.error"
            # contains the word "error", so every startup line was stored as ERROR.
            level_match = re.search(
                r"\|\s*(DEBUG|INFO|NOTICE|WARNING|WARN|ERROR|CRITICAL|FATAL)\s*\|",
                line,
            )
            if level_match:
                if level_match.group(1).upper() in ("ERROR", "CRITICAL", "FATAL"):
                    error_logger.error(f"[{source}] {line.strip()}")
                continue

            # No structured level present -> fall back to keyword heuristics.
            for pattern in error_patterns:
                if re.search(pattern, line):
                    # Log to error.log via the error handler
                    error_logger.error(f"[{source}] {line.strip()}")
                    break

    async def _run_command(self, command: List[str], timeout: int = 30) -> Optional[str]:
        """Run a shell command and return output"""
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            if process.returncode == 0:
                return stdout.decode("utf-8", errors="replace")
            else:
                # Some commands may return non-zero but still have useful output
                output = stdout.decode("utf-8", errors="replace")
                if output:
                    return output
                return None

        except asyncio.TimeoutError:
            logger.warning(f"Command timed out: {' '.join(command)}")
            return None
        except Exception as e:
            logger.debug(f"Command failed: {' '.join(command)} - {e}")
            return None

    async def collect_dmesg(self) -> Optional[str]:
        """Collect kernel ring buffer (dmesg)"""
        output = await self._run_command(["dmesg", "-T", "--level=err,warn"])
        if output:
            output_file = KERNEL_LOG_DIR / "dmesg.log"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"# dmesg collected at {datetime.now().isoformat()}\n")
                f.write(output)
            return output
        return None

    async def collect_gpu_logs(self) -> Optional[str]:
        """Collect GPU-related logs (NVIDIA, AMD)"""
        gpu_info = []

        # Probe only tools that are installed; absent vendor utilities are normal.
        if shutil.which("nvidia-smi"):
            nvidia_output = await self._run_command(["nvidia-smi", "-q"])
            if nvidia_output:
                gpu_info.append("=== NVIDIA GPU ===\n")
                gpu_info.append(nvidia_output)

        if shutil.which("rocm-smi"):
            amd_output = await self._run_command(["rocm-smi"])
            if amd_output:
                gpu_info.append("\n=== AMD GPU (ROCm) ===\n")
                gpu_info.append(amd_output)

        if gpu_info:
            output_file = KERNEL_LOG_DIR / "gpu.log"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(f"# GPU logs collected at {datetime.now().isoformat()}\n")
                f.write("".join(gpu_info))
            return "".join(gpu_info)

        return None

    async def start_continuous_collection(self, interval: int = 300):
        """
        Start continuous log collection (every 5 minutes by default).
        Collects new error/warning logs from journald.
        """
        self.running = True
        self._task = asyncio.create_task(self._collection_loop(interval))
        logger.info(f"Started continuous log collection (interval: {interval}s)")

    async def stop(self):
        """Stop continuous collection"""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("System log collector stopped")

    async def _collection_loop(self, interval: int):
        """Main collection loop"""
        while self.running:
            try:
                await asyncio.sleep(interval)

                # Collect only new errors from journald
                output = await self._run_command([
                    "journalctl",
                    "-p", "err..emerg",
                    "--since", f"-{interval} seconds",
                    "--no-pager",
                    "-o", "short-iso"
                ])

                if output and output.strip():
                    # Write to system error log
                    error_file = ERROR_DEBUG_DIR / "system-errors.log"
                    with open(error_file, "a", encoding="utf-8") as f:
                        f.write(f"\n--- {datetime.now().isoformat()} ---\n")
                        f.write(output)

                    # Also extract and log via Python logging
                    self._extract_and_log_errors("journald", output)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in collection loop: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Get collector status"""
        return {
            "running": self.running,
            "initialized": self._initialized,
            "error_debug_dir": str(ERROR_DEBUG_DIR),
            "system_log_dir": str(SYSTEM_LOG_DIR),
            "kernel_log_dir": str(KERNEL_LOG_DIR),
            "apps_log_dir": str(APPS_LOG_DIR),
            "sources_configured": list(self.log_sources.keys()),
        }


# Singleton instance
system_log_collector = SystemLogCollector()


async def init_system_logging():
    """
    Initialize system logging at backend startup.
    Call this from main.py lifespan.
    """
    try:
        # Collect initial logs
        results = await system_log_collector.collect_initial_logs()

        # Collect dmesg
        await system_log_collector.collect_dmesg()

        # Collect GPU logs
        await system_log_collector.collect_gpu_logs()

        # Start continuous collection
        await system_log_collector.start_continuous_collection(interval=300)

        logger.info("System logging initialized successfully")
        return results

    except Exception as e:
        logger.error(f"Failed to initialize system logging: {e}")
        return {"error": str(e)}


def setup_error_debug_logging():
    """
    Setup error.log and debug.log handlers.
    Can be called independently during central_logging setup.
    """
    system_log_collector._setup_handlers()
