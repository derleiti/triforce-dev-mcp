"""
Model Init Service v2.80
Initialisierung und Impfung von LLM-Modellen im TriStar Mesh

Basiert auf Empfehlungen von:
- Kimi (Init-Architektur, Gossip-Protokoll)
- Gemini (Koordinationsstruktur)
- Cogito (Prompt-Konsistenz)

Features:
- Model Registry mit Capabilities
- System-Prompt-Injection
- Role-based Configuration
- Health Monitoring
- Gossip-basierte Discovery
"""

import asyncio
import json
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set, Any
from pathlib import Path
from enum import Enum
import aiofiles
import logging

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.tristar.model_init")


# ============================================================================
# Enums & Constants
# ============================================================================

class ModelRole(str, Enum):
    """Rollen im TriStar Mesh"""
    ADMIN = "admin"      # Vollzugriff, System-Verwaltung
    LEAD = "lead"        # Koordination, Planung, Delegation
    WORKER = "worker"    # Ausführung, Spezialisierung
    REVIEWER = "reviewer"  # Qualitätskontrolle, Validierung


class ModelCapability(str, Enum):
    """Fähigkeiten der Modelle"""
    CODE = "code"
    MATH = "math"
    CREATIVE = "creative"
    ANALYSIS = "analysis"
    MULTILINGUAL = "multilingual"
    VISION = "vision"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"
    SECURITY = "security"
    TRANSLATION = "translation"
    DOCUMENTATION = "documentation"
    TOOL_USE = "tool_use"


# Default System Prompts (basierend auf Kimi/Cogito Empfehlungen)
DEFAULT_SYSTEM_PROMPTS = {
    ModelRole.LEAD: """Du bist {model_id}, ein Lead-Agent im TriStar Multi-LLM System.

DEINE ROLLE:
- Analysiere eingehende Aufgaben und erstelle Ausführungspläne
- Delegiere Teilaufgaben an spezialisierte Worker-Agenten
- Arbitriere Konflikte in maximal 2 Runden
- Halte die letzten 8 globalen Meta-Nachrichten im Kontext

VERFÜGBARE AKTIONEN: ASSIGN, REJECT, MERGE, ESCALATE

ANTWORTFORMAT:
=== RESPONSE ===
STATUS: [success|error|pending]
PLAN: [Strukturierter Ausführungsplan]
DELEGATIONS: [Liste der Delegationen an Worker]
SUMMARY: [Kurze Zusammenfassung]
=== END RESPONSE ===

Antworte immer strukturiert und präzise.""",

    ModelRole.WORKER: """Du bist {model_id}, ein Worker-Agent im TriStar Multi-LLM System.

DEINE ROLLE:
- Führe spezialisierte Aufgaben aus (Cluster: {capabilities})
- Akzeptiere nur Aufgaben von Sponsor oder Lead
- Liefere Ergebnisse mit Konfidenz-Score

ANTWORTFORMAT:
=== RESPONSE ===
STATUS: [success|error|pending]
CERTAINTY: [0.0-1.0]
RESULT: [Dein Ergebnis]
FALLBACK_HINT: [Optional: Alternative bei niedriger Konfidenz]
=== END RESPONSE ===

Bei Konfidenz < 0.6: Leite an Fallback weiter.""",

    ModelRole.REVIEWER: """Du bist {model_id}, ein Reviewer-Agent im TriStar Multi-LLM System.

DEINE ROLLE:
- Überprüfe Antworten mit Konfidenz < 0.85
- Validiere Code auf Sicherheit und Best Practices
- Prüfe logische Konsistenz

ANTWORTFORMAT:
=== REVIEW ===
STATUS: [PASS|REWORK|HALT]
DELTA_TOKENS: [Geschätzte Token-Änderung]
ISSUES: [Gefundene Probleme]
SUGGESTIONS: [Verbesserungsvorschläge]
=== END REVIEW ===

Max. Review-Queue: 3 Einträge.""",

    ModelRole.ADMIN: """Du bist {model_id}, ein Admin-Agent im TriStar Multi-LLM System.

DEINE ROLLE:
- Vollzugriff auf alle System-Funktionen
- Verwaltung von Konfigurationen und Berechtigungen
- Überwachung und Fehlerbehebung
- Dokumentation und Reporting

VERFÜGBARE TOOLS:
- Memory Controller (Lesen, Schreiben, Löschen)
- RBAC-Verwaltung
- Audit-Logs
- Health Monitoring

Antworte immer professionell und dokumentiere alle Aktionen.""",
}


