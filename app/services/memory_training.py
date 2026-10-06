"""Evidence distillation for the AILinux Memory Training Center.

This module never changes code, prompts, routing or curated memory.  It turns
selected Claude-Mem history and documented bug resolutions into bounded
candidates that still require an explicit promotion/implementation gate.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from .episodic_memory import redact

_SIGNAL_QUERIES = (
    "project_memory_revision",
    "feature_experience",
    "run_completed",
    "tool_failed",
    "test_failed",
    "provider_failed",
    "exception_raised",
)
_FAILURE_EVENTS = {"tool_failed", "test_failed", "provider_failed", "exception_raised"}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _project_payload(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return mirrored Project-Memory envelope and nested structured content."""
    outer = _json_object(row.get("narrative"))
    if not outer:
        outer = _json_object(row.get("text"))
    nested = _json_object(outer.get("content"))
    return outer, nested


async def _episodic_signals(provider: Any, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    compact: dict[int, dict[str, Any]] = {}
    warnings: list[str] = []
    for query in _SIGNAL_QUERIES:
        try:
            rows = await provider.search_global(query, limit)
        except Exception as exc:  # noqa: BLE001 - optional evidence source must fail open
            warnings.append(f"{query}:{type(exc).__name__}")
            continue
        for row in rows:
            meta = row.get("metadata") or {}
            event_type = str(meta.get("event_type") or "")
            if event_type in {"project_memory_revision", "feature_experience", "run_completed"} | _FAILURE_EVENTS:
                compact[int(row["id"])] = row

    by_project: dict[str, list[int]] = defaultdict(list)
    for row in compact.values():
        project = str(row.get("project") or "")
        if project:
            by_project[project].append(int(row["id"]))

    details: list[dict[str, Any]] = []
    # get_observations is deliberately bounded by the normal recall result cap.
    # Chunking preserves that contract while still allowing an operator digest.
    chunk_size = max(1, min(int(getattr(provider.settings, "memory_max_results", 4) or 4), 5))
    for project, ids in by_project.items():
        ids = sorted(set(ids))
        for offset in range(0, len(ids), chunk_size):
            chunk = ids[offset:offset + chunk_size]
            try:
                details.extend(await provider.get_observations(chunk, project))
            except Exception as exc:  # noqa: BLE001 - one broken scope must not kill the digest
                warnings.append(f"detail:{project}:{type(exc).__name__}")
    unique = {int(row["id"]): row for row in details if type(row.get("id")) is int}
    return list(unique.values()), warnings


def _distill_project_memory(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        if meta.get("event_type") != "project_memory_revision":
            continue
        entity = str(meta.get("entity_id") or row.get("id"))
        current = latest.get(entity)
        version = int(meta.get("version") or 0)
        current_version = int((current or {}).get("metadata", {}).get("version") or -1)
        if current is None or version > current_version or (version == current_version and row["id"] > current["id"]):
            latest[entity] = row

    best: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    for row in latest.values():
        meta = row.get("metadata") or {}
        if bool(meta.get("deleted")):
            continue
        outer, nested = _project_payload(row)
        status = str(nested.get("status") or outer.get("status") or "").lower()
        completed = status == "completed"
        if not completed:
            continue
        stages = nested.get("completed_stages") if isinstance(nested.get("completed_stages"), list) else []
        task = str(nested.get("task") or outer.get("title") or row.get("title") or "")[:1000]
        evidence = {
            "observation_id": row["id"],
            "project": row.get("project"),
            "entity_id": meta.get("entity_id"),
            "version": meta.get("version"),
            "server_seq": meta.get("server_seq"),
            "source": meta.get("source"),
        }
        if nested.get("schema") == "aicoder-feature-experience-v1":
            best.append({
                "kind": "verified_feature_experience",
                "title": str(outer.get("title") or row.get("title") or "Verified feature experience")[:240],
                "task": task,
                "summary": str(nested.get("summary") or "")[:3000],
                "architecture": str(nested.get("architecture") or "")[:2200],
                "verification": str(nested.get("verification") or "")[:1400],
                "lessons": str(nested.get("lessons") or "")[:1200],
                "future_features": str(nested.get("future_features") or "")[:1200],
                "verification_kind": str(nested.get("verification_kind") or "")[:64],
                "confidence": 0.95,
                "evidence": evidence,
            })
            regressions.append({
                "kind": "feature_regression",
                "title": f"Regression for verified feature: {task[:180]}",
                "verification": str(nested.get("verification") or "")[:1400],
                "evidence": evidence,
            })
            continue
        best.append({
            "kind": "verified_workflow",
            "title": str(outer.get("title") or row.get("title") or "Completed workflow")[:240],
            "task": task,
            "pattern": {"status": "completed", "completed_stages": stages[:32]},
            "confidence": 0.95 if stages else 0.85,
            "evidence": evidence,
        })
        regressions.append({
            "kind": "workflow_regression",
            "title": f"Replay completed workflow: {task[:180]}",
            "expected": {"status": "completed", "completed_stages": stages[:32]},
            "evidence": evidence,
        })
    return best, regressions


def _distill_feature_experiences(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    practices: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        meta = row.get("metadata") or {}
        if meta.get("event_type") != "feature_experience" or meta.get("verification_state") != "verified":
            continue
        fingerprint = str(meta.get("fingerprint") or row.get("id") or "")
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        task = str(meta.get("task") or row.get("title") or "")[:1000]
        evidence = {
            "observation_id": row.get("id"),
            "project": row.get("project"),
            "project_key": meta.get("project_key"),
            "repo": meta.get("repo"),
            "commit": meta.get("commit"),
            "source": meta.get("source"),
            "fingerprint": fingerprint[:32],
        }
        practices.append({
            "kind": "verified_feature_experience",
            "title": str(row.get("title") or f"Feature experience: {task}")[:240],
            "task": task,
            "summary": str(meta.get("summary") or "")[:3000],
            "architecture": str(meta.get("architecture") or "")[:2200],
            "verification": str(meta.get("verification") or "")[:1400],
            "lessons": str(meta.get("lessons") or "")[:1200],
            "future_features": str(meta.get("future_features") or "")[:1200],
            "confidence": 0.95,
            "evidence": evidence,
        })
        regressions.append({
            "kind": "feature_regression",
            "title": f"Regression for verified feature: {task[:180]}",
            "verification": str(meta.get("verification") or "")[:1400],
            "evidence": evidence,
        })
    return practices, regressions


def _distill_runtime(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    successful: list[dict[str, Any]] = []
    failures: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in rows:
        meta = row.get("metadata") or {}
        event_type = str(meta.get("event_type") or "")
        if event_type == "run_completed" and str(meta.get("verification_state") or "") == "completed":
            successful.append({
                "kind": "completed_run",
                "title": str(row.get("title") or "Completed run")[:240],
                "task": str(meta.get("task") or "")[:1000],
                "findings": str(meta.get("findings") or "")[:2000],
                "agent": str(meta.get("agent") or "")[:128],
                "model": str(meta.get("model") or "")[:256],
                "evidence": {"observation_id": row["id"], "project": row.get("project")},
            })
        elif event_type in _FAILURE_EVENTS:
            error = str(meta.get("error") or row.get("title") or "")[:1000]
            key = (
                event_type,
                str(meta.get("tool") or "")[:128],
                str(meta.get("error_type") or "")[:128],
                error,
            )
            item = failures.setdefault(key, {
                "kind": "anti_pattern",
                "event_type": event_type,
                "tool": key[1],
                "error_type": key[2],
                "error": error,
                "occurrences": 0,
                "evidence_ids": [],
            })
            item["occurrences"] += 1
            item["evidence_ids"].append(row["id"])
    return successful, list(failures.values())


def _distill_bugfixes(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    practices: list[dict[str, Any]] = []
    regressions: list[dict[str, Any]] = []
    for row in rows:
        evidence = {
            "report_id": row.get("id"),
            "fingerprint": row.get("fingerprint"),
            "app": row.get("app"),
            "repo": row.get("repo"),
            "fix_version": row.get("fix_version"),
            "fix_commit": row.get("fix_commit"),
            "docs_ref": row.get("docs_ref"),
            "resolved_at": row.get("resolved_at"),
        }
        practices.append({
            "kind": "verified_bugfix",
            "title": f"{row.get('app')}: {row.get('fingerprint')}",
            "fix_summary": row.get("fix_summary"),
            "verification": row.get("verification"),
            "occurrences": int(row.get("duplicate_count") or 1),
            "confidence": 1.0,
            "evidence": evidence,
        })
        regressions.append({
            "kind": "bug_regression",
            "title": f"Regression for {row.get('app')} bug {row.get('fingerprint')}",
            "verification": row.get("verification"),
            "evidence": evidence,
        })
    return practices, regressions


async def training_digest(*, engine: Any, limit: int = 50) -> dict[str, Any]:
    """Build a read-only Training Center digest from verified/bounded evidence."""
    limit = max(1, min(int(limit or 50), 100))
    episodic, warnings = await _episodic_signals(engine.provider, limit)

    from app.services.bug_reports import list_training_resolutions
    bugfix_rows = list_training_resolutions(limit=limit)

    workflow_practices, workflow_regressions = _distill_project_memory(episodic)
    feature_practices, feature_regressions = _distill_feature_experiences(episodic)
    successful_runs, anti_patterns = _distill_runtime(episodic)
    bugfix_practices, bug_regressions = _distill_bugfixes(bugfix_rows)

    return redact({
        "status": "ok" if not warnings else "degraded",
        "mode": "read_only_distillation",
        "automatic_promotion": False,
        "authority_order": ["runtime/code/tests", "project_memory", "curated_memory", "episodic_history"],
        "source_counts": {
            "episodic_selected": len(episodic),
            "documented_resolved_bugs": len(bugfix_rows),
        },
        "best_practices": workflow_practices + feature_practices + bugfix_practices,
        "successful_runs": successful_runs,
        "anti_patterns": anti_patterns,
        "regression_candidates": workflow_regressions + feature_regressions + bug_regressions,
        "warnings": warnings[:20],
    })
