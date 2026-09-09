"""Canonical TriForce server launcher.

The launcher is the single bridge from dotenv data into the backend process
environment. It never executes configuration as shell code. Process environment
values remain the highest-priority override; file values fill only missing keys.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping

from .config import Settings
from .settings_store import CONFIG_ENV, effective_environment, resolve_config_path


def build_server_process(
    *,
    config_path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    python: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Return validated Uvicorn argv and merged environment without executing."""
    source = dict(os.environ if environ is None else environ)
    selected = resolve_config_path(config_path)
    source[CONFIG_ENV] = str(selected)
    merged, _origins = effective_environment(selected, environ=source)
    settings = Settings.model_validate(merged, by_alias=True, by_name=True)

    executable = python or sys.executable
    argv = [
        executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        settings.server_host,
        "--port",
        str(settings.server_port),
        "--timeout-keep-alive",
        str(settings.server_keepalive),
    ]
    return argv, merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch TriForce with canonical configuration")
    parser.add_argument("--config")
    args = parser.parse_args()
    argv, env = build_server_process(config_path=args.config)
    # No secrets or full environment are printed here.
    print(
        f"TriForce launcher: {argv[argv.index('--host') + 1]}:"
        f"{argv[argv.index('--port') + 1]} config={env.get(CONFIG_ENV, '')}",
        flush=True,
    )
    os.execvpe(argv[0], argv, env)
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
