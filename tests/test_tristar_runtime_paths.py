import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _probe(env_updates: dict[str, str | None]) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    for key, value in env_updates.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    out = subprocess.check_output(
        [sys.executable, '-c', 'from app.paths import TRISTAR_DIR; print(TRISTAR_DIR)'],
        cwd=ROOT,
        env=env,
        text=True,
    )
    return out.strip()


def test_source_default_keeps_legacy_var_tristar():
    assert _probe({'TRIFORCE_STATE_DIR': None, 'TRISTAR_BASE': None}) == '/var/tristar'


def test_package_state_derives_tristar_path():
    assert _probe({'TRIFORCE_STATE_DIR': '/tmp/triforce-state', 'TRISTAR_BASE': None}) == '/tmp/triforce-state/tristar'


def test_explicit_tristar_base_wins():
    assert _probe({'TRIFORCE_STATE_DIR': '/tmp/triforce-state', 'TRISTAR_BASE': '/tmp/explicit-tristar'}) == '/tmp/explicit-tristar'


def test_core_mcp_state_users_follow_canonical_tristar_dir():
    checks = {
        'app/services/tristar_mcp.py': 'TRISTAR_BASE = TRISTAR_DIR',
        'app/services/command_queue.py': 'TRISTAR_DIR / "queue/state.json"',
        'app/services/memory_index.py': 'TRISTAR_DIR / "memory_index.json"',
        'app/utils/mcp_auth.py': 'TRISTAR_DIR / "auth"',
        'app/services/mesh_coordinator.py': 'TRIFORCE_BASE = TRISTAR_DIR',
    }
    for name, needle in checks.items():
        text = (ROOT / name).read_text()
        assert needle in text, name
