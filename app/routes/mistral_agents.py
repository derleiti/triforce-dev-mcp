from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mistral_agent import MistralAgentError, mistral_agent_service

router = APIRouter(prefix="/mistral/agents", tags=["mistral-agents"])
ConversationInput = Union[str, List[Dict[str, Any]]]


class StartRequest(BaseModel):
    inputs: ConversationInput
    agent_id: Optional[str] = None
    agent_version: Optional[Union[int, str]] = None
    store: Optional[bool] = None
    instructions: Optional[str] = None
    description: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    completion_args: Optional[Dict[str, Any]] = None
    handoff_execution: str = "server"


class AppendRequest(BaseModel):
    inputs: ConversationInput
    store: Optional[bool] = None
    completion_args: Optional[Dict[str, Any]] = None
    handoff_execution: str = "server"


class RestartRequest(AppendRequest):
    from_entry_id: str
    agent_version: Optional[Union[int, str]] = None


class ReviewRequest(BaseModel):
    task: str = Field(min_length=1)
    context: str = ""
    diff: str = ""
    tests: str = ""
    conversation_id: Optional[str] = None


def _raise(exc: MistralAgentError) -> None:
    raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status")
async def status() -> Dict[str, Any]:
    return {"ok": True, **mistral_agent_service.status()}


@router.get("/configured")
async def configured_agent() -> Dict[str, Any]:
    try:
        return {"ok": True, "agent": await mistral_agent_service.get_agent()}
    except MistralAgentError as exc:
        _raise(exc)


@router.post("/conversations")
async def start_conversation(req: StartRequest) -> Dict[str, Any]:
    try:
        result = await mistral_agent_service.start(**req.model_dump())
        return {"ok": True, **result}
    except MistralAgentError as exc:
        _raise(exc)


@router.post("/conversations/{conversation_id}")
async def append_conversation(conversation_id: str, req: AppendRequest) -> Dict[str, Any]:
    try:
        result = await mistral_agent_service.append(conversation_id, **req.model_dump())
        return {"ok": True, **result}
    except MistralAgentError as exc:
        _raise(exc)


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> Dict[str, Any]:
    try:
        return {"ok": True, "conversation": await mistral_agent_service.get_conversation(conversation_id)}
    except MistralAgentError as exc:
        _raise(exc)


@router.get("/conversations/{conversation_id}/history")
async def get_history(conversation_id: str) -> Dict[str, Any]:
    try:
        return {"ok": True, "history": await mistral_agent_service.get_history(conversation_id)}
    except MistralAgentError as exc:
        _raise(exc)


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str) -> Dict[str, Any]:
    try:
        return {"ok": True, "messages": await mistral_agent_service.get_messages(conversation_id)}
    except MistralAgentError as exc:
        _raise(exc)


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> Dict[str, Any]:
    try:
        return {"ok": True, "result": await mistral_agent_service.delete_conversation(conversation_id)}
    except MistralAgentError as exc:
        _raise(exc)


@router.post("/conversations/{conversation_id}/restart")
async def restart_conversation(conversation_id: str, req: RestartRequest) -> Dict[str, Any]:
    try:
        data = req.model_dump()
        from_entry_id = data.pop("from_entry_id")
        result = await mistral_agent_service.restart(conversation_id, from_entry_id, **data)
        return {"ok": True, **result}
    except MistralAgentError as exc:
        _raise(exc)


@router.post("/review")
async def review(req: ReviewRequest) -> Dict[str, Any]:
    try:
        return {"ok": True, **await mistral_agent_service.review(**req.model_dump())}
    except MistralAgentError as exc:
        _raise(exc)
