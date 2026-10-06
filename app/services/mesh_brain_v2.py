#!/usr/bin/env python3
"""
AILinux Mesh Brain v2.0 - Universal Load Balancer
==================================================

Verteilt Requests über ALLE verfügbaren Provider:
- Ollama Nodes (Hetzner + Backup)
- API Provider (Gemini, Mistral, Groq, OpenRouter, Anthropic, etc.)

TLA+ Verified: MeshGuardianSimple.tla
- Safety: AtLeastOneAvailable ✓
- Liveness: EventualRecovery ✓

Strategien:
- fallback:    Primary → Secondary bei Failure
- round_robin: Rotate durch alle Provider
- fastest:     Schnellster Provider (speed_tier)
- cheapest:    Günstigster Provider (cost_tier)
- best:        Höchste Qualität (quality_tier)
- random:      Zufällige Auswahl
"""
import asyncio
import logging
import time
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from enum import Enum
import httpx

logger = logging.getLogger("mesh_brain_v2")

# =============================================================================
# Enums
# =============================================================================

class Strategy(str, Enum):
    FALLBACK = "fallback"
    ROUND_ROBIN = "round_robin"
    FASTEST = "fastest"
    CHEAPEST = "cheapest"
    BEST = "best"
    RANDOM = "random"

class ProviderType(str, Enum):
    OLLAMA = "ollama"
    GEMINI = "gemini"
    MISTRAL = "mistral"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    ANTHROPIC = "anthropic"
    CLOUDFLARE = "cloudflare"
    CEREBRAS = "cerebras"
    GITHUB = "github"

# =============================================================================
# Provider Classes
# =============================================================================

@dataclass
class Provider:
    """Ein Provider im Load Balancer"""
    provider_id: str
    provider_type: ProviderType
    priority: int = 1           # Höher = bevorzugt
    cost_tier: int = 1          # 1=free/cheap, 5=expensive
    speed_tier: int = 3         # 1=slow, 5=fast
    quality_tier: int = 3       # 1=basic, 5=best
    healthy: bool = True
    requests_count: int = 0
    total_latency_ms: float = 0
    last_error: str = ""
    default_model: str = ""
    
    @property
    def avg_latency(self) -> float:
        if self.requests_count == 0:
            return 0
        return self.total_latency_ms / self.requests_count

@dataclass  
class OllamaNode(Provider):
    """Ollama Server Node"""
    host: str = "127.0.0.1"
    port: int = 11434
    models: List[str] = field(default_factory=list)
    
    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

# =============================================================================
# Mesh Brain v2.0
# =============================================================================