# ============================================================================
# Data Structures
# ============================================================================

@dataclass
class ModelConfig:
    """Konfiguration für ein LLM-Modell"""
    model_id: str
    model_name: str
    provider: str  # ollama, gemini, mistral, anthropic, etc.
    role: ModelRole
    capabilities: Set[ModelCapability] = field(default_factory=set)

    # System Prompt
    system_prompt: str = ""
    system_prompt_crc: str = ""

    # Network
    neighbor_ids: List[str] = field(default_factory=list)  # 8 nearest neighbors
    trust_score: float = 0.5
    consensus_key: str = ""

    # Rate Limits
    tokens_per_30s: int = 4000
    max_parallel_requests: int = 5

    # Status
    initialized: bool = False
    healthy: bool = True
    last_heartbeat: Optional[datetime] = None

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_name": self.model_name,
            "provider": self.provider,
            "role": self.role.value,
            "capabilities": [c.value for c in self.capabilities],
            "system_prompt": self.system_prompt[:200] + "..." if len(self.system_prompt) > 200 else self.system_prompt,
            "system_prompt_crc": self.system_prompt_crc,
            "neighbor_ids": self.neighbor_ids,
            "trust_score": self.trust_score,
            "tokens_per_30s": self.tokens_per_30s,
            "max_parallel_requests": self.max_parallel_requests,
            "initialized": self.initialized,
            "healthy": self.healthy,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ModelConfig":
        return cls(
            model_id=data["model_id"],
            model_name=data["model_name"],
            provider=data["provider"],
            role=ModelRole(data["role"]),
            capabilities={ModelCapability(c) for c in data.get("capabilities", [])},
            system_prompt=data.get("system_prompt", ""),
            system_prompt_crc=data.get("system_prompt_crc", ""),
            neighbor_ids=data.get("neighbor_ids", []),
            trust_score=data.get("trust_score", 0.5),
            tokens_per_30s=data.get("tokens_per_30s", 4000),
            max_parallel_requests=data.get("max_parallel_requests", 5),
            initialized=data.get("initialized", False),
            healthy=data.get("healthy", True),
            last_heartbeat=datetime.fromisoformat(data["last_heartbeat"]) if data.get("last_heartbeat") else None,
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
        )


# ============================================================================
# Model Registry
# ============================================================================

