"""Shared read-only setup job status contract for CLI and Control Center."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATUS_DIR = Path("/run/triforce-control-center")
STATUS_FILE = STATUS_DIR / "setup-job.json"


def read_setup_job() -> dict[str, Any] | None:
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def job_is_running(status: dict[str, Any] | None = None) -> bool:
    status = status if status is not None else read_setup_job()
    return bool(status and status.get("state") in {"queued", "running"})
