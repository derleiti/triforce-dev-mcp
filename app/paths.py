"""Canonical local filesystem paths for TriForce.

Development keeps its historical repository-local defaults. Packaged services
set ``TRIFORCE_STATE_DIR``/``TRIFORCE_LOG_DIR`` so mutable data is separated
from read-only program files under /opt/triforce.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(os.environ.get("TRIFORCE_PROJECT_ROOT", str(Path(__file__).resolve().parents[1]))).expanduser().resolve()
STATE_DIR = Path(os.environ.get("TRIFORCE_STATE_DIR", str(PROJECT_ROOT))).expanduser().resolve()
LOG_DIR = Path(os.environ.get("TRIFORCE_LOG_DIR", str(PROJECT_ROOT / "logs"))).expanduser().resolve()
VAULT_DIR = Path(os.environ.get("TRIFORCE_VAULT_DIR", str(STATE_DIR / ".vault"))).expanduser().resolve()
USERS_DIR = Path(os.environ.get("TRIFORCE_USERS_DIR", str(STATE_DIR / "users"))).expanduser().resolve()
DATA_DIR = Path(os.environ.get("TRIFORCE_DATA_DIR", str(STATE_DIR / "data"))).expanduser().resolve()
_TRISTAR_DEFAULT = str(STATE_DIR / "tristar") if os.environ.get("TRIFORCE_STATE_DIR") else "/var/tristar"
TRISTAR_DIR = Path(os.environ.get("TRISTAR_BASE", _TRISTAR_DEFAULT)).expanduser()
CONFIG_DIR = Path(os.environ.get("TRIFORCE_CONFIG_DIR", str(PROJECT_ROOT / "config"))).expanduser().resolve()
