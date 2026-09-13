"""Deterministic execution routing for TriForce agent calls.

The router is intentionally cheap: routing a small task must never require
another LLM call.  Native CLI agents handle ordinary reads/fixes directly;
AICoder is reserved for genuinely multi-step coding work and team runtime is
reserved for explicit requests or high-complexity tasks.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class AgentExecutionMode(str, Enum):
    AUTO = "auto"
    DIRECT = "direct"
    AICODER = "aicoder"
    TEAM = "team"


@dataclass(frozen=True)
class TaskRoutingDecision:
    mode: AgentExecutionMode
    score: int
    reasons: tuple[str, ...] = ()


_EXPLICIT_TEAM = re.compile(r"(?:^|\s)(?:/team\b|team[ -]?run\b|team[ -]?mode\b|multi[ -]?agent\b)", re.I)
_EXPLICIT_AICODER = re.compile(r"(?:^|\s)(?:/aicoder\b|aicoder[ -]?run\b)", re.I)
_EXPLICIT_DIRECT = re.compile(r"(?:^|\s)(?:/direct\b|direct[ -]?mode\b|single[ -]?agent\b|ohne\s+aicoder\b)", re.I)

# Strong signals that the task benefits from planning/validation across several
# changes.  The score is deliberately conservative: AUTO should stay direct.
_COMPLEX_SIGNALS: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (re.compile(r"\b(refactor|refaktor|architecture|architektur|migration|migrier)\w*", re.I), 3, "architecture/refactor"),
    (re.compile(r"\b(repo[- ]?wide|projektweit|codebase|alle[nr]?\s+(?:dateien|module|scripts))\b", re.I), 3, "wide scope"),
    (re.compile(r"\b(end[- ]?to[- ]?end|e2e|regression|regressions?tests?|integrationstest)\w*", re.I), 2, "broad verification"),
    (re.compile(r"\b(concurren|race condition|security|sicherheit|auth(?:entication|orization)?|berechtigung)\w*", re.I), 2, "high-risk concern"),
    (re.compile(r"\b(multi[- ]?service|mehrere\s+(?:services|module|komponenten|repos)|cross[- ]?component)\b", re.I), 3, "multiple components"),
    (re.compile(r"\b(release|package|paketier|deploy|deployment|publish|veröffentlich)\w*", re.I), 2, "delivery lifecycle"),
    (re.compile(r"\b(brainstorm|research|recherch|analysier.*(?:gesam|komplett)|autonom.*(?:bis|komplett))\b", re.I), 2, "open-ended investigation"),
)

_SIMPLE_SIGNALS = re.compile(
    r"\b(read|lies|lese|zeige|show|cat|grep|find|such|status|log|prüf|check|inspect|"
    r"erklär|explain|kleine?r?\s+fix|simple\s+fix|typo|einzelne?\s+datei|one\s+file)\b",
    re.I,
)


def _coerce_mode(value: str | AgentExecutionMode | None) -> AgentExecutionMode:
    if isinstance(value, AgentExecutionMode):
        return value
    normalized = str(value or "auto").strip().lower().replace("-", "_")
    aliases = {"single": "direct", "single_agent": "direct", "team_run": "team", "team_mode": "team"}
    normalized = aliases.get(normalized, normalized)
    try:
        return AgentExecutionMode(normalized)
    except ValueError as exc:
        raise ValueError("execution_mode must be one of: auto, direct, aicoder, team") from exc


def classify_agent_task(
    message: str,
    *,
    requested_mode: str | AgentExecutionMode | None = None,
    supports_direct: bool = True,
) -> TaskRoutingDecision:
    """Choose the cheapest execution mode that safely fits *message*.

    Explicit caller mode wins.  Textual chat overrides are next.  AUTO then
    uses deterministic complexity signals; it never calls an LLM classifier.
    """
    requested = _coerce_mode(requested_mode)
    text = str(message or "").strip()

    if requested is not AgentExecutionMode.AUTO:
        mode = requested
        if mode is AgentExecutionMode.DIRECT and not supports_direct:
            return TaskRoutingDecision(AgentExecutionMode.AICODER, 0, ("direct runtime unavailable",))
        return TaskRoutingDecision(mode, 0, ("explicit execution mode",))

    if _EXPLICIT_TEAM.search(text):
        return TaskRoutingDecision(AgentExecutionMode.TEAM, 10, ("explicit team request",))
    if _EXPLICIT_AICODER.search(text):
        return TaskRoutingDecision(AgentExecutionMode.AICODER, 6, ("explicit AICoder request",))
    if _EXPLICIT_DIRECT.search(text):
        mode = AgentExecutionMode.DIRECT if supports_direct else AgentExecutionMode.AICODER
        return TaskRoutingDecision(mode, 0, ("explicit direct request" if supports_direct else "direct runtime unavailable",))

    score = 0
    reasons: list[str] = []
    for pattern, weight, reason in _COMPLEX_SIGNALS:
        if pattern.search(text):
            score += weight
            reasons.append(reason)

    # Long prompts are not automatically complex, but several requested actions
    # plus an already non-trivial score are a useful secondary signal.
    action_words = len(re.findall(r"\b(?:und|then|danach|anschließend|sowie|plus)\b", text, re.I))
    if score and action_words >= 3:
        score += 1
        reasons.append("multiple requested stages")

    if not supports_direct:
        return TaskRoutingDecision(AgentExecutionMode.AICODER, score, tuple(reasons or ["direct runtime unavailable"]))
    if score >= 8:
        return TaskRoutingDecision(AgentExecutionMode.TEAM, score, tuple(reasons))
    if score >= 4:
        return TaskRoutingDecision(AgentExecutionMode.AICODER, score, tuple(reasons))

    if _SIMPLE_SIGNALS.search(text):
        reasons.append("simple/local operation")
    else:
        reasons.append("direct-first default")
    return TaskRoutingDecision(AgentExecutionMode.DIRECT, score, tuple(reasons))
