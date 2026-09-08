"""Telegram -> TriForce MCP agent bridge."""
from __future__ import annotations

from typing import Any, Dict, Optional
from pathlib import Path
import re

from fastapi import Request

from app.services.api_agent import run_api_agent, run_text_model
from app.utils.mcp_security import is_internal_full_request

OWNER_TOOLS = [
    "search", "current_time", "health", "status", "logs",
    "memory_search", "memory_store",
    "code_read", "code_search", "code_tree",
    "models", "specialist", "ollama_status", "ollama_list",
    "notify_list", "notify_send",
]

GROUP_TOOLS = [
    "search", "current_time", "health", "models", "ollama_status",
]


def _identity_label(params: Dict[str, Any]) -> str:
    username = str(params.get("telegram_username") or "").strip().lstrip("@")
    display_name = str(params.get("display_name") or "").strip()
    user_id = str(params.get("telegram_user_id") or "").strip()
    parts = []
    if display_name:
        parts.append(display_name)
    if username:
        parts.append(f"@{username}")
    if user_id:
        parts.append(f"Telegram-ID {user_id}")
    return " / ".join(parts) or "unbekannter Telegram-Nutzer"


_OWNER_HOME = Path("/home/zombie").resolve()
_ALLOWED_HIDDEN_OWNER_FILES = {".bashrc", ".profile", ".bash_profile", ".zshrc"}
_BLOCKED_OWNER_PATH_PARTS = {
    ".ssh", ".gnupg", ".aws", ".kube", ".password-store",
    "secrets", "credentials", "private_keys", "tokens",
}
_SECRET_LINE_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization|credential|bearer)"
)


def _extract_direct_file_request(message: str) -> str | None:
    patterns = [
        r"(?i)zeige\s+mir\s+(?:den\s+)?inhalt\s+von\s+[`'\"]?([^`'\"\s]+)",
        r"(?i)lies\s+(?:mir\s+)?(?:die\s+)?datei\s+[`'\"]?([^`'\"\s]+)",
        r"(?i)cat\s+[`'\"]?([^`'\"\s]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).rstrip(".,;:!?)]}")
    return None


def _owner_direct_file_read(requested_path: str) -> str:
    raw = requested_path.strip()
    if raw == "~":
        candidate = _OWNER_HOME
    elif raw.startswith("~/"):
        candidate = _OWNER_HOME / raw[2:]
    else:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = _OWNER_HOME / candidate
    resolved = candidate.resolve(strict=True)
    if resolved == _OWNER_HOME or _OWNER_HOME not in resolved.parents:
        raise ValueError("Pfad liegt ausserhalb des erlaubten Owner-Home-Verzeichnisses")

    parts_lower = {part.lower() for part in resolved.parts}
    if any(part in parts_lower for part in _BLOCKED_OWNER_PATH_PARTS):
        raise ValueError("Dieser Pfad ist aus Sicherheitsgruenden gesperrt")
    for part in resolved.relative_to(_OWNER_HOME).parts:
        if part.startswith(".") and part not in _ALLOWED_HIDDEN_OWNER_FILES:
            raise ValueError(f"Versteckter Pfad ist nicht freigegeben: {part}")
    if not resolved.is_file():
        raise ValueError("Pfad ist keine Datei")
    if resolved.stat().st_size > 200_000:
        raise ValueError("Datei ist zu gross fuer Telegram-Direktlesung (max. 200 KB)")

    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Datei ist keine UTF-8-Textdatei") from exc

    safe_lines = []
    redacted = 0
    for line in lines:
        if _SECRET_LINE_RE.search(line):
            safe_lines.append("[REDACTED: moegliche Zugangsdaten]")
            redacted += 1
        else:
            safe_lines.append(line)
    body = "\n".join(safe_lines)
    suffix = f"\n\n[Hinweis: {redacted} sensible Zeile(n) redigiert.]" if redacted else ""
    return f"{resolved}:\n\n{body}{suffix}"


