"""
Model Specialists: Expert Routing for Claude Code

Allows Claude to delegate specific tasks to specialized AI models.
Each specialist has defined capabilities, optimal use cases, and performance characteristics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger("ailinux.mcp.specialists")


class SpecialistCapability(str, Enum):
    """Capabilities that specialists can have."""
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    CODE_EXPLANATION = "code_explanation"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    SECURITY_ANALYSIS = "security_analysis"
    DOCUMENTATION = "documentation"
    TRANSLATION = "translation"
    SUMMARIZATION = "summarization"
    CREATIVE_WRITING = "creative_writing"
    TECHNICAL_WRITING = "technical_writing"
    DATA_ANALYSIS = "data_analysis"
    MATH = "math"
    VISION = "vision"
    GERMAN_LANGUAGE = "german_language"
    LONG_CONTEXT = "long_context"
    FAST_RESPONSE = "fast_response"
    REASONING = "reasoning"


class TaskComplexity(str, Enum):
    """Task complexity levels."""
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"
    EXPERT = "expert"


@dataclass
class ModelSpecialist:
    """Represents a specialized AI model with defined capabilities."""

    id: str
    name: str
    provider: str
    description: str
    capabilities: Set[SpecialistCapability]
    optimal_tasks: List[str]
    context_length: int = 8192
    response_speed: str = "medium"  # fast, medium, slow
    cost_tier: str = "medium"  # low, medium, high
    complexity_range: tuple = (TaskComplexity.SIMPLE, TaskComplexity.COMPLEX)
    system_prompt_template: Optional[str] = None
    notes: str = ""

    def matches_task(self, task_description: str, required_capabilities: Set[SpecialistCapability]) -> float:
        """
        Calculate how well this specialist matches a task.

        Returns:
            Match score from 0.0 to 1.0
        """
        # Check capability overlap
        if not required_capabilities:
            return 0.5

        overlap = len(self.capabilities & required_capabilities)
        if overlap == 0:
            return 0.0

        capability_score = overlap / len(required_capabilities)

        # Boost score if task description matches optimal tasks
        task_lower = task_description.lower()
        task_match = 0.0
        for optimal_task in self.optimal_tasks:
            if optimal_task.lower() in task_lower:
                task_match = 0.2
                break

        return min(1.0, capability_score + task_match)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "description": self.description,
            "capabilities": [c.value for c in self.capabilities],
            "optimal_tasks": self.optimal_tasks,
            "context_length": self.context_length,
            "response_speed": self.response_speed,
            "cost_tier": self.cost_tier,
            "notes": self.notes
        }


# =============================================================================
# Predefined Specialists
# =============================================================================

SPECIALISTS: Dict[str, ModelSpecialist] = {
    # Claude Models (Anthropic)
    "claude-opus": ModelSpecialist(
        id="anthropic/claude-opus-4",
        name="Claude Opus 4",
        provider="anthropic",
        description="Most capable Claude model for complex reasoning, analysis, and creative tasks",
        capabilities={
            SpecialistCapability.REASONING,
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_REVIEW,
            SpecialistCapability.ARCHITECTURE,
            SpecialistCapability.SECURITY_ANALYSIS,
            SpecialistCapability.DOCUMENTATION,
            SpecialistCapability.CREATIVE_WRITING,
            SpecialistCapability.TECHNICAL_WRITING,
            SpecialistCapability.VISION,
            SpecialistCapability.LONG_CONTEXT,
        },
        optimal_tasks=[
            "complex architecture design",
            "security audit",
            "in-depth code review",
            "research paper analysis",
            "nuanced creative writing"
        ],
        context_length=200000,
        response_speed="slow",
        cost_tier="high",
        complexity_range=(TaskComplexity.COMPLEX, TaskComplexity.EXPERT),
        notes="Use for tasks requiring deep reasoning. Expensive but most capable."
    ),

    "claude-sonnet": ModelSpecialist(
        id="anthropic/claude-sonnet-4",
        name="Claude Sonnet 4",
        provider="anthropic",
        description="Balanced Claude model with strong coding and analysis capabilities",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_REVIEW,
            SpecialistCapability.CODE_EXPLANATION,
            SpecialistCapability.DEBUGGING,
            SpecialistCapability.DOCUMENTATION,
            SpecialistCapability.TECHNICAL_WRITING,
            SpecialistCapability.VISION,
            SpecialistCapability.REASONING,
        },
        optimal_tasks=[
            "code generation",
            "code review",
            "debugging",
            "technical documentation",
            "API design"
        ],
        context_length=200000,
        response_speed="medium",
        cost_tier="medium",
        complexity_range=(TaskComplexity.MODERATE, TaskComplexity.COMPLEX),
        notes="Best all-around choice for development tasks."
    ),

    "claude-haiku": ModelSpecialist(
        id="mistral/mistral-small-latest",
        name="Mistral Small (Fast)",
        provider="mistral",
        description="Fast model for quick tasks and high throughput (Claude Haiku fallback)",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_EXPLANATION,
            SpecialistCapability.SUMMARIZATION,
            SpecialistCapability.TRANSLATION,
            SpecialistCapability.FAST_RESPONSE,
        },
        optimal_tasks=[
            "quick code snippets",
            "simple explanations",
            "summarization",
            "translation",
            "formatting"
        ],
        context_length=32768,
        response_speed="fast",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.MODERATE),
        notes="Use for quick, simple tasks where speed matters. Fallback for Claude Haiku."
    ),

    # Gemini Models (Google)
    "gemini-flash": ModelSpecialist(
        id="gemini/gemini-2.0-flash",
        name="Gemini 2.0 Flash",
        provider="gemini",
        description="Fast multimodal model with vision and long context",
        capabilities={
            SpecialistCapability.VISION,
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.LONG_CONTEXT,
            SpecialistCapability.FAST_RESPONSE,
            SpecialistCapability.SUMMARIZATION,
            SpecialistCapability.DATA_ANALYSIS,
        },
        optimal_tasks=[
            "image analysis",
            "document processing",
            "code with context",
            "multimodal tasks"
        ],
        context_length=128000,
        response_speed="fast",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.COMPLEX),
        notes="Excellent for vision tasks and large context windows."
    ),

    "gemini-thinking": ModelSpecialist(
        id="mistral/magistral-medium-latest",
        name="Magistral Medium (Reasoning)",
        provider="mistral",
        description="Mistral reasoning model with step-by-step thinking",
        capabilities={
            SpecialistCapability.REASONING,
            SpecialistCapability.MATH,
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.DEBUGGING,
            SpecialistCapability.ARCHITECTURE,
        },
        optimal_tasks=[
            "complex problem solving",
            "mathematical reasoning",
            "algorithm design",
            "logic puzzles"
        ],
        context_length=32768,
        response_speed="medium",
        cost_tier="medium",
        complexity_range=(TaskComplexity.MODERATE, TaskComplexity.EXPERT),
        notes="Use for tasks requiring step-by-step reasoning. Fallback for Gemini Thinking."
    ),

    # Ollama Local Models
    "llama": ModelSpecialist(
        id="ollama/llama3.2",
        name="Llama 3.2",
        provider="ollama",
        description="Fast local model for general tasks",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_EXPLANATION,
            SpecialistCapability.FAST_RESPONSE,
            SpecialistCapability.SUMMARIZATION,
        },
        optimal_tasks=[
            "quick code generation",
            "simple questions",
            "local processing"
        ],
        context_length=8192,
        response_speed="fast",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.MODERATE),
        notes="Runs locally, no API costs. Good for simple tasks."
    ),

    "qwen": ModelSpecialist(
        id="ollama/qwen2.5:14b",
        name="Qwen 2.5 14B",
        provider="ollama",
        description="Strong coding model running locally",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_REVIEW,
            SpecialistCapability.DEBUGGING,
            SpecialistCapability.TECHNICAL_WRITING,
        },
        optimal_tasks=[
            "code generation",
            "code completion",
            "technical tasks"
        ],
        context_length=32768,
        response_speed="medium",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.COMPLEX),
        notes="Excellent coding model. Runs locally."
    ),

    "codellama": ModelSpecialist(
        id="ollama/codellama",
        name="Code Llama",
        provider="ollama",
        description="Specialized model for code generation and understanding",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.CODE_EXPLANATION,
            SpecialistCapability.DEBUGGING,
        },
        optimal_tasks=[
            "code completion",
            "code explanation",
            "function generation"
        ],
        context_length=16384,
        response_speed="fast",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.MODERATE),
        notes="Optimized for code. Runs locally."
    ),

    # Mistral Models
    "mistral-large": ModelSpecialist(
        id="mistral/large",
        name="Mistral Large",
        provider="mistral",
        description="Powerful Mistral model for complex tasks",
        capabilities={
            SpecialistCapability.CODE_GENERATION,
            SpecialistCapability.REASONING,
            SpecialistCapability.TECHNICAL_WRITING,
            SpecialistCapability.TRANSLATION,
        },
        optimal_tasks=[
            "complex coding",
            "technical analysis",
            "multi-language tasks"
        ],
        context_length=32768,
        response_speed="medium",
        cost_tier="medium",
        complexity_range=(TaskComplexity.MODERATE, TaskComplexity.COMPLEX),
        notes="Strong European AI model with good multilingual support."
    ),

    # German Language Specialist
    "gpt-oss": ModelSpecialist(
        id="gpt-oss:20b-cloud",
        name="GPT-OSS 20B",
        provider="gpt-oss",
        description="German-focused open-source model",
        capabilities={
            SpecialistCapability.GERMAN_LANGUAGE,
            SpecialistCapability.TRANSLATION,
            SpecialistCapability.CREATIVE_WRITING,
            SpecialistCapability.SUMMARIZATION,
        },
        optimal_tasks=[
            "German content",
            "German-English translation",
            "German documentation"
        ],
        context_length=8192,
        response_speed="medium",
        cost_tier="low",
        complexity_range=(TaskComplexity.SIMPLE, TaskComplexity.MODERATE),
        notes="Best choice for German language tasks."
    ),
}


class SpecialistRouter:
    """Routes tasks to appropriate specialist models."""

    def __init__(self, specialists: Optional[Dict[str, ModelSpecialist]] = None):
        self.specialists = specialists or SPECIALISTS
        self._capability_keywords = self._build_capability_keywords()

    def _build_capability_keywords(self) -> Dict[str, Set[SpecialistCapability]]:
        """Build keyword to capability mapping for task analysis."""
        return {
            # Code-related
            "code": {SpecialistCapability.CODE_GENERATION},
            "coding": {SpecialistCapability.CODE_GENERATION},
            "program": {SpecialistCapability.CODE_GENERATION},
            "function": {SpecialistCapability.CODE_GENERATION},
            "implement": {SpecialistCapability.CODE_GENERATION},
            "write": {
                SpecialistCapability.CODE_GENERATION,
                SpecialistCapability.TECHNICAL_WRITING,
            },
            "review": {SpecialistCapability.CODE_REVIEW},
            "refactor": {SpecialistCapability.CODE_REVIEW},
            "explain": {SpecialistCapability.CODE_EXPLANATION},
            "debug": {SpecialistCapability.DEBUGGING},
            "fix": {SpecialistCapability.DEBUGGING},
            "bug": {SpecialistCapability.DEBUGGING},
            "error": {SpecialistCapability.DEBUGGING},

            # Architecture & Security
            "architecture": {SpecialistCapability.ARCHITECTURE},
            "design": {SpecialistCapability.ARCHITECTURE},
            "security": {SpecialistCapability.SECURITY_ANALYSIS},
            "vulnerability": {SpecialistCapability.SECURITY_ANALYSIS},
            "audit": {SpecialistCapability.SECURITY_ANALYSIS},

            # Documentation & Writing
            "document": {SpecialistCapability.DOCUMENTATION},
            "readme": {SpecialistCapability.DOCUMENTATION},
            "comment": {SpecialistCapability.DOCUMENTATION},
            "article": {SpecialistCapability.CREATIVE_WRITING},
            "story": {SpecialistCapability.CREATIVE_WRITING},

            # Analysis
            "summarize": {SpecialistCapability.SUMMARIZATION},
            "summary": {SpecialistCapability.SUMMARIZATION},
            "analyze": {SpecialistCapability.DATA_ANALYSIS},
            "data": {SpecialistCapability.DATA_ANALYSIS},

            # Other
            "translate": {SpecialistCapability.TRANSLATION},
            "german": {SpecialistCapability.GERMAN_LANGUAGE},
            "deutsch": {SpecialistCapability.GERMAN_LANGUAGE},
            "image": {SpecialistCapability.VISION},
            "picture": {SpecialistCapability.VISION},
            "photo": {SpecialistCapability.VISION},
            "math": {SpecialistCapability.MATH},
            "calculate": {SpecialistCapability.MATH},
            "reason": {SpecialistCapability.REASONING},
            "think": {SpecialistCapability.REASONING},
            "complex": {SpecialistCapability.REASONING},
            "fast": {SpecialistCapability.FAST_RESPONSE},
            "quick": {SpecialistCapability.FAST_RESPONSE},
            "long": {SpecialistCapability.LONG_CONTEXT},
            "large": {SpecialistCapability.LONG_CONTEXT},
        }

    def analyze_task(self, task_description: str) -> Set[SpecialistCapability]:
        """
        Analyze a task description to determine required capabilities.

        Args:
            task_description: Natural language task description

        Returns:
            Set of required capabilities
        """
        task_lower = task_description.lower()
        capabilities: Set[SpecialistCapability] = set()

        for keyword, caps in self._capability_keywords.items():
            if keyword in task_lower:
                capabilities.update(caps)

        return capabilities

    def estimate_complexity(self, task_description: str) -> TaskComplexity:
        """Estimate task complexity from description."""
        task_lower = task_description.lower()

        expert_keywords = ["complex", "architect", "security audit", "deep analysis", "comprehensive"]
        complex_keywords = ["design", "review", "refactor", "multiple", "integration"]
        moderate_keywords = ["implement", "create", "build", "add", "modify"]

        if any(kw in task_lower for kw in expert_keywords):
            return TaskComplexity.EXPERT
        elif any(kw in task_lower for kw in complex_keywords):
            return TaskComplexity.COMPLEX
        elif any(kw in task_lower for kw in moderate_keywords):
            return TaskComplexity.MODERATE
        else:
            return TaskComplexity.SIMPLE

    def route(
        self,
        task_description: str,
        preferred_speed: Optional[str] = None,
        max_cost_tier: Optional[str] = None,
        required_capabilities: Optional[Set[SpecialistCapability]] = None
    ) -> List[ModelSpecialist]:
        """
        Route a task to appropriate specialists.

        Args:
            task_description: Natural language task description
            preferred_speed: Preferred response speed (fast, medium, slow)
            max_cost_tier: Maximum acceptable cost tier (low, medium, high)
            required_capabilities: Explicitly required capabilities

        Returns:
            List of matching specialists, sorted by match score
        """
        if required_capabilities is None:
            required_capabilities = self.analyze_task(task_description)

        complexity = self.estimate_complexity(task_description)
        cost_order = {"low": 1, "medium": 2, "high": 3}
        speed_order = {"fast": 1, "medium": 2, "slow": 3}

        candidates: List[tuple[float, ModelSpecialist]] = []

        for specialist in self.specialists.values():
            # Check cost constraint
            if max_cost_tier:
                if cost_order.get(specialist.cost_tier, 2) > cost_order.get(max_cost_tier, 3):
                    continue

            # Check complexity range
            min_complexity, max_complexity = specialist.complexity_range
            complexity_order = {
                TaskComplexity.SIMPLE: 1,
                TaskComplexity.MODERATE: 2,
                TaskComplexity.COMPLEX: 3,
                TaskComplexity.EXPERT: 4
            }
            task_complexity = complexity_order.get(complexity, 2)
            if task_complexity < complexity_order.get(min_complexity, 1):
                continue  # Task too simple for this specialist
            if task_complexity > complexity_order.get(max_complexity, 4):
                continue  # Task too complex for this specialist

            # Calculate match score
            score = specialist.matches_task(task_description, required_capabilities)

            # Adjust for speed preference
            if preferred_speed:
                if specialist.response_speed == preferred_speed:
                    score += 0.1
                elif speed_order.get(specialist.response_speed, 2) < speed_order.get(preferred_speed, 2):
                    score += 0.05

            if score > 0:
                candidates.append((score, specialist))

        # Sort by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        return [specialist for _, specialist in candidates]

    def get_best_specialist(
        self,
        task_description: str,
        **kwargs
    ) -> Optional[ModelSpecialist]:
        """Get the single best specialist for a task."""
        specialists = self.route(task_description, **kwargs)
        return specialists[0] if specialists else None

    def get_specialist_for_capability(
        self,
        capability: SpecialistCapability,
        preferred_speed: Optional[str] = None
    ) -> Optional[ModelSpecialist]:
        """Get the best specialist for a specific capability."""
        candidates = [
            s for s in self.specialists.values()
            if capability in s.capabilities
        ]

        if not candidates:
            return None

        if preferred_speed:
            speed_match = [s for s in candidates if s.response_speed == preferred_speed]
            if speed_match:
                return speed_match[0]

        return candidates[0]

    def list_specialists(self) -> List[Dict[str, Any]]:
        """List all available specialists."""
        return [s.to_dict() for s in self.specialists.values()]

    def get_specialist_by_id(self, specialist_id: str) -> Optional[ModelSpecialist]:
        """Get a specialist by model ID."""
        for specialist in self.specialists.values():
            if specialist.id == specialist_id:
                return specialist
        return None

    def recommend_for_workflow(
        self,
        workflow_steps: List[str]
    ) -> Dict[str, ModelSpecialist]:
        """
        Recommend specialists for each step of a workflow.

        Args:
            workflow_steps: List of task descriptions for each step

        Returns:
            Dictionary mapping step descriptions to recommended specialists
        """
        recommendations = {}
        for step in workflow_steps:
            specialist = self.get_best_specialist(step)
            if specialist:
                recommendations[step] = specialist
        return recommendations


# Global router instance
specialist_router = SpecialistRouter()
