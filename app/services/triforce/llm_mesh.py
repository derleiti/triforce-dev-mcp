"""
LLM Mesh Network v2.60 - Full Mesh with Security Features

Provides LLM-to-LLM communication in a full mesh network:
- llm_call: Call a single LLM with RBAC, circuit breaker, and cycle detection
- llm_broadcast: Call multiple LLMs in parallel
- llm_consensus: Get consensus from multiple LLMs
- llm_delegate: Delegate specialized tasks to specific LLMs

Supports 9+ LLMs: Gemini, Claude, DeepSeek, Qwen, Kimi, Nova, Cogito, Mistral, GLM, MiniMax
"""

import asyncio
import uuid
import time
from typing import List, Dict, Any, Optional
import logging

from .circuit_breaker import circuit_registry, cycle_detector, rate_limiter
from .rbac import rbac_service
from .audit_logger import audit_logger

logger = logging.getLogger("ailinux.triforce.llm_mesh")


# Model ID aliases - maps short names to full model IDs
MODEL_ALIASES: Dict[str, str] = {
    # Lead models
    "gemini": "gemini/gemini-2.5-flash",
    "kimi": "kimi-k2:1t-cloud",

    # Worker models
    "claude": "anthropic/claude-sonnet-4",
    "deepseek": "deepseek-v3.1:671b-cloud",
    "qwen": "qwen3-vl:235b-cloud",  # Vision + Chat
    "qwen-coder": "qwen3-coder:480b-cloud",  # Code-specialized
    "glm": "glm-4.6:cloud",
    "minimax": "minimax-m2:cloud",

    # Reviewer/Reasoning models
    "mistral": "mistral/mistral-medium-latest",  # Faster, more stable
    "mistral-large": "mistral/mistral-large-latest",  # For complex tasks
    "cogito": "cogito-2.1:671b-cloud",

    # Admin/Special models
    "nova": "gpt-oss:cloud/120b",
    "codex": "gpt-oss:20b-cloud",

    # Reasoning models
    "kimi-thinking": "kimi-k2-thinking:cloud",
    "magistral": "mistral/magistral-medium-latest",
}


# LLM specializations for task routing
LLM_SPECIALIZATIONS: Dict[str, List[str]] = {
    "gemini": ["coordination", "planning", "research", "vision"],
    "claude": ["coding", "analysis", "documentation", "review"],
    "deepseek": ["heavy_coding", "algorithms", "optimization"],
    "qwen": ["multilingual", "vision", "general"],
    "qwen-coder": ["code_generation", "code_review", "refactoring"],
    "kimi": ["long_context", "research", "analysis"],
    "kimi-thinking": ["deep_reasoning", "math", "logic"],
    "nova": ["german", "documentation", "creative"],
    "cogito": ["reasoning", "logic", "debugging"],
    "mistral": ["review", "security", "fast_response"],
    "mistral-large": ["complex_analysis", "code_analysis", "reasoning"],
    "magistral": ["deep_reasoning", "math", "logic"],
    "glm": ["chinese", "general", "agents"],
    "minimax": ["agents", "general", "fast"],
}


def get_model_id(target: str) -> str:
    """Resolve target to full model ID"""
    return MODEL_ALIASES.get(target.lower(), target)


def get_best_llm_for_task(task_type: str) -> Optional[str]:
    """Find the best LLM for a task type"""
    for llm_id, specializations in LLM_SPECIALIZATIONS.items():
        if task_type.lower() in specializations:
            if circuit_registry.is_available(llm_id):
                return llm_id
    return "gemini"  # Default fallback


