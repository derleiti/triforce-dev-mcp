"""Small deterministic Ollama node router for TriForce.

Normal Ollama/cloud-proxy work is offloaded to backup first. Zombie-PC is used
for explicitly known local models, while the public Hetzner host remains the
last-resort local fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OllamaEndpoint:
    node_id: str
    base_url: str


_ZOMBIE_DEFAULT_MODELS = {
    "hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest",
}


def _csv_models(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _normal_model(model: str) -> str:
    value = str(model or "").strip()
    return value.split("/", 1)[1] if value.startswith("ollama/") else value


def ollama_candidates(model: str = "") -> list[OllamaEndpoint]:
    """Return ordered endpoints for one model without performing network I/O."""
    model_name = _normal_model(model)
    backup = OllamaEndpoint(
        "backup",
        os.getenv("TRIFORCE_OLLAMA_BACKUP_URL", "http://10.10.0.3:11434").rstrip("/"),
    )
    zombie = OllamaEndpoint(
        "zombie-pc",
        os.getenv("TRIFORCE_OLLAMA_ZOMBIE_URL", "http://10.10.0.2:11434").rstrip("/"),
    )
    local = OllamaEndpoint(
        "hetzner",
        os.getenv("OLLAMA_BASE_URL", os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")).rstrip("/"),
    )
    zombie_models = _ZOMBIE_DEFAULT_MODELS | _csv_models(
        os.getenv("TRIFORCE_OLLAMA_ZOMBIE_MODELS", "")
    )

    ordered: list[OllamaEndpoint]
    if model_name and model_name in zombie_models:
        ordered = [zombie, backup, local]
    else:
        ordered = [backup, local]

    result: list[OllamaEndpoint] = []
    seen: set[str] = set()
    for endpoint in ordered:
        if not endpoint.base_url or endpoint.base_url in seen:
            continue
        seen.add(endpoint.base_url)
        result.append(endpoint)
    return result
