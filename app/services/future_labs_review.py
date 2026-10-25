"""Operator-only, read-only Future Labs historical proposal review.

Uses explicit Claude-Mem IDs, rejects scope drift and never treats a historical
discussion as a verified code decision. No automatic promotion or write.
"""
from __future__ import annotations

import hashlib
from typing import Any

from .episodic_memory import redact

PROJECT = "triforce:account:admin@ailinux.me"
IDS = tuple(range(19, 38))
TOPICS = (
    ("reproducible_releases", "Reproducible ISO and signed release receipts", (19, 23, 26, 28, 34, 35),
     "Pinned manifest, repeated builds, checksums, signing, VM boot"),
    ("safe_updates", "Tested upgrades, rollback and kernel fallback", (26, 34, 35, 37),
     "Upgrade and rollback drill with known-good kernel"),
    ("agent_contract", "Agent runtime stability and permission contracts", (20, 21, 22, 24, 30, 31, 32),
     "Authorization, cancellation, error and tool permission contract tests"),
    ("local_privacy", "Local/private-first inference policy", (24, 25, 28, 32),
     "Network-egress and user opt-in verification"),
    ("aicoder_flagship", "AICoder install-to-task workflow", (19, 23, 34, 35),
     "Clean desktop installation and task end-to-end"),
    ("hardware_matrix", "Kernel/GPU compatibility matrix", (23, 29, 33, 36),
     "AMD, Intel, NVIDIA, VM and fallback kernel checks"),
)


async def review(provider: Any) -> dict[str, Any]:
    """Require the complete known source set before reporting a complete review."""
    found: dict[int, dict] = {}
    batch_size = max(1, min(int(getattr(provider.settings, "memory_max_results", 4)), 5))
    warnings: list[str] = []
    for pos in range(0, len(IDS), batch_size):
        wanted = list(IDS[pos:pos + batch_size])
        try:
            rows = await provider.get_observations(wanted, PROJECT)
        except Exception as exc:
            warnings.append(f"readback:{type(exc).__name__}")
            continue
        for row in rows:
            ident = row.get("id")
            title = str(row.get("title") or "")
            if type(ident) is not int or ident not in wanted or row.get("project") != PROJECT:
                continue
            if "Future Lab" not in title:
                continue
            found[ident] = row
    missing = [i for i in IDS if i not in found]
    sources = []
    for ident in sorted(found):
        row = found[ident]
        narrative = str(row.get("narrative") or row.get("text") or "")
        sources.append({
            "observation_id": ident, "project": PROJECT,
            "created_at": row.get("created_at"),
            "sha256": hashlib.sha256(narrative.encode("utf-8")).hexdigest(),
        })
    topics = []
    for key, title, ids, criteria in TOPICS:
        present = [i for i in ids if i in found]
        topics.append({
            "id": key, "title": title,
            "historical_state": "proposal_not_approved",
            "runtime_verification": "not_assessed",
            "evidence_ids": present,
            "missing_evidence_ids": [i for i in ids if i not in found],
            "acceptance_criteria": criteria,
        })
    return redact({
        "schema": "future-labs-review-v1",
        "status": "complete" if not missing and not warnings else "degraded",
        "read_only": True, "automatic_promotion": False,
        "source_project": PROJECT, "source_count": len(sources),
        "missing_ids": missing, "sources": sources,
        "topics": topics, "warnings": warnings[:12],
        "notice": "Historical AI proposals are untrusted evidence, never verified implementation claims.",
    })