async def llm_call(
    target: str,
    prompt: str,
    caller_llm: str = "unknown",
    context: Optional[Dict[str, Any]] = None,
    max_tokens: int = 2048,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout: int = 120,
    priority: str = "normal"
) -> Dict[str, Any]:
    """
    Call a single LLM with full security features.

    Features:
    - RBAC permission check
    - Cycle detection (prevents infinite loops)
    - Rate limiting
    - Circuit breaker with fallback
    - Audit logging

    Args:
        target: Target LLM (e.g., "deepseek", "gemini")
        prompt: The prompt to send
        caller_llm: ID of the calling LLM
        context: Additional context dict
        max_tokens: Max response tokens
        trace_id: Trace ID for audit (auto-generated if not provided)
        session_id: Session ID for audit
        timeout: Timeout in seconds
        priority: Priority level (high|normal|low)

    Returns:
        Dict with success status, response, and metadata
    """
    start_time = time.time()
    trace_id = trace_id or str(uuid.uuid4())

    # 1. RBAC Check
    if not rbac_service.can_call_llm(caller_llm, target):
        await audit_logger.log_security_event(
            llm_id=caller_llm,
            event_type="llm_call_denied",
            details={"target": target, "reason": "rbac"},
            trace_id=trace_id,
            session_id=session_id
        )
        return {
            "target": target,
            "success": False,
            "error": f"RBAC denied: {caller_llm} cannot call {target}",
            "trace_id": trace_id
        }

    # 2. Cycle Detection
    if not cycle_detector.add_to_chain(trace_id, target):
        chain = cycle_detector.get_chain(trace_id)
        await audit_logger.log_cycle_detected(
            llm_id=caller_llm,
            call_chain=chain,
            trace_id=trace_id,
            session_id=session_id
        )
        return {
            "target": target,
            "success": False,
            "error": f"Cycle detected: {' -> '.join(chain)} -> {target}",
            "trace_id": trace_id
        }

    # 3. Rate Limiting
    if not rate_limiter.is_allowed(target):
        wait_time = rate_limiter.get_wait_time(target)
        await audit_logger.log_rate_limited(
            llm_id=target,
            current_rate=60,  # Approximate
            limit=rate_limiter.llm_limits.get(target.lower(), 60),
            trace_id=trace_id,
            session_id=session_id
        )
        return {
            "target": target,
            "success": False,
            "error": f"Rate limit exceeded. Wait {wait_time:.1f}s",
            "wait_seconds": wait_time,
            "trace_id": trace_id
        }

    # 4. Circuit Breaker Check
    actual_target = target
    fallback_used = None

    if not circuit_registry.is_available(target):
        fallback = circuit_registry.get_fallback(target)
        if fallback:
            actual_target = fallback
            fallback_used = fallback
            logger.info(f"Using fallback {fallback} for {target}")
        else:
            return {
                "target": target,
                "success": False,
                "error": f"Circuit open for {target}, no fallback available",
                "trace_id": trace_id
            }

    # 5. Resolve model ID
    model_id = get_model_id(actual_target)

    # 6. Build system prompt for mesh call
    system_prompt = """You are an LLM in the TriForce Full Mesh Network.
You were called by another LLM to help with a task.

Respond in this format:
=== RESPONSE ===
STATUS: success|partial|need_info
SUMMARY: [Brief summary of your response]
DETAILS: [Detailed response]
=== END RESPONSE ==="""

    if context:
        system_prompt += f"\n\nContext provided:\n{context}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    # 7. Make the actual LLM call
    try:
        # Import here to avoid circular imports
        from ..chat import stream_chat
        from ..model_registry import registry

        model = await registry.get_model(model_id)
        if not model:
            raise ValueError(f"Model {model_id} not found in registry")

        chunks = []
        async for chunk in stream_chat(
            model,
            model_id,
            iter(messages),
            stream=True,
            temperature=0.7,
        ):
            if chunk:
                chunks.append(chunk)

        response_text = "".join(chunks)
        execution_time_ms = (time.time() - start_time) * 1000

        # Record success
        circuit_registry.record_success(actual_target)

        # Log the call
        await audit_logger.log_llm_call(
            caller_llm=caller_llm,
            target_llm=actual_target,
            prompt_preview=prompt[:200],
            result_status="success",
            execution_time_ms=execution_time_ms,
            trace_id=trace_id,
            session_id=session_id
        )

        # Clean up cycle chain
        cycle_detector.pop_from_chain(trace_id)

        return {
            "target": target,
            "actual_target": actual_target,
            "success": True,
            "response": response_text,
            "model_id": model_id,
            "execution_time_ms": execution_time_ms,
            "fallback_used": fallback_used,
            "trace_id": trace_id
        }

    except asyncio.TimeoutError:
        execution_time_ms = timeout * 1000
        circuit_registry.record_failure(actual_target)

        await audit_logger.log_llm_call(
            caller_llm=caller_llm,
            target_llm=actual_target,
            prompt_preview=prompt[:200],
            result_status="timeout",
            execution_time_ms=execution_time_ms,
            trace_id=trace_id,
            session_id=session_id,
            error_message=f"Timeout after {timeout}s"
        )

        cycle_detector.pop_from_chain(trace_id)

        return {
            "target": target,
            "success": False,
            "error": f"Timeout after {timeout}s",
            "trace_id": trace_id
        }

    except Exception as e:
        execution_time_ms = (time.time() - start_time) * 1000
        circuit_registry.record_failure(actual_target)

        await audit_logger.log_llm_call(
            caller_llm=caller_llm,
            target_llm=actual_target,
            prompt_preview=prompt[:200],
            result_status="error",
            execution_time_ms=execution_time_ms,
            trace_id=trace_id,
            session_id=session_id,
            error_message=str(e)
        )

        cycle_detector.pop_from_chain(trace_id)
        logger.error(f"LLM call to {target} failed: {e}")

        return {
            "target": target,
            "success": False,
            "error": str(e),
            "trace_id": trace_id
        }


