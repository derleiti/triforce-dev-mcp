"""
TriForce Auto-Evolution Service v1.0
=====================================

Multi-Agent Backend-Weiterentwicklung durch KI-Kollaboration.
Aktiviert alle Worker-Agents für kollektive Codebase-Analyse,
Recherche und automatische Verbesserungsvorschläge.

Usage:
    from app.services.auto_evolve import AutoEvolveService

    service = AutoEvolveService()
    result = await service.run_evolution(mode="analyze")
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path

from app.paths import TRISTAR_DIR
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ailinux.auto_evolve")


class EvolutionMode(str, Enum):
    """Evolution execution modes."""
    ANALYZE = "analyze"      # Only analysis, no code changes
    SUGGEST = "suggest"      # Analysis + concrete code suggestions
    IMPLEMENT = "implement"  # Analysis + code changes in feature branch
    FULL_AUTO = "full_auto"  # Complete cycle including tests and PR


class FindingSeverity(str, Enum):
    """Finding severity levels."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingCategory(str, Enum):
    """Finding categories."""
    PERFORMANCE = "performance"
    SECURITY = "security"
    QUALITY = "quality"
    ARCHITECTURE = "architecture"
    SCALABILITY = "scalability"


@dataclass
class EvolutionFinding:
    """A single finding from the evolution analysis."""
    category: FindingCategory
    severity: FindingSeverity
    file: str
    line: Optional[int]
    current: str
    suggested: str
    impact: str
    effort: str
    consensus: float = 0.0
    agent_votes: Dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "category": self.category.value,
            "severity": self.severity.value,
        }


@dataclass
class EvolutionResult:
    """Result of an evolution run."""
    evolution_id: str
    mode: EvolutionMode
    started_at: str
    completed_at: Optional[str] = None

    agents_consulted: List[str] = field(default_factory=list)
    findings: List[EvolutionFinding] = field(default_factory=list)

    codebase_stats: Dict[str, int] = field(default_factory=dict)
    memory_context: List[Dict[str, Any]] = field(default_factory=list)
    web_research: List[Dict[str, Any]] = field(default_factory=list)

    next_actions: List[str] = field(default_factory=list)
    memory_stored: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        data["findings"] = [f.to_dict() for f in self.findings]
        return data


