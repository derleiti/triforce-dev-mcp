from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

ConversationInput = Union[str, List[Dict[str, Any]]]


class MistralAgentError(RuntimeError):
    """Raised when the Mistral Agents/Conversations API rejects a request."""


@dataclass(frozen=True)
class MistralAgentConfig:
    agent_id: str
    agent_version: Optional[Union[int, str]]
    base_url: str
    timeout_seconds: int
    store: bool


class MistralAgentService:
    """Native adapter for Mistral Agents + Conversations.

    This intentionally uses the REST API instead of the pinned mistralai SDK because
    the REST API exposes agent_version on conversation start while older SDK builds do not.
    """

    def _config(self) -> MistralAgentConfig:
        settings = get_settings()
        agent_id = (settings.mistral_agent_id or settings.nova_mistral_agent_id or "").strip()
        return MistralAgentConfig(
            agent_id=agent_id,
            agent_version=settings.mistral_agent_version,
            base_url=settings.mistral_agent_base_url.rstrip("/"),
            timeout_seconds=settings.mistral_agent_timeout_seconds,
            store=settings.mistral_agent_store,
        )

    def status(self) -> Dict[str, Any]:
        settings = get_settings()
        cfg = self._config()
        return {
            "configured": bool(settings.mistral_api_key and cfg.agent_id),
            "api_key_configured": bool(settings.mistral_api_key),
            "agent_id": cfg.agent_id or None,
            "agent_version": cfg.agent_version,
            "base_url": cfg.base_url,
            "timeout_seconds": cfg.timeout_seconds,
            "store": cfg.store,
        }

    @staticmethod
    def _headers() -> Dict[str, str]:
        api_key = (get_settings().mistral_api_key or "").strip()
        if not api_key:
            raise MistralAgentError("MISTRAL_API_KEY is not configured")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        cfg = self._config()
        url = f"{cfg.base_url}{path}"
        client_timeout = aiohttp.ClientTimeout(total=timeout or cfg.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.request(method, url, headers=self._headers(), json=payload) as response:
                    body = await response.text()
                    try:
                        data = json.loads(body) if body else {}
                    except json.JSONDecodeError:
                        data = {"raw": body}
                    if response.status >= 400:
                        detail = data.get("message") or data.get("detail") or data.get("error") or body
                        raise MistralAgentError(f"Mistral API {response.status}: {detail}")
                    if not isinstance(data, dict):
                        return {"data": data}
                    return data
        except aiohttp.ClientError as exc:
            raise MistralAgentError(f"Mistral API transport error: {exc}") from exc

    @staticmethod
    def extract_text(response: Dict[str, Any]) -> str:
        chunks: List[str] = []
        for output in response.get("outputs") or []:
            if not isinstance(output, dict):
                continue
            content = output.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, str):
                        chunks.append(part)
                    elif isinstance(part, dict):
                        text = part.get("text") or part.get("content")
                        if isinstance(text, str):
                            chunks.append(text)
        return "\n".join(chunk for chunk in chunks if chunk).strip()

    async def get_agent(
        self,
        agent_id: Optional[str] = None,
        agent_version: Optional[Union[int, str]] = None,
    ) -> Dict[str, Any]:
        cfg = self._config()
        resolved_id = (agent_id or cfg.agent_id).strip()
        if not resolved_id:
            raise MistralAgentError("MISTRAL_AGENT_ID is not configured")
        resolved_version = cfg.agent_version if agent_version is None else agent_version
        if resolved_version is None:
            return await self._request("GET", f"/v1/agents/{resolved_id}")
        return await self._request("GET", f"/v1/agents/{resolved_id}/versions/{resolved_version}")

    async def start(
        self,
        inputs: ConversationInput,
        *,
        agent_id: Optional[str] = None,
        agent_version: Optional[Union[int, str]] = None,
        store: Optional[bool] = None,
        instructions: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        completion_args: Optional[Dict[str, Any]] = None,
        handoff_execution: str = "server",
    ) -> Dict[str, Any]:
        cfg = self._config()
        resolved_id = (agent_id or cfg.agent_id).strip()
        if not resolved_id:
            raise MistralAgentError("MISTRAL_AGENT_ID is not configured")
        payload: Dict[str, Any] = {
            "agent_id": resolved_id,
            "inputs": inputs,
            "store": cfg.store if store is None else store,
            "handoff_execution": handoff_execution,
        }
        resolved_version = cfg.agent_version if agent_version is None else agent_version
        if resolved_version is not None:
            payload["agent_version"] = resolved_version
        if instructions:
            payload["instructions"] = instructions
        if description:
            payload["description"] = description
        if metadata:
            payload["metadata"] = metadata
        if completion_args:
            payload["completion_args"] = completion_args
        result = await self._request("POST", "/v1/conversations", payload=payload)
        result["response"] = self.extract_text(result)
        return result

    async def append(
        self,
        conversation_id: str,
        inputs: ConversationInput,
        *,
        store: Optional[bool] = None,
        completion_args: Optional[Dict[str, Any]] = None,
        handoff_execution: str = "server",
    ) -> Dict[str, Any]:
        cfg = self._config()
        payload: Dict[str, Any] = {
            "inputs": inputs,
            "store": cfg.store if store is None else store,
            "handoff_execution": handoff_execution,
        }
        if completion_args:
            payload["completion_args"] = completion_args
        result = await self._request("POST", f"/v1/conversations/{conversation_id}", payload=payload)
        result["response"] = self.extract_text(result)
        return result

    async def get_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/conversations/{conversation_id}")

    async def get_history(self, conversation_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/conversations/{conversation_id}/history")

    async def get_messages(self, conversation_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/conversations/{conversation_id}/messages")

    async def delete_conversation(self, conversation_id: str) -> Dict[str, Any]:
        return await self._request("DELETE", f"/v1/conversations/{conversation_id}")

    async def restart(
        self,
        conversation_id: str,
        from_entry_id: str,
        inputs: ConversationInput,
        *,
        agent_version: Optional[Union[int, str]] = None,
        store: Optional[bool] = None,
        completion_args: Optional[Dict[str, Any]] = None,
        handoff_execution: str = "server",
    ) -> Dict[str, Any]:
        cfg = self._config()
        payload: Dict[str, Any] = {
            "from_entry_id": from_entry_id,
            "inputs": inputs,
            "store": cfg.store if store is None else store,
            "handoff_execution": handoff_execution,
        }
        resolved_version = cfg.agent_version if agent_version is None else agent_version
        if resolved_version is not None:
            payload["agent_version"] = resolved_version
        if completion_args:
            payload["completion_args"] = completion_args
        result = await self._request(
            "POST", f"/v1/conversations/{conversation_id}/restart", payload=payload
        )
        result["response"] = self.extract_text(result)
        return result

    async def review(
        self,
        task: str,
        *,
        context: str = "",
        diff: str = "",
        tests: str = "",
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = (
            "Act as TriForce's independent final reviewer. Check the implementation against the original task, "
            "look for unfinished work, regressions, unsafe assumptions, missing tests, and architecture mismatches. "
            "Prefer concrete evidence. End with exactly one verdict line: VERDICT: PASS or VERDICT: FIX_REQUIRED.\n\n"
            f"ORIGINAL TASK:\n{task.strip()}\n"
        )
        if context.strip():
            prompt += f"\nHANDOFF / CONTEXT:\n{context.strip()}\n"
        if diff.strip():
            prompt += f"\nCURRENT DIFF:\n{diff.strip()}\n"
        if tests.strip():
            prompt += f"\nTEST RESULTS:\n{tests.strip()}\n"

        if conversation_id:
            result = await self.append(conversation_id, prompt)
        else:
            result = await self.start(prompt, description="TriForce independent implementation review")

        text = result.get("response", "")
        upper = text.upper()
        if "VERDICT: FIX_REQUIRED" in upper:
            verdict = "FIX_REQUIRED"
        elif "VERDICT: PASS" in upper:
            verdict = "PASS"
        else:
            verdict = "UNKNOWN"
        return {**result, "verdict": verdict}


mistral_agent_service = MistralAgentService()
