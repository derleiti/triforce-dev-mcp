from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("app.rate_limit")
# pyrate-limiter logs every bucket construction at INFO; route declarations create
# several buckets during import. Keep normal startup logs focused on actionable events.
logging.getLogger("pyrate_limiter").setLevel(logging.WARNING)
logging.getLogger("pyrate_limiter.limiter").setLevel(logging.WARNING)

try:
    from fastapi_limiter import FastAPILimiter as _UpstreamFastAPILimiter
except Exception:
    _UpstreamFastAPILimiter = None

try:
    from fastapi_limiter.depends import RateLimiter as _UpstreamRateLimiter
except Exception:
    _UpstreamRateLimiter = None

try:
    from pyrate_limiter import Duration, Limiter, Rate
except Exception:
    Duration = Limiter = Rate = None

_compat_notice_logged = False


def _log_compat_once(message: str) -> None:
    global _compat_notice_logged
    if not _compat_notice_logged:
        logger.info(message)
        _compat_notice_logged = True


def _supports_legacy_rate_limiter() -> bool:
    if _UpstreamRateLimiter is None:
        return False
    try:
        parameters = inspect.signature(_UpstreamRateLimiter.__init__).parameters
    except (TypeError, ValueError):
        return False
    return "times" in parameters and "seconds" in parameters


_HAS_LEGACY_RATE_LIMITER = _supports_legacy_rate_limiter()


class FastAPILimiter:
    """Compatibility shim for old and new fastapi-limiter package APIs."""

    @classmethod
    async def init(cls, *args: Any, **kwargs: Any) -> None:
        if _UpstreamFastAPILimiter is not None and hasattr(_UpstreamFastAPILimiter, "init"):
            await _UpstreamFastAPILimiter.init(*args, **kwargs)
            return
        _log_compat_once(
            "fastapi-limiter legacy init API unavailable; using compatibility mode without global limiter init"
        )

    @classmethod
    async def close(cls) -> None:
        if _UpstreamFastAPILimiter is not None and hasattr(_UpstreamFastAPILimiter, "close"):
            await _UpstreamFastAPILimiter.close()


class RateLimiter:
    """Compatibility wrapper that is safe with nested FastAPI routers."""

    def __init__(
        self,
        times: int = 1,
        seconds: int = 1,
        identifier: Callable | None = None,
        callback: Callable | None = None,
        blocking: bool = False,
        **_: Any,
    ) -> None:
        self._delegate = None
        self._legacy = False

        if _UpstreamRateLimiter is None:
            _log_compat_once(
                "fastapi-limiter is unavailable; request rate limiting is disabled"
            )
            return

        try:
            if _HAS_LEGACY_RATE_LIMITER:
                self._delegate = _UpstreamRateLimiter(
                    times=times,
                    seconds=seconds,
                    identifier=identifier,
                    callback=callback,
                )
                self._legacy = True
            elif Limiter is not None and Rate is not None and Duration is not None:
                limiter = Limiter(Rate(times, Duration.SECOND * seconds))
                kwargs: dict[str, Any] = {"limiter": limiter, "blocking": blocking}
                if identifier is not None:
                    kwargs["identifier"] = identifier
                if callback is not None:
                    kwargs["callback"] = callback
                self._delegate = _UpstreamRateLimiter(**kwargs)
                _log_compat_once(
                    "fastapi-limiter legacy API unavailable; using in-process compatibility rate limiting"
                )
            else:
                _log_compat_once(
                    "fastapi-limiter is incompatible; request rate limiting is disabled"
                )
        except Exception as exc:
            logger.warning("Failed to initialize compatibility rate limiter: %s", exc)

    async def __call__(self, request: Request, response: Response) -> Any:
        if self._delegate is None:
            return None
        if not self._legacy:
            return await self._delegate(request=request, response=response)

        # fastapi-limiter <=0.1 scans request.app.routes and assumes every entry
        # is an APIRoute. FastAPI 0.116+ also stores _IncludedRouter entries.
        upstream = _UpstreamFastAPILimiter
        if upstream is None or not getattr(upstream, "redis", None):
            raise RuntimeError("FastAPILimiter.init must run before requests")
        identifier = self._delegate.identifier or upstream.identifier
        callback = self._delegate.callback or upstream.http_callback
        rate_key = await identifier(request)
        route_key = f"{request.method}:{request.scope.get('path', '')}"
        key = f"{upstream.prefix}:{rate_key}:{route_key}"
        try:
            pexpire = await self._delegate._check(key)
        except Exception as exc:
            if exc.__class__.__name__ != "NoScriptError":
                raise
            upstream.lua_sha = await upstream.redis.script_load(upstream.lua_script)
            pexpire = await self._delegate._check(key)
        if pexpire != 0:
            return await callback(request, response, pexpire)
        return None
