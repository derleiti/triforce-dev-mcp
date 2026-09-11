from __future__ import annotations

import logging


def test_gemini_backward_compatible_aliases_share_singleton():
    from app.services.gemini_access import gemini_access, gemini_access_point, gemini_service

    assert gemini_access_point is gemini_access
    assert gemini_service is gemini_access


def test_pyrate_startup_logger_is_not_info_level():
    import app.utils.rate_limit_compat  # noqa: F401

    assert logging.getLogger("pyrate_limiter").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("pyrate_limiter.limiter").getEffectiveLevel() >= logging.WARNING


def test_federation_source_does_not_log_raw_signed_frames():
    from pathlib import Path

    source = Path("app/services/federation_websocket.py").read_text(encoding="utf-8")
    route_source = Path("app/routes/federation.py").read_text(encoding="utf-8")
    assert "Raw WS message" not in source
    assert "WS Route received from" not in route_source
