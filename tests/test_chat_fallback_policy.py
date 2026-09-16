import httpx
import pytest
from fastapi import HTTPException

from app.services.chat import (
    _allow_ollama_fallback,
    _exception_status_code,
    _is_ollama_cloud_model,
)


def _http_error(status: int) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": "upstream"})


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_hard_provider_failures_do_not_chain_into_ollama_cloud(status):
    assert not _allow_ollama_fallback(_http_error(status), "gpt-oss:20b-cloud")


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_hard_provider_failures_may_fallback_to_local_ollama(status):
    assert _allow_ollama_fallback(_http_error(status), "qwen3:14b")


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_provider_failures_remain_fallback_eligible(status):
    assert _allow_ollama_fallback(_http_error(status), "gpt-oss:20b-cloud")


def test_httpx_status_is_preserved_through_exception_chain():
    request = httpx.Request("POST", "https://example.invalid")
    response = httpx.Response(403, request=request)
    cause = httpx.HTTPStatusError("forbidden", request=request, response=response)
    wrapped = RuntimeError("provider failed")
    wrapped.__cause__ = cause
    assert _exception_status_code(wrapped) == 403


@pytest.mark.parametrize(
    ("model", "expected"),
    [("ollama/gpt-oss:20b-cloud", True), ("gpt-oss:cloud/20b", True), ("qwen3:14b", False)],
)
def test_ollama_cloud_model_detection(model, expected):
    assert _is_ollama_cloud_model(model) is expected

@pytest.mark.asyncio
async def test_cloud_fallback_re_raises_hard_provider_error_without_secondary_call():
    from app.services.chat import _fallback_to_ollama

    error = _http_error(402)
    with pytest.raises(HTTPException) as caught:
        async for _ in _fallback_to_ollama(
            [{"role": "user", "content": "hello"}],
            None,
            5.0,
            "Mistral",
            error,
            fallback_model="gpt-oss:20b-cloud",
        ):
            pass
    assert caught.value is error


@pytest.mark.asyncio
async def test_transient_failure_still_streams_configured_fallback(monkeypatch):
    import app.services.chat as chat

    calls = []

    async def fake_stream(model, messages, *, temperature, stream, timeout):
        calls.append((model, messages, temperature, stream, timeout))
        yield "fallback-ok"

    monkeypatch.setattr(chat, "_stream_ollama", fake_stream)
    chunks = [
        chunk
        async for chunk in chat._fallback_to_ollama(
            [{"role": "user", "content": "hello"}],
            0.2,
            7.0,
            "Mistral",
            _http_error(503),
            fallback_model="gpt-oss:20b-cloud",
        )
    ]
    assert chunks == ["fallback-ok"]
    assert calls[0][0] == "gpt-oss:20b-cloud"
