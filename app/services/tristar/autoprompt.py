"""
TriStar AutoPrompt Manager v2.80 - YAML-based Prompt Management

Provides hierarchical prompt management:
1. Global AutoPrompt: Default system-wide prompt configuration
2. Project AutoPrompt: Project-specific prompt overrides
3. Ad-hoc Override: Runtime prompt modifications via CLI

Prompt layers are merged in order: global → project → ad-hoc
"""

import yaml
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Any, Optional
from pathlib import Path

from app.paths import TRISTAR_DIR
import logging

logger = logging.getLogger("ailinux.tristar.autoprompt")


@dataclass
class AutoPromptProfile:
    """A single autoprompt profile"""
    name: str
    description: str = ""
    version: str = "1.0"

    # Core prompt components
    system_prompt: str = ""
    task_prefix: str = ""
    task_suffix: str = ""

    # Chain behavior
    max_cycles: int = 10
    aggressive: bool = False
    parallel_agents: int = 4

    # Agent configuration
    lead_model: str = "gemini"
    worker_models: List[str] = field(default_factory=lambda: ["claude", "deepseek", "qwen"])
    reviewer_models: List[str] = field(default_factory=lambda: ["mistral", "cogito"])

    # Output settings
    output_format: str = "markdown"
    include_reasoning: bool = True
    include_sources: bool = True

    # Safety settings
    require_review: bool = False
    auto_commit: bool = False
    sandbox_exec: bool = True

    # Metadata
    tags: List[str] = field(default_factory=list)
    author: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoPromptProfile":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def merge_with(self, other: "AutoPromptProfile") -> "AutoPromptProfile":
        """Merge this profile with another, other takes precedence for non-empty values"""
        merged = AutoPromptProfile(name=other.name or self.name)

        for field_name in self.__dataclass_fields__:
            self_val = getattr(self, field_name)
            other_val = getattr(other, field_name)

            # Use other's value if it's set and not default
            if other_val and other_val != getattr(AutoPromptProfile, field_name, None):
                setattr(merged, field_name, other_val)
            else:
                setattr(merged, field_name, self_val)

        return merged


# Default global autoprompt
DEFAULT_GLOBAL_PROMPT = AutoPromptProfile(
    name="global_default",
    description="Default TriStar global autoprompt",
    version="2.80",
    system_prompt="""Du bist ein Multi-LLM-Koordinator im TriStar System.

Deine Aufgabe ist es, komplexe Aufgaben zu analysieren, in Teilaufgaben zu zerlegen,
und diese an spezialisierte LLMs zu delegieren.

WICHTIGE REGELN:
1. Analysiere die Aufgabe gründlich bevor du delegierst
2. Wähle die richtigen Spezialisten für jede Teilaufgabe
3. Konsolidiere Ergebnisse zu einer kohärenten Antwort
4. Wenn die Aufgabe abgeschlossen ist, beende mit [CHAIN_DONE]
5. Speichere wichtige Erkenntnisse im Memory

VERFÜGBARE SPEZIALISTEN:
- Claude: Coding, Analyse, Dokumentation
- DeepSeek: Schweres Coding, Algorithmen, Optimierung
- Qwen: Multilingual, Vision, General
- Mistral: Review, Security, Schnelle Antworten
- Cogito: Reasoning, Logik, Debugging
- Nova: Deutsch, Dokumentation, Kreativ
- Kimi: Long Context, Research

TOOLS:
@mcp.call(tool_name, {params})""",
    task_prefix="AUFGABE:\n",
    task_suffix="\n\nBitte analysiere diese Aufgabe und erstelle einen Ausführungsplan.",
    lead_model="gemini",
    worker_models=["claude", "deepseek", "qwen"],
    reviewer_models=["mistral", "cogito"],
    output_format="markdown",
    include_reasoning=True,
    include_sources=True,
)