class MeshBrainV2:
    """Universal Load Balancer für alle AI Provider"""
    
    def __init__(self):
        self.providers: Dict[str, Provider] = {}
        self.ollama_nodes: Dict[str, OllamaNode] = {}
        self._round_robin_idx = 0
        self._initialized = False
        
    async def initialize(self):
        """Initialize all providers"""
        if self._initialized:
            return
            
        logger.info("Mesh Brain v2.0 initializing...")
        
        # === OLLAMA NODES ===
        self.ollama_nodes = {
            # Prefer compute away from the public hub. Hetzner remains the most
            # reliable fallback, but backup should absorb ordinary Ollama work.
            "backup": OllamaNode(
                provider_id="backup",
                provider_type=ProviderType.OLLAMA,
                priority=12,
                cost_tier=1,
                speed_tier=4,
                quality_tier=3,
                host="10.10.0.3",
                port=11434,
                default_model="qwen3.5:cloud"
            ),
            "zombie-pc": OllamaNode(
                provider_id="zombie-pc",
                provider_type=ProviderType.OLLAMA,
                priority=11,
                cost_tier=1,
                speed_tier=4,
                quality_tier=3,
                host="10.10.0.2",
                port=11434,
                default_model="hf-gemma-4-e4b-uncensored-hauhaucs-aggressive:latest"
            ),
            "hetzner": OllamaNode(
                provider_id="hetzner",
                provider_type=ProviderType.OLLAMA,
                priority=7,
                cost_tier=1,
                speed_tier=4,
                quality_tier=3,
                host="127.0.0.1",
                port=11434,
                default_model="qwen3.5:cloud"
            )
        }
        
        # === API PROVIDERS ===
        # Sortiert nach: Free/Fast zuerst, dann Premium
        self.providers = {
            # Tier 1: Free & Fast
            "groq": Provider(
                "groq", ProviderType.GROQ, 
                priority=9, cost_tier=1, speed_tier=5, quality_tier=3,
                default_model="llama-3.3-70b-versatile"
            ),
            "cerebras": Provider(
                "cerebras", ProviderType.CEREBRAS,
                priority=8, cost_tier=1, speed_tier=5, quality_tier=3,
                default_model="llama-3.3-70b"
            ),
            
            # Tier 2: Free/Cheap & Good
            "gemini": Provider(
                "gemini", ProviderType.GEMINI,
                priority=7, cost_tier=2, speed_tier=4, quality_tier=4,
                default_model="gemini-2.5-flash"
            ),
            "github": Provider(
                "github", ProviderType.GITHUB,
                priority=6, cost_tier=1, speed_tier=3, quality_tier=4,
                default_model="gpt-4o-mini"
            ),
            "cloudflare": Provider(
                "cloudflare", ProviderType.CLOUDFLARE,
                priority=5, cost_tier=1, speed_tier=4, quality_tier=3,
                default_model="@cf/meta/llama-3.2-3b-instruct"
            ),
            
            # Tier 3: Paid & Quality
            "mistral": Provider(
                "mistral", ProviderType.MISTRAL,
                priority=4, cost_tier=2, speed_tier=4, quality_tier=4,
                default_model="mistral-small-latest"
            ),
            "openrouter": Provider(
                "openrouter", ProviderType.OPENROUTER,
                priority=3, cost_tier=3, speed_tier=3, quality_tier=5,
                default_model="deepseek/deepseek-chat"
            ),
            
            # Tier 4: Premium
            "anthropic": Provider(
                "anthropic", ProviderType.ANTHROPIC,
                priority=2, cost_tier=5, speed_tier=3, quality_tier=5,
                default_model="claude-3.5-sonnet"
            ),
        }
        
        # Check Ollama nodes health
        for node_id, node in self.ollama_nodes.items():
            await self._check_ollama_health(node)
            
        self._initialized = True
        
        healthy_ollama = sum(1 for n in self.ollama_nodes.values() if n.healthy)
        total_models = sum(len(n.models) for n in self.ollama_nodes.values())
        
        logger.info(f"Mesh Brain v2.0 ready:")
        logger.info(f"  Ollama: {healthy_ollama}/{len(self.ollama_nodes)} nodes, {total_models} models")
        logger.info(f"  API: {len(self.providers)} providers")
    
    async def _check_ollama_health(self, node: OllamaNode):
        """Check Ollama node health"""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                start = time.time()
                resp = await client.get(f"{node.base_url}/api/tags")
                latency = (time.time() - start) * 1000
                
                if resp.status_code == 200:
                    data = resp.json()
                    node.models = [m["name"] for m in data.get("models", [])]
                    node.healthy = True
                    node.total_latency_ms = latency
                    node.requests_count = 1
                    logger.info(f"Ollama {node.provider_id}: {len(node.models)} models, {latency:.0f}ms")
                    return
        except Exception as e:
            node.last_error = str(e)
            logger.warning(f"Ollama {node.provider_id} unhealthy: {e}")
        node.healthy = False
    
    def get_all_providers(self, include_unhealthy: bool = False) -> List[Provider]:
        """Get all providers (Ollama + API)"""
        providers = []
        
        for node in self.ollama_nodes.values():
            if include_unhealthy or node.healthy:
                providers.append(node)
                
        for provider in self.providers.values():
            if include_unhealthy or provider.healthy:
                providers.append(provider)
                
        return providers
        
    def select_provider(self, strategy: Strategy = Strategy.FALLBACK,
                       prefer_local: bool = True,
                       provider_type: ProviderType = None,
                       model: str = None,
                       exclude: Optional[set[str]] = None) -> Optional[Provider]:
        """Select the best currently usable provider.

        Exclusions are used by retry/failover so a failed high-priority node
        cannot be selected repeatedly. For Ollama nodes, a discovered model
        inventory is authoritative for model-specific routing.
        """
        excluded = set(exclude or ())
        candidates = []

        def ollama_eligible(node: OllamaNode) -> bool:
            if not node.healthy or node.provider_id in excluded:
                return False
            if provider_type is not None and provider_type != ProviderType.OLLAMA:
                return False
            if model and node.models and model not in node.models:
                return False
            return True

        if prefer_local:
            for node in self.ollama_nodes.values():
                if ollama_eligible(node):
                    candidates.append(node)

        for provider in self.providers.values():
            if provider.healthy and provider.provider_id not in excluded:
                if provider_type is None or provider_type == provider.provider_type:
                    candidates.append(provider)

        if not prefer_local:
            for node in self.ollama_nodes.values():
                if node not in candidates and ollama_eligible(node):
                    candidates.append(node)

        if not candidates:
            logger.warning("No healthy providers available!")
            return None
            
        if strategy == Strategy.FALLBACK:
            # Priority encodes preferred placement. Model availability still
            # gets enforced by the Ollama request path after health discovery.
            return sorted(candidates, key=lambda p: -p.priority)[0]
            
        elif strategy == Strategy.ROUND_ROBIN:
            self._round_robin_idx = (self._round_robin_idx + 1) % len(candidates)
            return candidates[self._round_robin_idx]
            
        elif strategy == Strategy.CHEAPEST:
            return sorted(candidates, key=lambda p: (p.cost_tier, -p.priority))[0]
            
        elif strategy == Strategy.FASTEST:
            return sorted(candidates, key=lambda p: (-p.speed_tier, -p.priority))[0]
            
        elif strategy == Strategy.BEST:
            return sorted(candidates, key=lambda p: (-p.quality_tier, -p.priority))[0]
            
        elif strategy == Strategy.RANDOM:
            return random.choice(candidates)
            
        return candidates[0]
    
    async def chat(self, message: str, 
                  model: str = None,
                  strategy: Strategy = Strategy.FALLBACK,
                  system: str = None,
                  prefer_local: bool = True,
                  provider_id: str = None,
                  max_retries: int = 3) -> Dict[str, Any]:
        """
        Send chat request via load balancer.
        
        Args:
            message: The prompt/message
            model: Specific model (optional, auto-select if None)
            strategy: Load balancing strategy
            system: System prompt
            prefer_local: Prefer Ollama over API
            provider_id: Force specific provider
            max_retries: Max retry attempts
            
        Returns:
            Response dict with _provider, _strategy, _latency_ms metadata
        """
        await self.initialize()
        
        # If an explicit model is known on Ollama, keep failover inside the
        # Ollama node pool. This avoids sending an Ollama-only model name to an
        # unrelated API provider during retries.
        routed_type = None
        if model and any(
            node.healthy and model in node.models for node in self.ollama_nodes.values()
        ):
            routed_type = ProviderType.OLLAMA

        # Force specific provider?
        if provider_id:
            provider = self.ollama_nodes.get(provider_id) or self.providers.get(provider_id)
            if not provider:
                return {"error": f"Unknown provider: {provider_id}"}
        else:
            provider = self.select_provider(
                strategy, prefer_local, provider_type=routed_type, model=model
            )

        if not provider:
            return {"error": "No healthy providers available"}
        
        retries = 0
        last_error = None
        tried_providers = set()
        
        while retries < max_retries:
            if provider.provider_id in tried_providers:
                # Get next provider
                provider = self.select_provider(
                    strategy, prefer_local, provider_type=routed_type,
                    model=model, exclude=tried_providers
                )
                if not provider:
                    break
                    
            tried_providers.add(provider.provider_id)
            start = time.time()
            
            try:
                if isinstance(provider, OllamaNode):
                    result = await self._chat_ollama(provider, message, model, system)
                else:
                    result = await self._chat_api(provider, message, model, system)
                
                elapsed = (time.time() - start) * 1000
                provider.requests_count += 1
                provider.total_latency_ms += elapsed
                
                if "error" not in result:
                    result["_provider"] = provider.provider_id
                    result["_provider_type"] = provider.provider_type.value
                    result["_strategy"] = strategy.value
                    result["_latency_ms"] = round(elapsed, 2)
                    result["_model"] = model or provider.default_model
                    return result
                else:
                    last_error = result.get("error")
                    logger.warning(f"Provider {provider.provider_id} returned error: {last_error}")
                    
            except Exception as e:
                last_error = str(e)
                provider.last_error = last_error
                logger.error(f"Provider {provider.provider_id} failed: {e}")
            
            retries += 1
            provider = self.select_provider(
                strategy, prefer_local, provider_type=routed_type,
                model=model, exclude=tried_providers
            )

        return {
            "error": f"All providers failed after {retries} retries. Last error: {last_error}",
            "_tried": list(tried_providers)
        }
    
    async def _chat_ollama(self, node: OllamaNode, message: str, 
                          model: str, system: str) -> Dict[str, Any]:
        """Chat via Ollama node"""
        if not model:
            # Use default or first available
            if node.default_model and node.default_model in node.models:
                model = node.default_model
            elif node.models:
                model = node.models[0]
            else:
                return {"error": "No models available on node"}
            
        payload = {"model": model, "prompt": message, "stream": False}
        if system:
            payload["system"] = system
            
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{node.base_url}/api/generate", json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "response": data.get("response", ""),
                    "model": model,
                    "done": data.get("done", True),
                    "eval_count": data.get("eval_count", 0),
                }
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    
    async def _chat_api(self, provider: Provider, message: str,
                       model: str, system: str) -> Dict[str, Any]:
        """Chat via API provider - delegates to chat_router"""
        try:
            from app.services.chat_router import handle_chat_smart
        except ImportError:
            return {"error": "chat_router not available"}
        
        # Build full model name if needed
        if not model:
            model = provider.default_model
            
        # Add provider prefix if missing
        prefix_map = {
            ProviderType.GEMINI: "gemini/",
            ProviderType.MISTRAL: "mistral/",
            ProviderType.GROQ: "groq/",
            ProviderType.OPENROUTER: "openrouter/",
            ProviderType.ANTHROPIC: "anthropic/",
            ProviderType.CLOUDFLARE: "cloudflare/",
            ProviderType.CEREBRAS: "cerebras/",
            ProviderType.GITHUB: "github/",
        }
        
        prefix = prefix_map.get(provider.provider_type, "")
        if prefix and not model.startswith(prefix):
            model = f"{prefix}{model}"
        
        result = await handle_chat_smart({
            "message": message,
            "model": model,
            "system_prompt": system
        })
        
        # Normalize response
        if isinstance(result, dict):
            if "response" not in result and "output" in result:
                result["response"] = result["output"]
            return result
        
        return {"response": str(result)}
    
    async def broadcast(self, message: str, 
                       strategy: Strategy = Strategy.FASTEST,
                       system: str = None,
                       max_providers: int = 3) -> Dict[str, Any]:
        """
        Send to multiple providers simultaneously.
        
        Args:
            message: The prompt
            strategy: How to select the final result
            system: System prompt
            max_providers: Max providers to query
            
        Returns:
            Best response based on strategy
        """
        await self.initialize()
        
        providers = self.get_all_providers()[:max_providers]
        
        if not providers:
            return {"error": "No providers available"}
        
        # Create tasks
        tasks = []
        for provider in providers:
            if isinstance(provider, OllamaNode):
                task = self._chat_ollama(provider, message, None, system)
            else:
                task = self._chat_api(provider, message, None, system)
            tasks.append((provider, asyncio.create_task(task)))
        
        # Wait for results
        results = []
        for provider, task in tasks:
            try:
                result = await asyncio.wait_for(task, timeout=60)
                if "error" not in result:
                    result["_provider"] = provider.provider_id
                    results.append(result)
            except asyncio.TimeoutError:
                logger.warning(f"Provider {provider.provider_id} timed out")
            except Exception as e:
                logger.warning(f"Provider {provider.provider_id} failed: {e}")
        
        if not results:
            return {"error": "All providers failed"}
        
        # Select best based on strategy
        if strategy == Strategy.FASTEST:
            # Already have first result
            best = results[0]
        elif strategy == Strategy.BEST:
            # Longest response (simple heuristic)
            best = max(results, key=lambda r: len(r.get("response", "")))
        else:
            best = results[0]
            
        best["_broadcast"] = True
        best["_responses"] = len(results)
        best["_providers"] = [r["_provider"] for r in results]
        
        return best
    
    async def get_status(self) -> Dict[str, Any]:
        """Full status of all providers"""
        await self.initialize()
        
        # Refresh Ollama health
        for node in self.ollama_nodes.values():
            await self._check_ollama_health(node)
        
        total_ollama_models = sum(len(n.models) for n in self.ollama_nodes.values())
        healthy_ollama = sum(1 for n in self.ollama_nodes.values() if n.healthy)
        healthy_api = sum(1 for p in self.providers.values() if p.healthy)
        
        return {
            "version": "2.0",
            "summary": {
                "ollama_nodes": f"{healthy_ollama}/{len(self.ollama_nodes)}",
                "ollama_models": total_ollama_models,
                "api_providers": f"{healthy_api}/{len(self.providers)}",
                "total_providers": len(self.ollama_nodes) + len(self.providers),
            },
            "ollama_nodes": {
                nid: {
                    "healthy": n.healthy,
                    "host": f"{n.host}:{n.port}",
                    "models": len(n.models),
                    "model_list": n.models,
                    "priority": n.priority,
                    "requests": n.requests_count,
                    "avg_latency_ms": round(n.avg_latency, 2),
                    "last_error": n.last_error or None
                }
                for nid, n in self.ollama_nodes.items()
            },
            "api_providers": {
                pid: {
                    "healthy": p.healthy,
                    "type": p.provider_type.value,
                    "default_model": p.default_model,
                    "priority": p.priority,
                    "cost_tier": p.cost_tier,
                    "speed_tier": p.speed_tier,
                    "quality_tier": p.quality_tier,
                    "requests": p.requests_count,
                    "avg_latency_ms": round(p.avg_latency, 2),
                    "last_error": p.last_error or None
                }
                for pid, p in self.providers.items()
            },
            "strategies": [s.value for s in Strategy]
        }
    
    def mark_unhealthy(self, provider_id: str):
        """Mark a provider as unhealthy"""
        if provider_id in self.ollama_nodes:
            self.ollama_nodes[provider_id].healthy = False
        elif provider_id in self.providers:
            self.providers[provider_id].healthy = False
            
    def mark_healthy(self, provider_id: str):
        """Mark a provider as healthy"""
        if provider_id in self.ollama_nodes:
            self.ollama_nodes[provider_id].healthy = True
        elif provider_id in self.providers:
            self.providers[provider_id].healthy = True