async def handle_telegram_mcp_agent(
    params: Dict[str, Any],
    request: Optional[Request] = None,
) -> Dict[str, Any]:
    if request is not None and not is_internal_full_request(request):
        return {
            "status": "error",
            "error": "telegram_mcp_agent is restricted to trusted internal callers",
            "code": "INTERNAL_ONLY",
        }

    message = str(params.get("message") or "").strip()
    if not message:
        return {"status": "error", "error": "message is required"}

    model = str(params.get("model") or "nvidia/nvidia/nemotron-3-ultra-550b-a55b").strip()
    fallback_model = "ollama/nemotron-3-super:cloud"
    profile = str(params.get("profile") or "group").strip().lower()
    if profile not in {"owner", "group"}:
        profile = "group"

    telegram_user_id = str(params.get("telegram_user_id") or "").strip()
    telegram_username = str(params.get("telegram_username") or "").strip().lstrip("@")
    display_name = str(params.get("display_name") or "").strip()
    chat_id = str(params.get("chat_id") or "").strip()
    chat_title = str(params.get("chat_title") or "").strip()

    identity = _identity_label(params)
    tools = OWNER_TOOLS if profile == "owner" else GROUP_TOOLS

    identity_tags = ["telegram"]
    if telegram_user_id:
        identity_tags.append(f"telegram_user_id:{telegram_user_id}")
    if telegram_username:
        identity_tags.append(f"telegram_username:{telegram_username}")

    if profile == "owner":
        profile_rules = (
            "Dieser Nutzer ist der verifizierte Besitzer der Telegram-Bridge. "
            "Du darfst die bereitgestellten Owner-Tools benutzen. Destruktive oder nicht angebotene "
            "Operationen darfst du nicht erfinden. Wenn du memory_store nutzt, tagge nutzerbezogene "
            f"Erinnerungen mit mindestens {identity_tags!r}. Wenn du nach Erinnerungen zu diesem Nutzer "
            "suchst, bevorzuge diese Telegram-Tags. Gib niemals Secrets, Tokens oder Zugangsdaten aus."
        )
    else:
        profile_rules = (
            "Dieser Nutzer ist ein Teilnehmer einer freigegebenen Telegram-Gruppe, aber nicht der Owner. "
            "Nutze ausschließlich die angebotenen öffentlichen Read/Search-Tools. Keine privaten Memories, "
            "keine internen Dateien, keine Administration und keine schreibenden Aktionen."
        )

    system_prompt = (
        "Du bist Nova, betrieben durch Nemotron via TriForce MCP und eingebunden in Telegram. "
        "Nutze Tools aktiv, wenn die Frage aktuelle oder interne Daten benötigt, statt Werte zu erfinden. "
        f"Telegram-Identität: {identity}. Profil: {profile}. "
        f"Chat: {chat_title or '(privat)'} ({chat_id or 'unbekannt'}). "
        f"{profile_rules} "
        "Antworte in der Sprache des Nutzers, kompakt und natürlich für Telegram."
    )

    # Fast path: explicit owner file reads are deterministic and must not burn
    # ReAct turns trying alternate path spellings. Sensitive paths remain blocked.
    if profile == "owner":
        requested_file = _extract_direct_file_request(message)
        if requested_file:
            try:
                file_text = _owner_direct_file_read(requested_file)
                return {
                    "status": "completed",
                    "model": "direct-owner-file-read",
                    "response": file_text,
                    "turns": 0,
                    "tools_called": ["owner_direct_file_read"],
                    "elapsed_ms": 0,
                    "fast_path": True,
                    "planner_used": False,
                    "planner_model": None,
                    "executor_model": None,
                    "telegram": {
                        "profile": profile,
                        "user_id": telegram_user_id,
                        "username": telegram_username,
                        "display_name": display_name,
                        "chat_id": chat_id,
                        "chat_title": chat_title,
                    },
                    "tool_profile": tools,
                }
            except Exception as exc:
                return {
                    "status": "completed",
                    "model": "direct-owner-file-read",
                    "response": f"⚠️ Datei konnte nicht direkt gelesen werden: {str(exc)[:500]}",
                    "turns": 0,
                    "tools_called": ["owner_direct_file_read"],
                    "elapsed_ms": 0,
                    "fast_path": True,
                    "planner_used": False,
                    "planner_model": None,
                    "executor_model": None,
                    "telegram": {
                        "profile": profile,
                        "user_id": telegram_user_id,
                        "username": telegram_username,
                        "display_name": display_name,
                        "chat_id": chat_id,
                        "chat_title": chat_title,
                    },
                    "tool_profile": tools,
                }

    # Fast path: trivial smalltalk must not enter the MCP ReAct loop.
    # This avoids unnecessary memory/tool calls for greetings such as "hi".
    simple = message.strip().lower().rstrip("!?. ")
    simple_greetings = {
        "hi", "hey", "hallo", "moin", "servus", "hello",
        "hi nova", "hey nova", "hallo nova", "moin nova", "servus nova",
    }
    if simple in simple_greetings:
        direct = await run_text_model(
            model=fallback_model,
            task=message,
            system_prompt=(
                "Du bist Nova in Telegram. Antworte freundlich, kurz und direkt in der Sprache des Nutzers. "
                "Keine Tools, keine Memory-Suche und keine Meta-Erklaerungen fuer einfache Begruessungen."
            ),
            timeout=15,
        )
        if direct.get("status") == "completed" and str(direct.get("response") or "").strip():
            result = {
                "status": "completed",
                "model": model,
                "response": str(direct.get("response") or "").strip(),
                "turns": 1,
                "tools_called": [],
                "elapsed_ms": 0,
                "planner_used": True,
                "planner_model": model,
                "executor_model": None,
                "fast_path": True,
            }
            result["telegram"] = {
                "profile": profile,
                "user_id": telegram_user_id,
                "username": telegram_username,
                "display_name": display_name,
                "chat_id": chat_id,
                "chat_title": chat_title,
            }
            result["tool_profile"] = tools
            return result

    planner_prompt = (
        "Du bist die vorgeschaltete Planungsinstanz fuer einen Telegram-MCP-Agenten. "
        "Analysiere die Nutzeranfrage, erhalte die Absicht exakt und formuliere einen knappen Ausfuehrungsauftrag "
        "fuer den nachgelagerten Tool-Agenten. Antworte nicht selbst auf die Nutzerfrage. "
        "Erfinde keine Fakten oder Tool-Ergebnisse. Wenn keine Tools noetig sind, formuliere trotzdem nur den "
        "Ausfuehrungsauftrag."
    )
    planner = await run_text_model(
        model=model,
        task=message,
        system_prompt=planner_prompt,
        timeout=15,
    )
    planner_used = planner.get("status") == "completed" and bool(str(planner.get("response") or "").strip())
    execution_task = str(planner.get("response") or "").strip() if planner_used else message

    result = await run_api_agent(
        model=fallback_model,
        task=execution_task,
        tools=tools,
        system_prompt=system_prompt,
        max_turns=16,
        timeout=180,
    )
    result["planner_used"] = planner_used
    result["planner_model"] = model
    result["executor_model"] = fallback_model
    if not planner_used:
        result["planner_error"] = str(planner.get("response") or planner.get("error") or planner.get("status") or "unknown error")[:300]
    result["telegram"] = {
        "profile": profile,
        "user_id": telegram_user_id,
        "username": telegram_username,
        "display_name": display_name,
        "chat_id": chat_id,
        "chat_title": chat_title,
    }
    result["tool_profile"] = tools
    return result


TELEGRAM_AGENT_HANDLERS = {
    "telegram_mcp_agent": handle_telegram_mcp_agent,
}