# Vordefinierte Modell-Konfigurationen (120 Modelle)
PREDEFINED_MODELS: List[Dict[str, Any]] = [
    # === LEADS (Koordinatoren) ===
    {"model_id": "gemini-lead", "model_name": "gemini/gemini-2.5-flash", "provider": "gemini", "role": "lead",
     "capabilities": ["analysis", "reasoning", "long_context", "tool_use"]},
    {"model_id": "kimi-lead", "model_name": "kimi-k2:1t-cloud", "provider": "ollama", "role": "lead",
     "capabilities": ["long_context", "analysis", "reasoning"]},
    {"model_id": "gemini-pro-lead", "model_name": "gemini/gemini-2.5-pro", "provider": "gemini", "role": "lead",
     "capabilities": ["analysis", "reasoning", "code", "long_context"]},

    # === WORKERS (Spezialisten) ===
    # Coding
    {"model_id": "claude-worker", "model_name": "anthropic/claude-sonnet-4", "provider": "anthropic", "role": "worker",
     "capabilities": ["code", "analysis", "documentation"]},
    {"model_id": "deepseek-worker", "model_name": "deepseek-v3.1:671b-cloud", "provider": "ollama", "role": "worker",
     "capabilities": ["code", "math", "reasoning"]},
    {"model_id": "qwen-coder", "model_name": "qwen3-coder:32b-cloud", "provider": "ollama", "role": "worker",
     "capabilities": ["code", "tool_use"]},
    {"model_id": "codestral-worker", "model_name": "mistral/codestral-latest", "provider": "mistral", "role": "worker",
     "capabilities": ["code", "documentation"]},

    # Multilingual
    {"model_id": "qwen-worker", "model_name": "qwen3-vl:235b-cloud", "provider": "ollama", "role": "worker",
     "capabilities": ["multilingual", "vision", "translation"]},
    {"model_id": "nova-worker", "model_name": "gpt-oss:20b-cloud", "provider": "ollama", "role": "worker",
     "capabilities": ["multilingual", "documentation", "creative"]},

    # Math & Reasoning
    {"model_id": "deepseek-math", "model_name": "deepseek-v3.1:671b-cloud", "provider": "ollama", "role": "worker",
     "capabilities": ["math", "reasoning"]},
    {"model_id": "qwen-math", "model_name": "qwen3-math:72b", "provider": "ollama", "role": "worker",
     "capabilities": ["math", "reasoning", "code"]},

    # Creative
    {"model_id": "gemini-creative", "model_name": "gemini/gemini-2.0-flash", "provider": "gemini", "role": "worker",
     "capabilities": ["creative", "multilingual", "analysis"]},

    # Vision
    {"model_id": "gemini-vision", "model_name": "gemini/gemini-2.0-flash", "provider": "gemini", "role": "worker",
     "capabilities": ["vision", "analysis"]},
    {"model_id": "claude-vision", "model_name": "anthropic/claude-sonnet-4", "provider": "anthropic", "role": "worker",
     "capabilities": ["vision", "analysis", "code"]},

    # === REVIEWERS (Qualitätskontrolle) ===
    {"model_id": "mistral-reviewer", "model_name": "mistral/mistral-medium-latest", "provider": "mistral", "role": "reviewer",
     "capabilities": ["security", "code", "analysis"]},
    {"model_id": "cogito-reviewer", "model_name": "cogito-2.1:671b-cloud", "provider": "ollama", "role": "reviewer",
     "capabilities": ["reasoning", "analysis"]},
    {"model_id": "mistral-large-reviewer", "model_name": "mistral/mistral-large-latest", "provider": "mistral", "role": "reviewer",
     "capabilities": ["security", "code", "reasoning"]},

    # === ADMINS ===
    {"model_id": "nova-admin", "model_name": "gpt-oss:20b-cloud", "provider": "ollama", "role": "admin",
     "capabilities": ["documentation", "multilingual", "tool_use"]},
    {"model_id": "system-admin", "model_name": "gemini/gemini-2.5-flash", "provider": "gemini", "role": "admin",
     "capabilities": ["tool_use", "analysis", "reasoning"]},
]

