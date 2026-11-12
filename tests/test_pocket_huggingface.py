"""Fast, deterministic public catalogue regression suite; no live network."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.routes import pocket_huggingface as hf
from app.utils.auth_middleware import PUBLIC_EXACT_PATHS


def test_only_public_text_gguf_models_returned():
    valid = {
        "id": "bartowski/SmolLM2-360M-Instruct-GGUF",
        "private": False,
        "gated": False,
        "pipeline_tag": "text-generation",
        "tags": ["gguf", "text-generation"],
        "downloads": 500,
    }
    assert hf._entry(valid)["id"] == valid["id"]
    assert hf._entry(dict(valid, private=True)) is None
    assert hf._entry(dict(valid, gated="auto")) is None
    assert hf._entry(dict(valid, pipeline_tag="text-to-image")) is None
    assert hf._entry(dict(valid, id="evil/../hidden")) is None
    assert hf._entry(dict(valid, tags=["safetensors"])) is None


def test_litert_is_not_gguf_and_never_auto_converts():
    valid = {
        "id": "litert-community/gemma-4-E2B-it-litert-lm",
        "private": False, "gated": False,
        "tags": ["litert-lm"],
        "pipeline_tag": None,
    }
    assert hf._entry(valid) is None
    item = hf._entry(valid, "litertlm")
    assert item["format"] == "LiteRT-LM"
    assert item["url"].startswith("https://huggingface.co/")


def test_recommended_is_existing_small_q4_file_only():
    x = {
        "siblings": [
            {"rfilename": "model-Q4_K_M.gguf", "size": 280_000_000},
            {"rfilename": "large-Q4_K_M.gguf", "size": 5_000_000_000},
            {"rfilename": "bad-Q4_K_M.bin", "size": 250_000_000},
            {"rfilename": "../traversal-Q4_K_M.gguf", "size": 200_000_000},
        ]
    }
    result = hf._suggested_file(x)
    assert result == {"filename": "model-Q4_K_M.gguf", "bytes": 280_000_000}


def test_search_calls_official_hf_with_strict_filter_and_bounds(monkeypatch):
    hf._CACHE.clear()
    observed = []

    async def fake_get(client, url, params=None):
        observed.append((url, params))
        return [
            {"id": "litert-community/gemma-4-E2B-it-litert-lm",
             "tags": ["litert-lm"], "pipeline_tag": None, "downloads": 900},
            {"id": "other/private", "tags": ["litert-lm"], "private": True},
        ]

    monkeypatch.setattr(hf, "_get_json", fake_get)
    data = asyncio.run(hf.hf_search("gemma", 6, "litertlm"))
    assert data["count"] == 1
    assert data["results"][0]["format"] == "LiteRT-LM"
    assert observed == [(hf._HF, {
        "search": "gemma", "filter": "litert-lm",
        "sort": "downloads", "direction": "-1", "limit": 25,
    })]
    again = asyncio.run(hf.hf_search("gemma", 6, "litertlm"))
    assert again["count"] == 1
    assert len(observed) == 1  # in-memory TTL cache hit


def test_reject_invalid_query():
    with pytest.raises(HTTPException) as error:
        asyncio.run(hf.hf_search("\n", 5, "gguf"))
    assert error.value.status_code == 422


def test_catalogue_paths_are_public_readonly_and_exact():
    assert "/v1/pocket/huggingface/search" in PUBLIC_EXACT_PATHS
    assert "/v1/pocket/huggingface/recommended" in PUBLIC_EXACT_PATHS
    assert "/v1/pocket/huggingface/admin" not in PUBLIC_EXACT_PATHS