# =============================================================================
# Singleton
# =============================================================================

mesh_brain_v2 = MeshBrainV2()

# =============================================================================
# Convenience Functions
# =============================================================================

async def brain_chat(message: str, **kwargs) -> Dict[str, Any]:
    """Quick chat via mesh brain"""
    return await mesh_brain_v2.chat(message, **kwargs)

async def brain_status() -> Dict[str, Any]:
    """Get brain status"""
    return await mesh_brain_v2.get_status()

async def brain_broadcast(message: str, **kwargs) -> Dict[str, Any]:
    """Broadcast to multiple providers"""
    return await mesh_brain_v2.broadcast(message, **kwargs)

# =============================================================================
# CLI Test
# =============================================================================

async def test_brain():
    """Test the Mesh Brain v2"""
    import json
    
    brain = MeshBrainV2()
    
    print("=" * 60)
    print("MESH BRAIN v2.0 TEST")
    print("=" * 60)
    
    # Status
    print("\n=== STATUS ===")
    status = await brain.get_status()
    print(f"Ollama: {status['summary']['ollama_nodes']} nodes, {status['summary']['ollama_models']} models")
    print(f"API: {status['summary']['api_providers']} providers")
    
    # Test strategies
    test_prompt = "Was ist 2+2? Antworte mit nur der Zahl."
    
    for strategy in [Strategy.FALLBACK, Strategy.CHEAPEST, Strategy.FASTEST]:
        print(f"\n=== {strategy.value.upper()} ===")
        result = await brain.chat(test_prompt, strategy=strategy)
        print(f"Provider: {result.get('_provider', 'unknown')}")
        print(f"Latency: {result.get('_latency_ms', 0)}ms")
        print(f"Response: {result.get('response', result.get('error', 'no response'))[:100]}")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(test_brain())