async def llm_broadcast(
    targets: List[str],
    prompt: str,
    caller_llm: str = "unknown",
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    timeout: int = 120
) -> Dict[str, Any]:
    """
    Send prompt to multiple LLMs in parallel.

    Args:
        targets: List of target LLM names
        prompt: Prompt to send to all
        caller_llm: ID of calling LLM
        trace_id: Trace ID for audit
        session_id: Session ID for audit
        timeout: Timeout per LLM

    Returns:
        Dict with all responses
    """
    trace_id = trace_id or str(uuid.uuid4())

    # Create tasks for all targets
    tasks = [
        llm_call(
            target=target,
            prompt=prompt,
            caller_llm=caller_llm,
            trace_id=trace_id,
            session_id=session_id,
            timeout=timeout
        )
        for target in targets
    ]

    # Execute all in parallel
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Process results
    responses = {}
    success_count = 0
    error_count = 0

    for target, result in zip(targets, results):
        if isinstance(result, Exception):
            responses[target] = {
                "success": False,
                "error": str(result)
            }
            error_count += 1
        else:
            responses[target] = result
            if result.get("success"):
                success_count += 1
            else:
                error_count += 1

    return {
        "broadcast": True,
        "targets": targets,
        "responses": responses,
        "success_count": success_count,
        "error_count": error_count,
        "trace_id": trace_id
    }


