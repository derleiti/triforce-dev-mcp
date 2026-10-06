#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.revenue_loop import run_once

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    print(json.dumps(asyncio.run(run_once()), ensure_ascii=False, indent=2))