# Erweitere auf 120 Modelle durch Varianten
def _generate_model_variants() -> List[Dict[str, Any]]:
    """Generiert zusätzliche Modellvarianten für 120 Modelle"""
    variants = PREDEFINED_MODELS.copy()

    # Ollama Modelle - Local/Self-hosted
    ollama_models = [
        # Llama Familie
        ("llama3.2:3b", ["code", "analysis"]),
        ("llama3.2:8b", ["code", "analysis", "reasoning"]),
        ("llama3.3:70b", ["code", "analysis", "reasoning", "long_context"]),
        ("llama3.1:8b", ["code", "analysis"]),
        ("llama3.1:70b", ["code", "analysis", "reasoning"]),
        ("llama3.1:405b", ["code", "analysis", "reasoning", "long_context"]),
        ("codellama:7b", ["code"]),
        ("codellama:13b", ["code"]),
        ("codellama:34b", ["code", "reasoning"]),
        ("codellama:70b", ["code", "reasoning", "long_context"]),

        # Mistral Familie
        ("mistral:7b", ["code", "multilingual"]),
        ("mistral:7b-instruct", ["code", "multilingual", "analysis"]),
        ("mistral-nemo:12b", ["code", "multilingual", "reasoning"]),
        ("mixtral:8x7b", ["code", "multilingual", "reasoning"]),
        ("mixtral:8x22b", ["code", "multilingual", "reasoning", "long_context"]),

        # Google Gemma
        ("gemma:2b", ["code", "multilingual"]),
        ("gemma:7b", ["code", "multilingual"]),
        ("gemma2:2b", ["code", "multilingual"]),
        ("gemma2:9b", ["code", "multilingual", "analysis"]),
        ("gemma2:27b", ["code", "multilingual", "analysis", "reasoning"]),

        # Microsoft Phi
        ("phi:2.7b", ["code", "reasoning"]),
        ("phi3:3.8b", ["code", "reasoning", "math"]),
        ("phi3:14b", ["code", "reasoning", "math"]),
        ("phi4:14b", ["code", "reasoning", "math", "analysis"]),

        # Alibaba Qwen
        ("qwen:7b", ["code", "multilingual"]),
        ("qwen:14b", ["code", "multilingual", "math"]),
        ("qwen:32b", ["code", "multilingual", "math", "reasoning"]),
        ("qwen:72b", ["code", "multilingual", "math", "reasoning", "long_context"]),
        ("qwen:110b", ["code", "multilingual", "math", "reasoning", "long_context"]),
        ("qwen2:7b", ["code", "multilingual"]),
        ("qwen2:72b", ["code", "multilingual", "math", "reasoning"]),
        ("qwen2.5:7b", ["code", "multilingual"]),
        ("qwen2.5:14b", ["code", "multilingual", "math"]),
        ("qwen2.5:32b", ["code", "multilingual", "math", "reasoning"]),
        ("qwen2.5:72b", ["code", "multilingual", "math", "reasoning", "long_context"]),
        ("qwen2.5-coder:7b", ["code"]),
        ("qwen2.5-coder:14b", ["code", "reasoning"]),
        ("qwen2.5-coder:32b", ["code", "reasoning", "analysis"]),

        # DeepSeek
        ("deepseek-v2:16b", ["code", "reasoning"]),
        ("deepseek-v2:236b", ["code", "reasoning", "long_context"]),
        ("deepseek-v2.5:236b", ["code", "reasoning", "math", "long_context"]),
        ("deepseek-coder:6.7b", ["code"]),
        ("deepseek-coder:33b", ["code", "reasoning"]),
        ("deepseek-coder-v2:16b", ["code"]),
        ("deepseek-coder-v2:236b", ["code", "reasoning"]),

        # StarCoder
        ("starcoder:3b", ["code"]),
        ("starcoder:7b", ["code"]),
        ("starcoder:15b", ["code"]),
        ("starcoder2:3b", ["code"]),
        ("starcoder2:7b", ["code"]),
        ("starcoder2:15b", ["code", "reasoning"]),

        # Yi (01.AI)
        ("yi:6b", ["multilingual"]),
        ("yi:9b", ["multilingual", "reasoning"]),
        ("yi:34b", ["multilingual", "reasoning"]),

        # Other Models
        ("solar:10.7b", ["multilingual", "analysis"]),
        ("neural-chat:7b", ["multilingual", "creative"]),
        ("orca-mini:3b", ["analysis"]),
        ("orca-mini:7b", ["analysis", "reasoning"]),
        ("wizard-vicuna:13b", ["creative", "reasoning"]),
        ("nous-hermes:13b", ["creative", "reasoning"]),
        ("dolphin-phi:2.7b", ["code", "reasoning"]),
        ("dolphin-mistral:7b", ["code", "multilingual"]),
        ("openhermes:7b", ["creative", "analysis"]),
        ("tinyllama:1.1b", ["analysis"]),
        ("stable-code:3b", ["code"]),
        ("magicoder:7b", ["code"]),
        ("zephyr:7b", ["multilingual", "creative"]),
        ("vicuna:7b", ["creative"]),
        ("vicuna:13b", ["creative", "reasoning"]),
    ]

    for model, caps in ollama_models:
        variants.append({
            "model_id": f"ollama-{model.replace(':', '-').replace('.', '')}",
            "model_name": model,
            "provider": "ollama",
            "role": "worker",
            "capabilities": caps,
        })

    # Cloud Modelle - API-basiert
    cloud_models = [
        # Gemini
        ("gemini/gemini-1.0-pro", "gemini", ["analysis", "multilingual"]),
        ("gemini/gemini-1.5-flash", "gemini", ["analysis", "multilingual", "vision"]),
        ("gemini/gemini-1.5-flash-8b", "gemini", ["analysis", "multilingual"]),
        ("gemini/gemini-1.5-pro", "gemini", ["analysis", "reasoning", "long_context", "vision"]),
        ("gemini/gemini-2.0-flash-thinking", "gemini", ["reasoning", "analysis", "math"]),
        ("gemini/gemini-exp-1206", "gemini", ["reasoning", "analysis", "code"]),

        # Mistral
        ("mistral/mistral-tiny", "mistral", ["code", "multilingual"]),
        ("mistral/mistral-small-latest", "mistral", ["code", "multilingual"]),
        ("mistral/mistral-medium-latest", "mistral", ["code", "multilingual", "reasoning"]),
        ("mistral/open-mistral-7b", "mistral", ["code", "multilingual"]),
        ("mistral/open-mixtral-8x7b", "mistral", ["code", "multilingual", "reasoning"]),
        ("mistral/open-mixtral-8x22b", "mistral", ["code", "multilingual", "reasoning", "long_context"]),
        ("mistral/pixtral-12b-2409", "mistral", ["vision", "analysis"]),
        ("mistral/pixtral-large-latest", "mistral", ["vision", "analysis", "reasoning"]),
        ("mistral/ministral-3b-latest", "mistral", ["code"]),
        ("mistral/ministral-8b-latest", "mistral", ["code", "multilingual"]),

        # Anthropic Claude
        ("anthropic/claude-3-haiku", "anthropic", ["code", "analysis"]),
        ("anthropic/claude-3-sonnet", "anthropic", ["code", "analysis", "reasoning"]),
        ("anthropic/claude-3-opus", "anthropic", ["code", "analysis", "reasoning", "long_context"]),
        ("anthropic/claude-3.5-haiku", "anthropic", ["code", "analysis"]),
        ("anthropic/claude-3.5-sonnet", "anthropic", ["code", "analysis", "reasoning"]),
        ("anthropic/claude-3.5-opus", "anthropic", ["code", "analysis", "reasoning", "long_context"]),

        # GPT-OSS
        ("gpt-oss:7b-cloud", "gpt-oss", ["multilingual", "creative"]),
        ("gpt-oss:13b-cloud", "gpt-oss", ["multilingual", "creative", "analysis"]),
        ("gpt-oss:30b-cloud", "gpt-oss", ["multilingual", "creative", "analysis", "reasoning"]),
    ]

    for model, provider, caps in cloud_models:
        variants.append({
            "model_id": f"{provider}-{model.split('/')[-1].replace('.', '').replace('-', '_')}",
            "model_name": model,
            "provider": provider,
            "role": "worker",
            "capabilities": caps,
        })

    return variants[:120]  # Maximal 120 Modelle


