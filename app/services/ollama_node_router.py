"""Resource-aware Ollama node routing for TriForce.

Remote workers are opportunistic. They may disappear at any time without
becoming a dependency of the public hub: unavailable/stale/draining nodes are
omitted and the local Hetzner Ollama endpoint remains a deterministic fallback.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class OllamaEndpoint:
    node_id: str
    base_url: str
    score: float = 0.0
    reason: str = ""


_ZOMBIE_DEFAULT_MODELS = {
    "hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest",
}


def _csv_models(value: str) -> set[str]:
    return {item.strip() for item in str(value or "").split(",") if item.strip()}


def _normal_model(model: str) -> str:
    value = str(model or "").strip()
    return value.split("/", 1)[1] if value.startswith("ollama/") else value


def _local_capacity_score() -> float:
    """Score the public hub while keeping a deliberate offload bias."""
    try:
        cpu = float(psutil.cpu_percent(interval=None))
    except Exception:
        cpu = 0.0
    try:
        memory = float(psutil.virtual_memory().percent)
    except Exception:
        memory = 0.0
    try:
        load1 = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load1 = 0.0
    cpu_count = max(int(psutil.cpu_count() or 1), 1)

    cpu_headroom = max(0.0, 1.0 - min(cpu, 100.0) / 100.0)
    memory_headroom = max(0.0, 1.0 - min(memory, 100.0) / 100.0)
    load_ratio = load1 / cpu_count
    load_headroom = max(0.0, 1.0 - min(load_ratio, 1.5) / 1.5)
    resource = 0.42 * cpu_headroom + 0.42 * memory_headroom + 0.16 * load_headroom

    # The hub is intentionally penalized so healthy workers absorb ordinary
    # compute, but a heavily loaded worker can still lose to a healthy hub.
    return max(1.0, min(72.0, resource * 72.0))


def _federation_snapshot(node_id: str) -> dict | None:
    """Read cached federation health without performing network I/O."""
    try:
        from .server_federation import federation
        node = federation.nodes.get(node_id)
        if node is None:
            return None
        return {
            "status": getattr(node.status, "value", str(node.status)),
            "score": float(node.selection_score(stale_after=federation.STALE_AFTER)),
            "draining": bool(node.draining),
            "heartbeat_age": node.heartbeat_age_seconds(),
        }
    except Exception:
        return None


def _remote_score(node_id: str, default_score: float) -> tuple[float, str]:
    snapshot = _federation_snapshot(node_id)
    if snapshot is None:
        return default_score, "bootstrap-static"

    status = str(snapshot.get("status") or "unknown")
    if snapshot.get("draining"):
        return -1.0, "draining"
    if status in {"offline", "degraded"}:
        return -1.0, status
    if status == "healthy":
        score = float(snapshot.get("score") or 0.0)
        if score <= 0:
            return -1.0, "stale-or-saturated"
        return score, "live-metrics"

    # During process startup, before the first heartbeat completes, retain the
    # historical static ordering. Actual request failover remains authoritative.
    return default_score * 0.95, "heartbeat-pending"


def ollama_candidates(model: str = "") -> list[OllamaEndpoint]:
    """Return live candidates ordered by resource score.

    A missing optional worker is never fatal. The local Hetzner endpoint is
    always retained as a final candidate.
    """
    model_name = _normal_model(model)
    backup_url = os.getenv(
        "TRIFORCE_OLLAMA_BACKUP_URL", "http://10.10.0.3:11434"
    ).rstrip("/")
    zombie_url = os.getenv(
        "TRIFORCE_OLLAMA_ZOMBIE_URL", "http://10.10.0.2:11434"
    ).rstrip("/")
    local_url = os.getenv(
        "OLLAMA_BASE_URL",
        os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434"),
    ).rstrip("/")

    zombie_models = _ZOMBIE_DEFAULT_MODELS | _csv_models(
        os.getenv("TRIFORCE_OLLAMA_ZOMBIE_MODELS", "")
    )

    candidates: list[OllamaEndpoint] = []

    def add_remote(node_id: str, base_url: str, default_score: float) -> None:
        if not base_url:
            return
        score, reason = _remote_score(node_id, default_score)
        if score < 0:
            return
        candidates.append(OllamaEndpoint(node_id, base_url, score, reason))

    if model_name and model_name in zombie_models:
        # Strong model affinity: zombie gets the first opportunity when live.
        add_remote("zombie-pc", zombie_url, 96.0)
        add_remote("backup", backup_url, 76.0)
    else:
        add_remote("backup", backup_url, 88.0)

    candidates.append(
        OllamaEndpoint("hetzner", local_url, _local_capacity_score(), "local-fallback")
    )

    # Avoid duplicate URLs and prefer the highest scoring identity.
    deduped: dict[str, OllamaEndpoint] = {}
    for endpoint in candidates:
        previous = deduped.get(endpoint.base_url)
        if previous is None or endpoint.score > previous.score:
            deduped[endpoint.base_url] = endpoint

    ordered = sorted(deduped.values(), key=lambda item: item.score, reverse=True)

    # For the zombie-only local model, preserve model affinity over load score:
    # trying an unrelated worker first only adds a predictable 404.
    if model_name in zombie_models:
        ordered.sort(key=lambda item: 0 if item.node_id == "zombie-pc" else 1)

    return ordered
