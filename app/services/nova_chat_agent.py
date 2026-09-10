from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services.chat_router import api_proxy
from app.services.tristar.agent_controller import agent_controller


@dataclass
class NovaAccountProfile:
    provider: str
    route: str
    agent_id: Optional[str]
    model: Optional[str]
    url: Optional[str]
    user: Optional[str]
    password_set: bool
    configured: bool

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "route": self.route,
            "agent_id": self.agent_id,
            "model": self.model,
            "url_configured": bool(self.url),
            "user_configured": bool(self.user),
            "password_configured": self.password_set,
            "configured": self.configured,
        }


@dataclass
class NovaSpecializedAgent:
    id: str
    provider: str
    name: str
    description: str
    capabilities: List[str]
    use_cases: List[str]
    account: NovaAccountProfile

    def to_public_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name,
            "description": self.description,
            "capabilities": self.capabilities,
            "use_cases": self.use_cases,
            "configured": self.account.configured,
            "account": self.account.to_public_dict(),
        }


class NovaChatAgentService:
    """Internal Nova router for provider-specific chat accounts."""

    _SPECIALIZED_AGENT_DEFS = {
        "chatgpt": {
            "id": "nova-account/chatgpt",
            "name": "Nova ChatGPT Account Agent",
            "description": "Generalist account-backed agent for code execution, debugging, and task planning.",
            "capabilities": ["code", "debug", "planning", "general_reasoning"],
            "use_cases": ["implementation tasks", "debugging", "terminal workflows"],
        },
        "gemini": {
            "id": "nova-account/google",
            "name": "Nova Google Account Agent",
            "description": "Fast multimodal account-backed agent for research, summaries, and broad-context tasks.",
            "capabilities": ["research", "summarization", "multimodal", "fast_response"],
            "use_cases": ["research", "document digestion", "context-heavy prompts"],
        },
        "claude": {
            "id": "nova-account/claude",
            "name": "Nova Claude Account Agent",
            "description": "Account-backed agent optimized for long-context reasoning, reviews, and polished writing.",
            "capabilities": ["reasoning", "code_review", "long_context", "writing"],
            "use_cases": ["reviews", "architecture reasoning", "high-quality drafting"],
        },
        "mistral": {
            "id": "nova-agent/mistral",
            "name": "Nova Mistral Native Agent",
            "description": "Version-pinned Mistral Agent with persistent Conversations API state for reviews, research, and handoffs.",
            "capabilities": ["code_review", "research", "persistent_context", "handoffs"],
            "use_cases": ["independent final review", "stage handoffs", "long-running task review"],
        },
    }

    def _resolve_provider_name(self, provider: str) -> str:
        provider = (provider or "auto").strip().lower()
        specialized_map = {
            spec["id"]: name
            for name, spec in self._SPECIALIZED_AGENT_DEFS.items()
        }
        if provider in {"google", "gemini"}:
            return "gemini"
        return specialized_map.get(provider, provider)

    def _profile(self, provider: str) -> NovaAccountProfile:
        settings = get_settings()
        provider = self._resolve_provider_name(provider)

        if provider == "auto":
            for candidate in ("chatgpt", "gemini", "claude", "mistral"):
                profile = self._profile(candidate)
                if profile.configured:
                    return profile
            return NovaAccountProfile("auto", "unconfigured", None, None, None, None, False, False)

        if provider == "chatgpt":
            url = settings.nova_chatgpt_url or settings.chatgpt_url
            user = settings.nova_chatgpt_user or settings.chatgpt_user
            password = settings.nova_chatgpt_pass or settings.chatgpt_pass
            agent_id = settings.nova_chatgpt_agent_id or "codex-mcp"
            return NovaAccountProfile(
                provider="chatgpt",
                route="internal_mcp_agent",
                agent_id=agent_id,
                model=None,
                url=url,
                user=user,
                password_set=bool(password),
                configured=bool(agent_id and (url or user or password)),
            )

        if provider in {"google", "gemini"}:
            url = settings.nova_google_url or settings.google_url
            user = settings.nova_google_user or settings.google_user
            password = settings.nova_google_pass or settings.google_pass
            agent_id = settings.nova_gemini_agent_id or settings.gemini_agent_id or "gemini-mcp"
            return NovaAccountProfile(
                provider="gemini",
                route="internal_mcp_agent",
                agent_id=agent_id,
                model=None,
                url=url,
                user=user,
                password_set=bool(password or settings.gemini_api_key),
                configured=bool(agent_id and (url or user or password or settings.gemini_api_key)),
            )

        if provider == "claude":
            url = settings.nova_claude_url or settings.claude_url
            user = settings.nova_claude_user or settings.claude_user
            password = settings.nova_claude_pass or settings.claude_pass
            agent_id = settings.nova_claude_agent_id or settings.claude_agent_id or "claude-mcp"
            return NovaAccountProfile(
                provider="claude",
                route="internal_mcp_agent",
                agent_id=agent_id,
                model=None,
                url=url,
                user=user,
                password_set=bool(password or settings.anthropic_api_key),
                configured=bool(agent_id and (url or user or password or settings.anthropic_api_key)),
            )

        if provider == "mistral":
            url = settings.mistral_agent_base_url or settings.nova_mistral_url
            user = settings.nova_mistral_user
            password = settings.nova_mistral_pass
            agent_id = settings.mistral_agent_id or settings.nova_mistral_agent_id
            if settings.mistral_api_key and agent_id:
                return NovaAccountProfile(
                    provider="mistral",
                    route="mistral_agent_api",
                    agent_id=agent_id,
                    model=None,
                    url=url,
                    user=user,
                    password_set=True,
                    configured=True,
                )
            return NovaAccountProfile(
                provider="mistral",
                route="direct_api",
                agent_id=None,
                model="mistral/mistral-large-latest",
                url=url,
                user=user,
                password_set=bool(password or settings.mistral_api_key),
                configured=bool(password or settings.mistral_api_key),
            )

        raise ValueError(f"Unsupported provider: {provider}")

    def _specialized_agents(self) -> List[NovaSpecializedAgent]:
        agents: List[NovaSpecializedAgent] = []
        for provider, metadata in self._SPECIALIZED_AGENT_DEFS.items():
            agents.append(
                NovaSpecializedAgent(
                    id=metadata["id"],
                    provider=provider,
                    name=metadata["name"],
                    description=metadata["description"],
                    capabilities=list(metadata["capabilities"]),
                    use_cases=list(metadata["use_cases"]),
                    account=self._profile(provider),
                )
            )
        return agents

    def list_specialized_agents(self) -> Dict[str, Any]:
        agents = self._specialized_agents()
        return {
            "specialized_agents": [agent.to_public_dict() for agent in agents],
            "configured_specialized_agents": [
                agent.id for agent in agents if agent.account.configured
            ],
        }

    def list_accounts(self) -> Dict[str, Any]:
        profiles = [self._profile(name) for name in ("chatgpt", "gemini", "claude", "mistral")]
        return {
            "accounts": [profile.to_public_dict() for profile in profiles],
            "configured_providers": [profile.provider for profile in profiles if profile.configured],
            **self.list_specialized_agents(),
        }

    def _build_messages(
        self,
        message: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        system: str = "",
    ) -> List[Dict[str, str]]:
        if messages:
            normalized: List[Dict[str, str]] = []
            for item in messages:
                role = str(item.get("role", "user"))
                content = item.get("content", "")
                if isinstance(content, list):
                    content = json.dumps(content, ensure_ascii=False)
                normalized.append({"role": role, "content": str(content)})
            if system.strip() and (not normalized or normalized[0]["role"] != "system"):
                normalized.insert(0, {"role": "system", "content": system.strip()})
            return normalized

        if not message.strip():
            raise ValueError("message or messages required")

        built: List[Dict[str, str]] = []
        if system.strip():
            built.append({"role": "system", "content": system.strip()})
        built.append({"role": "user", "content": message})
        return built

    def _messages_to_agent_prompt(self, messages: List[Dict[str, str]]) -> str:
        chunks = [
            "You are nova_chat_agent. Follow the conversation and answer the latest user request directly.",
        ]
        for item in messages:
            role = item.get("role", "user").upper()
            content = item.get("content", "").strip()
            if content:
                chunks.append(f"{role}:\n{content}")
        return "\n\n".join(chunks)

    async def chat(
        self,
        provider: str = "auto",
        message: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 1200,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        profile = self._profile(provider)
        if not profile.configured:
            raise ValueError(f"Provider not configured for nova_chat_agent: {profile.provider}")

        chat_messages = self._build_messages(message=message, messages=messages, system=system)

        if profile.route == "internal_mcp_agent":
            prompt = self._messages_to_agent_prompt(chat_messages)
            result = await agent_controller.call_agent(profile.agent_id or "", prompt, timeout=timeout)
            if result.get("status") != "success":
                raise RuntimeError(result.get("error") or f"nova_chat_agent failed via {profile.agent_id}")
            return {
                "provider": profile.provider,
                "route": profile.route,
                "agent_id": profile.agent_id,
                "response": result.get("response", ""),
                "account": profile.to_public_dict(),
            }

        if profile.route == "mistral_agent_api":
            from app.services.mistral_agent import mistral_agent_service
            prompt = self._messages_to_agent_prompt(chat_messages)
            completion_args = {"temperature": temperature, "max_tokens": max_tokens}
            result = await mistral_agent_service.start(
                prompt,
                agent_id=profile.agent_id,
                completion_args=completion_args,
            )
            return {
                "provider": profile.provider,
                "route": profile.route,
                "agent_id": profile.agent_id,
                "agent_version": get_settings().mistral_agent_version,
                "conversation_id": result.get("conversation_id"),
                "response": result.get("response", ""),
                "account": profile.to_public_dict(),
            }

        model_id = model or profile.model or "mistral/mistral-large-latest"
        response = await api_proxy.chat(
            model=model_id,
            messages=chat_messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return {
            "provider": profile.provider,
            "route": profile.route,
            "model": model_id,
            "response": response,
            "account": profile.to_public_dict(),
        }

    async def invoke_specialized_agent(
        self,
        specialist_id: str,
        message: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
        system: str = "",
        model: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 1200,
        timeout: int = 120,
    ) -> Dict[str, Any]:
        provider = self._resolve_provider_name(specialist_id)
        if provider not in self._SPECIALIZED_AGENT_DEFS:
            raise ValueError(f"Unknown Nova specialized agent: {specialist_id}")
        result = await self.chat(
            provider=provider,
            message=message,
            messages=messages,
            system=system,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        result["specialized_agent_id"] = self._SPECIALIZED_AGENT_DEFS[provider]["id"]
        return result


nova_chat_agent_service = NovaChatAgentService()