class AutoEvolveService:
    """
    Multi-Agent Auto-Evolution Service.

    Orchestrates all CLI agents to analyze, research, and improve
    the codebase through collective intelligence.
    """

    # Agent configurations
    AGENT_ROLES = {
        "claude-mcp": {
            "role": "Security & Architecture Auditor",
            "focus": ["security", "architecture", "code quality"],
            "prompt": """Analysiere das AILinux-Backend auf:
1. Sicherheitslücken (SQL Injection, XSS, API Key Exposure)
2. Architektur-Probleme (Circular Imports, God Classes, Tight Coupling)
3. SOLID-Prinzipien Verletzungen
4. Error Handling Lücken

Fokus-Dateien: app/routes/, app/services/, app/middleware/

Antworte im JSON-Format:
{
  "findings": [
    {"file": "...", "line": N, "issue": "...", "severity": "high|medium|low", "suggestion": "..."}
  ]
}"""
        },
        "codex-mcp": {
            "role": "Code Optimizer & Refactoring Expert",
            "focus": ["performance", "optimization", "refactoring"],
            "prompt": """Analysiere das AILinux-Backend auf:
1. Performance-Bottlenecks (sync I/O, N+1 Queries, Missing Caching)
2. Code-Duplikation (DRY-Violations)
3. Async/Await Fehler
4. Resource-Leaks

Fokus-Dateien: app/services/chat.py, app/services/gemini_access.py

Antworte im JSON-Format:
{
  "findings": [
    {"file": "...", "line": N, "issue": "...", "impact": "...", "optimization": "..."}
  ]
}"""
        },
        "gemini-mcp": {
            "role": "Lead Researcher & Innovation Scout",
            "focus": ["research", "innovation", "trends"],
            "prompt": """Als Lead Researcher:
1. Recherchiere aktuelle FastAPI/Python Best Practices 2024
2. Finde neue MCP-Patterns und -Tools
3. Identifiziere relevante neue LLM-APIs
4. Schlage innovative Features vor

Nutze Web-Suche für aktuelle Informationen.

Antworte im JSON-Format:
{
  "research": [
    {"topic": "...", "finding": "...", "source": "...", "applicability": "..."}
  ],
  "innovations": [
    {"idea": "...", "benefit": "...", "effort": "low|medium|high"}
  ]
}"""
        },
        "opencode-mcp": {
            "role": "Integration & Testing Engineer",
            "focus": ["integration", "testing", "compatibility"],
            "prompt": """Analysiere das AILinux-Backend auf:
1. API-Kompatibilität (OpenAI-Compat, MCP-Standard)
2. Test-Coverage Lücken
3. Deployment-Readiness
4. Dependency-Risiken

Fokus-Dateien: app/routes/openai_compat.py, app/routes/mcp.py

Antworte im JSON-Format:
{
  "findings": [
    {"area": "...", "issue": "...", "fix": "...", "priority": N}
  ]
}"""
        },
    }

    # Codebase search patterns
    SEARCH_PATTERNS = [
        ("TODO|FIXME|HACK|XXX|BUG", "Pending issues"),
        ("except.*pass|except Exception:", "Swallowed exceptions"),
        ("time\\.sleep|import time", "Blocking operations"),
        ("os\\.environ\\.get.*None\\)", "Missing env defaults"),
        ("# type: ignore", "Type check bypasses"),
    ]

    def __init__(self, backend_path: str = "/home/zombie/triforce"):
        self.backend_path = Path(backend_path)
        self.prompts_path = TRISTAR_DIR / "prompts"
        self.evolution_log = TRISTAR_DIR / "logs/evolution"
        self.evolution_log.mkdir(parents=True, exist_ok=True)

        # Service imports (lazy)
        self._mcp_service = None
        self._agent_controller = None
        self._memory_controller = None

    async def _get_mcp_service(self):
        """Lazy load MCP service."""
        if self._mcp_service is None:
            from app.services.tristar_mcp import TriStarMCPService
            self._mcp_service = TriStarMCPService()
        return self._mcp_service

    async def _get_agent_controller(self):
        """Lazy load agent controller."""
        if self._agent_controller is None:
            try:
                from app.services.tristar.agent_controller import AgentController
                self._agent_controller = AgentController()
            except ImportError:
                logger.warning("AgentController not available")
        return self._agent_controller

    async def _get_memory_controller(self):
        """Lazy load memory controller."""
        if self._memory_controller is None:
            try:
                from app.services.tristar.memory_controller import MemoryController
                self._memory_controller = MemoryController()
            except ImportError:
                logger.warning("MemoryController not available")
        return self._memory_controller

    async def run_evolution(
        self,
        mode: EvolutionMode = EvolutionMode.ANALYZE,
        focus_areas: Optional[List[str]] = None,
        max_findings: int = 50,
    ) -> EvolutionResult:
        """
        Execute an evolution cycle.

        Args:
            mode: Evolution execution mode
            focus_areas: Optional list of focus areas to prioritize
            max_findings: Maximum number of findings to return

        Returns:
            EvolutionResult with all findings and recommendations
        """
        evolution_id = f"evo_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"

        result = EvolutionResult(
            evolution_id=evolution_id,
            mode=mode,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        logger.info(f"[EVOLUTION START] {evolution_id} mode={mode.value}")

        try:
            # Phase 1: Agent Mesh Activation
            agent_results = await self._activate_agent_mesh()
            result.agents_consulted = list(agent_results.keys())

            # Phase 2: Codebase Analysis
            codebase_findings = await self._analyze_codebase()
            result.codebase_stats = codebase_findings.get("stats", {})

            # Phase 3: Memory Context
            memory_context = await self._load_memory_context()
            result.memory_context = memory_context

            # Phase 4: Web Research (if gemini available)
            if "gemini-mcp" in agent_results:
                web_research = await self._web_research()
                result.web_research = web_research

            # Phase 5: Consolidate Findings
            all_findings = self._consolidate_findings(
                agent_results,
                codebase_findings,
                focus_areas,
            )
            result.findings = all_findings[:max_findings]

            # Phase 6: Generate Next Actions
            result.next_actions = self._generate_actions(result.findings, mode)

            # Phase 7: Store in Memory
            result.memory_stored = await self._store_evolution_result(result)

            result.completed_at = datetime.now(timezone.utc).isoformat()

        except Exception as e:
            logger.exception(f"Evolution failed: {e}")
            result.error = str(e)
            result.completed_at = datetime.now(timezone.utc).isoformat()

        # Log result
        await self._log_evolution(result)

        return result

    async def _activate_agent_mesh(self) -> Dict[str, Any]:
        """Activate all CLI agents with their specific tasks."""
        results = {}

        agent_controller = await self._get_agent_controller()
        if not agent_controller:
            logger.warning("Agent controller not available, using fallback")
            return await self._fallback_agent_analysis()

        # Broadcast to all agents in parallel
        tasks = []
        for agent_id, config in self.AGENT_ROLES.items():
            task = asyncio.create_task(
                self._query_agent(agent_controller, agent_id, config["prompt"])
            )
            tasks.append((agent_id, task))

        # Wait for all with timeout
        for agent_id, task in tasks:
            try:
                result = await asyncio.wait_for(task, timeout=120)
                results[agent_id] = result
                logger.info(f"Agent {agent_id} completed analysis")
            except asyncio.TimeoutError:
                logger.warning(f"Agent {agent_id} timed out")
                results[agent_id] = {"error": "timeout"}
            except Exception as e:
                logger.warning(f"Agent {agent_id} failed: {e}")
                results[agent_id] = {"error": str(e)}

        return results

    async def _query_agent(
        self,
        controller,
        agent_id: str,
        prompt: str,
    ) -> Dict[str, Any]:
        """Query a single agent."""
        try:
            # Use agent_controller's call method
            response = await controller.call_agent(agent_id, prompt, timeout=120)

            # Try to parse JSON response
            if isinstance(response, str):
                try:
                    return json.loads(response)
                except json.JSONDecodeError:
                    return {"raw_response": response}
            return response

        except Exception as e:
            return {"error": str(e)}

    async def _fallback_agent_analysis(self) -> Dict[str, Any]:
        """Fallback analysis when agents aren't available."""
        logger.info("Using fallback analysis (no live agents)")

        # Use direct MCP tools instead
        results = {}

        try:
            # Use the chat service for analysis
            from app.services.chat import generate_response

            for agent_id, config in self.AGENT_ROLES.items():
                try:
                    # Route to appropriate model based on agent type
                    model = {
                        "claude-mcp": "gemini/gemini-2.5-flash",
                        "codex-mcp": "mistral/codestral-latest",
                        "gemini-mcp": "gemini/gemini-2.5-pro",
                        "opencode-mcp": "gemini/gemini-2.0-flash",
                    }.get(agent_id, "gemini/gemini-2.5-flash")

                    response = await generate_response(
                        model=model,
                        prompt=config["prompt"],
                        system="You are an expert code analyst. Respond only in valid JSON.",
                        temperature=0.3,
                    )

                    if response:
                        try:
                            results[agent_id] = json.loads(response)
                        except json.JSONDecodeError:
                            results[agent_id] = {"raw_response": response[:500]}

                except Exception as e:
                    results[agent_id] = {"error": str(e)}

        except ImportError:
            logger.warning("Chat service not available")

        return results

    async def _analyze_codebase(self) -> Dict[str, Any]:
        """Analyze codebase structure and patterns."""
        findings = {
            "stats": {},
            "patterns": [],
            "structure": {},
        }

        app_path = self.backend_path / "app"

        # Count files by type
        py_files = list(app_path.rglob("*.py"))
        findings["stats"]["python_files"] = len(py_files)
        findings["stats"]["total_lines"] = sum(
            len(f.read_text(errors="ignore").splitlines())
            for f in py_files if f.is_file()
        )

        # Search for patterns
        for pattern, description in self.SEARCH_PATTERNS:
            matches = []
            for py_file in py_files:
                try:
                    content = py_file.read_text(errors="ignore")
                    for i, line in enumerate(content.splitlines(), 1):
                        import re
                        if re.search(pattern, line, re.IGNORECASE):
                            matches.append({
                                "file": str(py_file.relative_to(self.backend_path)),
                                "line": i,
                                "content": line.strip()[:100],
                            })
                except Exception:
                    pass

            if matches:
                findings["patterns"].append({
                    "pattern": pattern,
                    "description": description,
                    "count": len(matches),
                    "matches": matches[:10],  # Limit to 10
                })

        # Basic structure
        findings["structure"] = {
            "routes": len(list((app_path / "routes").glob("*.py"))) if (app_path / "routes").exists() else 0,
            "services": len(list((app_path / "services").glob("*.py"))) if (app_path / "services").exists() else 0,
            "models": len(list((app_path / "models").glob("*.py"))) if (app_path / "models").exists() else 0,
        }

        return findings

    async def _load_memory_context(self) -> List[Dict[str, Any]]:
        """Load relevant memory entries for context."""
        context = []

        memory_controller = await self._get_memory_controller()
        if not memory_controller:
            return context

        try:
            # Search for relevant memories
            for query in ["improvement", "bug", "architecture", "performance"]:
                results = await memory_controller.search(
                    query=query,
                    memory_type="decision",
                    limit=5,
                )
                context.extend(results.get("entries", []))
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")

        return context[:20]  # Limit total

    async def _web_research(self) -> List[Dict[str, Any]]:
        """Perform web research for best practices."""
        research = []

        queries = [
            "FastAPI best practices 2024",
            "Python asyncio performance optimization",
            "MCP Model Context Protocol patterns",
        ]

        try:
            from app.services.web_search import web_search

            for query in queries:
                try:
                    results = await web_search(query, max_results=3)
                    for r in results:
                        research.append({
                            "query": query,
                            "title": r.get("title"),
                            "url": r.get("url"),
                            "snippet": r.get("snippet", "")[:200],
                        })
                except Exception as e:
                    logger.warning(f"Web search failed for '{query}': {e}")

        except ImportError:
            logger.warning("Web search service not available")

        return research

    def _consolidate_findings(
        self,
        agent_results: Dict[str, Any],
        codebase_findings: Dict[str, Any],
        focus_areas: Optional[List[str]],
    ) -> List[EvolutionFinding]:
        """Consolidate all findings into a unified list."""
        findings = []

        # Convert agent findings
        for agent_id, result in agent_results.items():
            if "error" in result:
                continue

            agent_findings = result.get("findings", [])
            for f in agent_findings:
                try:
                    finding = EvolutionFinding(
                        category=self._map_category(f.get("area", agent_id)),
                        severity=self._map_severity(f.get("severity", f.get("priority", "medium"))),
                        file=f.get("file", "unknown"),
                        line=f.get("line"),
                        current=f.get("issue", f.get("current", "")),
                        suggested=f.get("suggestion", f.get("fix", f.get("optimization", ""))),
                        impact=f.get("impact", ""),
                        effort=f.get("effort", "unknown"),
                        consensus=0.25,  # One agent vote
                        agent_votes={agent_id: True},
                    )
                    findings.append(finding)
                except Exception:
                    pass

        # Add codebase pattern findings
        for pattern_result in codebase_findings.get("patterns", []):
            for match in pattern_result.get("matches", [])[:5]:
                finding = EvolutionFinding(
                    category=FindingCategory.QUALITY,
                    severity=FindingSeverity.LOW,
                    file=match.get("file", ""),
                    line=match.get("line"),
                    current=match.get("content", ""),
                    suggested=f"Review: {pattern_result.get('description', '')}",
                    impact="Code quality",
                    effort="low",
                )
                findings.append(finding)

        # Sort by severity
        severity_order = {
            FindingSeverity.CRITICAL: 0,
            FindingSeverity.HIGH: 1,
            FindingSeverity.MEDIUM: 2,
            FindingSeverity.LOW: 3,
        }
        findings.sort(key=lambda f: severity_order.get(f.severity, 99))

        return findings

    def _map_category(self, area: str) -> FindingCategory:
        """Map area string to FindingCategory."""
        area_lower = area.lower()
        if "security" in area_lower:
            return FindingCategory.SECURITY
        elif "performance" in area_lower or "optim" in area_lower:
            return FindingCategory.PERFORMANCE
        elif "arch" in area_lower:
            return FindingCategory.ARCHITECTURE
        elif "scal" in area_lower:
            return FindingCategory.SCALABILITY
        return FindingCategory.QUALITY

    def _map_severity(self, severity: Any) -> FindingSeverity:
        """Map severity value to FindingSeverity."""
        if isinstance(severity, int):
            if severity <= 1:
                return FindingSeverity.CRITICAL
            elif severity <= 3:
                return FindingSeverity.HIGH
            elif severity <= 5:
                return FindingSeverity.MEDIUM
            return FindingSeverity.LOW

        sev_lower = str(severity).lower()
        if "critical" in sev_lower:
            return FindingSeverity.CRITICAL
        elif "high" in sev_lower:
            return FindingSeverity.HIGH
        elif "medium" in sev_lower or "mid" in sev_lower:
            return FindingSeverity.MEDIUM
        return FindingSeverity.LOW

    def _generate_actions(
        self,
        findings: List[EvolutionFinding],
        mode: EvolutionMode,
    ) -> List[str]:
        """Generate next action items based on findings."""
        actions = []

        # Group by severity
        critical = [f for f in findings if f.severity == FindingSeverity.CRITICAL]
        high = [f for f in findings if f.severity == FindingSeverity.HIGH]

        if critical:
            actions.append(f"URGENT: Address {len(critical)} critical findings")
        if high:
            actions.append(f"HIGH: Review {len(high)} high-priority findings")

        if mode in [EvolutionMode.SUGGEST, EvolutionMode.IMPLEMENT, EvolutionMode.FULL_AUTO]:
            for f in findings[:5]:
                actions.append(f"Fix {f.file}:{f.line or '?'} - {f.current[:50]}")

        if mode == EvolutionMode.IMPLEMENT:
            actions.append("Create feature branch: feature/auto-evolve-xxx")
            actions.append("Run test suite after changes")

        if mode == EvolutionMode.FULL_AUTO:
            actions.append("Generate PR with all approved changes")
            actions.append("Request human review")

        return actions

    async def _store_evolution_result(self, result: EvolutionResult) -> bool:
        """Store evolution result in TriStar memory."""
        memory_controller = await self._get_memory_controller()
        if not memory_controller:
            return False

        try:
            summary = {
                "evolution_id": result.evolution_id,
                "mode": result.mode.value,
                "findings_count": len(result.findings),
                "critical_count": len([f for f in result.findings if f.severity == FindingSeverity.CRITICAL]),
                "agents": result.agents_consulted,
                "timestamp": result.completed_at,
            }

            await memory_controller.store(
                content=json.dumps(summary),
                memory_type="summary",
                tags=["auto-evolve", "analysis", result.mode.value],
                confidence=0.85,
            )
            return True

        except Exception as e:
            logger.warning(f"Failed to store evolution result: {e}")
            return False

    async def _log_evolution(self, result: EvolutionResult) -> None:
        """Log evolution result to file."""
        log_file = self.evolution_log / f"{result.evolution_id}.json"
        try:
            log_file.write_text(json.dumps(result.to_dict(), indent=2))
            logger.info(f"Evolution logged to {log_file}")
        except Exception as e:
            logger.warning(f"Failed to log evolution: {e}")

    async def get_evolution_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent evolution history."""
        history = []

        log_files = sorted(
            self.evolution_log.glob("evo_*.json"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )

        for log_file in log_files[:limit]:
            try:
                data = json.loads(log_file.read_text())
                history.append({
                    "evolution_id": data.get("evolution_id"),
                    "mode": data.get("mode"),
                    "started_at": data.get("started_at"),
                    "findings_count": len(data.get("findings", [])),
                })
            except Exception:
                pass

        return history


# Convenience function for direct usage
async def evolve(
    mode: str = "analyze",
    focus: Optional[List[str]] = None,
) -> EvolutionResult:
    """
    Run auto-evolution with specified mode.

    Args:
        mode: One of "analyze", "suggest", "implement", "full_auto"
        focus: Optional list of focus areas

    Returns:
        EvolutionResult with findings and recommendations
    """
    service = AutoEvolveService()
    return await service.run_evolution(
        mode=EvolutionMode(mode),
        focus_areas=focus,
    )