async def llm_consensus(
    targets: List[str],
    question: str,
    caller_llm: str = "unknown",
    weights: Optional[Dict[str, float]] = None,
    min_agreement: float = 0.6,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Get consensus from multiple LLMs on a question.

    Args:
        targets: List of LLMs to query
        question: Question to get consensus on
        caller_llm: ID of calling LLM
        weights: Optional weights per LLM (default 1.0)
        min_agreement: Minimum agreement threshold (0.0-1.0)
        trace_id: Trace ID for audit
        session_id: Session ID for audit

    Returns:
        Dict with individual responses and consensus analysis
    """
    trace_id = trace_id or str(uuid.uuid4())
    weights = weights or {}

    # Get responses from all targets
    broadcast = await llm_broadcast(
        targets=targets,
        prompt=question,
        caller_llm=caller_llm,
        trace_id=trace_id,
        session_id=session_id
    )

    # Filter successful responses
    successful = {
        t: r for t, r in broadcast["responses"].items()
        if r.get("success")
    }

    if len(successful) < 2:
        return {
            "question": question,
            "targets": targets,
            "consensus": None,
            "error": "Not enough successful responses for consensus",
            "individual_responses": broadcast["responses"],
            "trace_id": trace_id
        }

    # Build analysis prompt
    responses_text = "\n\n".join([
        f"=== {t} (weight: {weights.get(t, 1.0)}) ===\n{r.get('response', 'No response')}"
        for t, r in successful.items()
    ])

    analysis_prompt = f"""Analyze the following responses and find consensus:

QUESTION: {question}

RESPONSES:
{responses_text}

TASK:
1. AGREEMENT: What do all/most responses agree on?
2. DIFFERENCES: Where do they differ?
3. RECOMMENDATION: What's the best recommendation based on consensus?
4. AGREEMENT_SCORE: Rate the overall agreement from 0.0 to 1.0

Minimum required agreement: {min_agreement}

Format your response as:
AGREEMENT: ...
DIFFERENCES: ...
RECOMMENDATION: ...
AGREEMENT_SCORE: 0.X"""

    # Use gemini (or first available lead) for consensus analysis
    consensus_result = await llm_call(
        target="gemini",
        prompt=analysis_prompt,
        caller_llm=caller_llm,
        trace_id=trace_id,
        session_id=session_id
    )

    return {
        "question": question,
        "targets": targets,
        "individual_responses": broadcast["responses"],
        "consensus": consensus_result.get("response") if consensus_result.get("success") else None,
        "consensus_success": consensus_result.get("success", False),
        "success_count": broadcast["success_count"],
        "trace_id": trace_id
    }


async def llm_delegate(
    target: str,
    task_type: str,
    prompt: str,
    caller_llm: str = "unknown",
    context_files: Optional[List[str]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Delegate a specialized task to an LLM.

    Args:
        target: Target LLM (or "auto" for automatic selection)
        task_type: Type of task (coding|research|review|documentation)
        prompt: Task description
        caller_llm: ID of calling LLM
        context_files: List of file paths for context
        trace_id: Trace ID for audit
        session_id: Session ID for audit

    Returns:
        Dict with delegation result
    """
    trace_id = trace_id or str(uuid.uuid4())

    # Auto-select best LLM if requested
    if target.lower() == "auto":
        target = get_best_llm_for_task(task_type) or "gemini"

    # Build context with file contents if provided
    context = {"task_type": task_type}
    if context_files:
        context["files"] = context_files
        # Note: actual file reading would happen in tool executor

    # Build delegation prompt
    delegation_prompt = f"""DELEGATED TASK
Type: {task_type}
From: {caller_llm}

TASK:
{prompt}

Please complete this task thoroughly and return your results."""

    result = await llm_call(
        target=target,
        prompt=delegation_prompt,
        caller_llm=caller_llm,
        context=context,
        trace_id=trace_id,
        session_id=session_id,
        max_tokens=4096  # More tokens for complex tasks
    )

    result["task_type"] = task_type
    result["delegated"] = True
    return result


# Utility functions

def get_available_llms() -> List[str]:
    """Get list of currently available LLMs"""
    return [
        llm_id for llm_id in MODEL_ALIASES.keys()
        if circuit_registry.is_available(llm_id)
    ]


def get_llm_status() -> Dict[str, Any]:
    """Get status of all LLMs in the mesh"""
    status = {}
    for llm_id in MODEL_ALIASES.keys():
        breaker = circuit_registry.get_breaker(llm_id)
        usage = rate_limiter.get_current_usage(llm_id)
        status[llm_id] = {
            "model_id": MODEL_ALIASES[llm_id],
            "circuit_state": breaker.state.value,
            "available": breaker.is_available(),
            "rate_limit": usage,
            "specializations": LLM_SPECIALIZATIONS.get(llm_id, []),
            "fallback": breaker.get_fallback()
        }
    return status