class AutoPromptManager:
    """
    Manages autoprompt profiles with hierarchical merging.

    Directory structure:
    /var/tristar/autoprompts/
    ├── global.yaml           # Global default
    ├── profiles/
    │   ├── coding.yaml       # Coding-focused profile
    │   ├── research.yaml     # Research-focused profile
    │   └── german.yaml       # German language profile
    └── projects/
        └── {project_id}/
            └── autoprompt.yaml
    """

    def __init__(
        self,
        base_dir: str | Path = TRISTAR_DIR / "autoprompts",
    ):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / "profiles").mkdir(exist_ok=True)
        (self.base_dir / "projects").mkdir(exist_ok=True)

        # Initialize with defaults if not present
        self._init_defaults()

        # Cache loaded profiles
        self._cache: Dict[str, AutoPromptProfile] = {}

    def _init_defaults(self):
        """Initialize default autoprompt files if not present"""
        global_file = self.base_dir / "global.yaml"
        if not global_file.exists():
            self._save_profile(DEFAULT_GLOBAL_PROMPT, global_file)
            logger.info("Created default global autoprompt")

        # Create built-in profiles
        profiles = {
            "coding": AutoPromptProfile(
                name="coding",
                description="Coding-focused autoprompt",
                system_prompt=DEFAULT_GLOBAL_PROMPT.system_prompt + """

CODING-MODUS AKTIV:
- Fokus auf Code-Qualität und Best Practices
- Automatische Code-Reviews durch Reviewer-LLMs
- Tests werden automatisch generiert
- Sandbox-Execution für Code-Snippets""",
                worker_models=["claude", "deepseek", "qwen-coder", "codestral"],
                reviewer_models=["mistral", "cogito"],
                require_review=True,
                sandbox_exec=True,
            ),
            "research": AutoPromptProfile(
                name="research",
                description="Research-focused autoprompt",
                system_prompt=DEFAULT_GLOBAL_PROMPT.system_prompt + """

RESEARCH-MODUS AKTIV:
- Fokus auf Quellenqualität und Faktentreue
- Web-Recherche wird automatisch durchgeführt
- Ergebnisse werden mit Quellen belegt
- Memory wird für persistente Erkenntnisse genutzt""",
                worker_models=["kimi", "gemini", "qwen"],
                include_sources=True,
            ),
            "german": AutoPromptProfile(
                name="german",
                description="German language autoprompt",
                system_prompt=DEFAULT_GLOBAL_PROMPT.system_prompt + """

DEUTSCH-MODUS AKTIV:
- Alle Ausgaben auf Deutsch
- Nova als primärer Worker für deutsche Inhalte
- Kulturelle Anpassungen beachten""",
                worker_models=["nova", "claude", "gemini"],
                output_format="markdown",
            ),
        }

        for name, profile in profiles.items():
            profile_file = self.base_dir / "profiles" / f"{name}.yaml"
            if not profile_file.exists():
                self._save_profile(profile, profile_file)
                logger.info(f"Created {name} autoprompt profile")

    def _save_profile(self, profile: AutoPromptProfile, path: Path):
        """Save profile to YAML file"""
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(profile.to_dict(), f, allow_unicode=True, default_flow_style=False)

    def _load_profile(self, path: Path) -> Optional[AutoPromptProfile]:
        """Load profile from YAML file"""
        if not path.exists():
            return None

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                return AutoPromptProfile.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load profile from {path}: {e}")
            return None

    async def get_global_prompt(self) -> AutoPromptProfile:
        """Get the global autoprompt"""
        if "global" in self._cache:
            return self._cache["global"]

        profile = self._load_profile(self.base_dir / "global.yaml")
        if not profile:
            profile = DEFAULT_GLOBAL_PROMPT

        self._cache["global"] = profile
        return profile

    async def get_profile(self, name: str) -> Optional[AutoPromptProfile]:
        """Get a named profile"""
        cache_key = f"profile:{name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        profile = self._load_profile(self.base_dir / "profiles" / f"{name}.yaml")
        if profile:
            self._cache[cache_key] = profile

        return profile

    async def get_project_prompt(self, project_id: str) -> Optional[AutoPromptProfile]:
        """Get project-specific autoprompt"""
        cache_key = f"project:{project_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        project_dir = self.base_dir / "projects" / project_id
        profile = self._load_profile(project_dir / "autoprompt.yaml")
        if profile:
            self._cache[cache_key] = profile

        return profile

    async def set_project_prompt(
        self,
        project_id: str,
        profile: AutoPromptProfile,
    ):
        """Set project-specific autoprompt"""
        project_dir = self.base_dir / "projects" / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        self._save_profile(profile, project_dir / "autoprompt.yaml")

        # Update cache
        self._cache[f"project:{project_id}"] = profile
        logger.info(f"Set autoprompt for project {project_id}")

    async def get_merged_prompt(
        self,
        project_id: Optional[str] = None,
        profile: Optional[str] = None,
        override: Optional[str] = None,
    ) -> AutoPromptProfile:
        """
        Get merged autoprompt applying all layers.

        Merge order: global → profile → project → override
        """
        # Start with global
        result = await self.get_global_prompt()

        # Apply named profile if specified
        if profile:
            named_profile = await self.get_profile(profile)
            if named_profile:
                result = result.merge_with(named_profile)

        # Apply project prompt if specified
        if project_id:
            project_prompt = await self.get_project_prompt(project_id)
            if project_prompt:
                result = result.merge_with(project_prompt)

        # Apply ad-hoc override
        if override:
            override_profile = AutoPromptProfile(
                name="override",
                system_prompt=result.system_prompt + f"\n\nOVERRIDE:\n{override}",
            )
            result = result.merge_with(override_profile)

        return result

    async def list_profiles(self) -> List[Dict[str, Any]]:
        """List all available profiles"""
        profiles = []

        # Global
        global_profile = await self.get_global_prompt()
        profiles.append({
            "name": "global",
            "type": "global",
            "description": global_profile.description,
            "version": global_profile.version,
        })

        # Named profiles
        profiles_dir = self.base_dir / "profiles"
        for path in profiles_dir.glob("*.yaml"):
            profile = self._load_profile(path)
            if profile:
                profiles.append({
                    "name": profile.name,
                    "type": "profile",
                    "description": profile.description,
                    "version": profile.version,
                })

        return profiles

    async def show_prompt(
        self,
        project_id: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Show the merged prompt configuration"""
        merged = await self.get_merged_prompt(project_id=project_id, profile=profile)
        return merged.to_dict()

    def clear_cache(self):
        """Clear the profile cache"""
        self._cache.clear()
        logger.info("AutoPrompt cache cleared")


# Singleton instance
autoprompt_manager = AutoPromptManager()