# ============================================================================
# Model Init Service
# ============================================================================

class ModelInitService:
    """
    Service für die Initialisierung und Verwaltung von LLM-Modellen.

    Funktionen:
    - Model Registry Management
    - System-Prompt-Injection
    - Neighbor Discovery (Gossip)
    - Health Monitoring
    """

    def __init__(self, data_dir: str | Path = TRISTAR_DIR / "models"):
        self.data_dir = Path(data_dir)
        self.models: Dict[str, ModelConfig] = {}
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self):
        """Initialisiert den Service"""
        if self._initialized:
            return

        self.data_dir.mkdir(parents=True, exist_ok=True)
        await self._load_models()

        # Registriere vordefinierte Modelle
        if not self.models:
            await self._register_predefined_models()

        self._initialized = True
        logger.info(f"ModelInitService initialized with {len(self.models)} models")

    async def _ensure_initialized(self):
        """Stellt sicher, dass der Service initialisiert ist"""
        if not self._initialized:
            await self.initialize()

    async def _register_predefined_models(self):
        """Registriert die vordefinierten Modelle"""
        models = _generate_model_variants()

        for model_data in models:
            config = ModelConfig(
                model_id=model_data["model_id"],
                model_name=model_data["model_name"],
                provider=model_data["provider"],
                role=ModelRole(model_data["role"]),
                capabilities={ModelCapability(c) for c in model_data["capabilities"]},
            )
            await self.register_model(config, persist=False)

        await self._persist_models()

    async def register_model(
        self,
        config: ModelConfig,
        persist: bool = True,
    ) -> ModelConfig:
        """Registriert ein neues Modell"""
        async with self._lock:
            # Generate system prompt
            if not config.system_prompt:
                config.system_prompt = self._generate_system_prompt(config)

            # Calculate CRC
            config.system_prompt_crc = format(zlib.crc32(config.system_prompt.encode()) & 0xffffffff, '08x')

            # Generate consensus key
            config.consensus_key = uuid.uuid4().hex[:32]

            # Assign neighbors (wird später durch Gossip aktualisiert)
            config.neighbor_ids = self._assign_initial_neighbors(config)

            self.models[config.model_id] = config

            if persist:
                await self._persist_models()

            logger.info(f"Registered model {config.model_id} with role {config.role.value}")
            return config

    def _generate_system_prompt(self, config: ModelConfig) -> str:
        """Generiert den System-Prompt für ein Modell"""
        template = DEFAULT_SYSTEM_PROMPTS.get(config.role, DEFAULT_SYSTEM_PROMPTS[ModelRole.WORKER])

        return template.format(
            model_id=config.model_id,
            capabilities=", ".join(c.value for c in config.capabilities),
        )

    def _assign_initial_neighbors(self, config: ModelConfig) -> List[str]:
        """Weist initiale Nachbarn zu (basierend auf Capabilities)"""
        # Finde Modelle mit ähnlichen Capabilities
        neighbors = []
        for model_id, model in self.models.items():
            if model_id == config.model_id:
                continue
            overlap = len(config.capabilities & model.capabilities)
            if overlap > 0:
                neighbors.append((model_id, overlap))

        # Sortiere nach Überlappung und nimm die Top 8
        neighbors.sort(key=lambda x: x[1], reverse=True)
        return [n[0] for n in neighbors[:8]]

    async def init_model(self, model_id: str) -> Dict[str, Any]:
        """
        Initialisiert ein Modell (Model-Impfung).

        Gibt die Init-Daten zurück, die an das Modell gesendet werden.
        """
        await self._ensure_initialized()
        config = self.models.get(model_id)
        if not config:
            raise ValueError(f"Model not found: {model_id}")

        # Aktualisiere Nachbarn
        config.neighbor_ids = self._assign_initial_neighbors(config)
        config.initialized = True
        config.last_heartbeat = datetime.now(timezone.utc)
        config.updated_at = datetime.now(timezone.utc)

        await self._persist_models()

        # Init-Payload (basierend auf Kimis Empfehlungen)
        return {
            "model_id": config.model_id,
            "role": config.role.value,
            "capabilities": [c.value for c in config.capabilities],
            "neighbor_ids": config.neighbor_ids,
            "trust_score": config.trust_score,
            "consensus_key": config.consensus_key,
            "system_prompt": config.system_prompt,
            "system_prompt_crc": config.system_prompt_crc,
            "rate_limits": {
                "tokens_per_30s": config.tokens_per_30s,
                "max_parallel_requests": config.max_parallel_requests,
            },
            "initialized_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_model(self, model_id: str) -> Optional[ModelConfig]:
        """Gibt ein Modell zurück"""
        await self._ensure_initialized()
        return self.models.get(model_id)

    async def list_models(
        self,
        role: Optional[ModelRole] = None,
        capability: Optional[ModelCapability] = None,
        provider: Optional[str] = None,
        initialized_only: bool = False,
    ) -> List[ModelConfig]:
        """Listet Modelle mit optionalen Filtern"""
        await self._ensure_initialized()
        results = list(self.models.values())

        if role:
            results = [m for m in results if m.role == role]
        if capability:
            results = [m for m in results if capability in m.capabilities]
        if provider:
            results = [m for m in results if m.provider == provider]
        if initialized_only:
            results = [m for m in results if m.initialized]

        return results

    async def update_system_prompt(
        self,
        model_id: str,
        new_prompt: str,
    ) -> ModelConfig:
        """Aktualisiert den System-Prompt eines Modells"""
        await self._ensure_initialized()
        config = self.models.get(model_id)
        if not config:
            raise ValueError(f"Model not found: {model_id}")

        config.system_prompt = new_prompt
        config.system_prompt_crc = format(zlib.crc32(new_prompt.encode()) & 0xffffffff, '08x')
        config.updated_at = datetime.now(timezone.utc)

        await self._persist_models()
        return config

    async def heartbeat(self, model_id: str) -> bool:
        """Aktualisiert den Heartbeat eines Modells"""
        await self._ensure_initialized()
        config = self.models.get(model_id)
        if not config:
            return False

        config.last_heartbeat = datetime.now(timezone.utc)
        config.healthy = True
        return True

    async def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken zurück"""
        await self._ensure_initialized()
        by_role = {}
        by_provider = {}
        by_capability = {}
        initialized_count = 0
        healthy_count = 0

        for model in self.models.values():
            # By Role
            role_key = model.role.value
            by_role[role_key] = by_role.get(role_key, 0) + 1

            # By Provider
            by_provider[model.provider] = by_provider.get(model.provider, 0) + 1

            # By Capability
            for cap in model.capabilities:
                by_capability[cap.value] = by_capability.get(cap.value, 0) + 1

            if model.initialized:
                initialized_count += 1
            if model.healthy:
                healthy_count += 1

        return {
            "total_models": len(self.models),
            "initialized": initialized_count,
            "healthy": healthy_count,
            "by_role": by_role,
            "by_provider": by_provider,
            "by_capability": by_capability,
        }

    async def _persist_models(self):
        """Persistiert die Modell-Konfigurationen"""
        file_path = self.data_dir / "models.json"
        data = {model_id: config.to_dict() for model_id, config in self.models.items()}

        async with aiofiles.open(file_path, "w") as f:
            await f.write(json.dumps(data, indent=2))

    async def _load_models(self):
        """Lädt Modell-Konfigurationen"""
        file_path = self.data_dir / "models.json"

        if file_path.exists():
            async with aiofiles.open(file_path, "r") as f:
                content = await f.read()
                data = json.loads(content)

            for model_id, model_data in data.items():
                try:
                    # Reconstruct full system prompt if needed
                    if "system_prompt" not in model_data or len(model_data.get("system_prompt", "")) < 50:
                        role = ModelRole(model_data["role"])
                        caps = [ModelCapability(c) for c in model_data.get("capabilities", [])]
                        model_data["system_prompt"] = DEFAULT_SYSTEM_PROMPTS.get(
                            role, DEFAULT_SYSTEM_PROMPTS[ModelRole.WORKER]
                        ).format(
                            model_id=model_id,
                            capabilities=", ".join(c.value for c in caps),
                        )

                    config = ModelConfig.from_dict(model_data)
                    self.models[model_id] = config
                except Exception as e:
                    logger.warning(f"Failed to load model {model_id}: {e}")

            logger.info(f"Loaded {len(self.models)} models from disk")


# ============================================================================
# Singleton Instance
# ============================================================================

model_init_service = ModelInitService()


async def init_model_service():
    """Initialisiert den Model Init Service"""
    await model_init_service.initialize()
    return model_init_service
