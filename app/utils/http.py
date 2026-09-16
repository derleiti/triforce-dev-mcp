from __future__ import annotations

from typing import Tuple

from httpx import Response, ResponseNotRead


def extract_http_error(response: Response | None, *, default_message: str = "Upstream request failed", default_code: str = "upstream_error") -> Tuple[str, str]:
    """Return a tuple of (message, code) derived from an HTTPX response."""
    message = default_message
    code = default_code

    if response is None:
        return message, code

    try:
        data = response.json()
    except ResponseNotRead:
        status = getattr(response, "status_code", None)
        reason = getattr(response, "reason_phrase", "") or ""
        if status:
            message = f"HTTP {status}{f' {reason}' if reason else ''}"
        return message, code
    except ValueError:
        try:
            text = (response.text or "").strip()
        except ResponseNotRead:
            text = ""
        if text:
            message = text
        return message, code

    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or message)
            code = str(error.get("code") or code)
            return message, code

        detail = data.get("detail")
        if isinstance(detail, dict):
            message = str(detail.get("message") or message)
            detail_code = detail.get("code")
            if detail_code:
                code = str(detail_code)
            return message, code

        if isinstance(detail, str):
            message = detail
            return message, code

    text = str(data)
    if text:
        message = text
    return message, code
