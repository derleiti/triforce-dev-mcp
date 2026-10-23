"""Safe, owner-scoped MCP read tools for the Nova Discord voice assistant.

The restricted voice broker invokes existing TriForce MCP handler implementations
with fixed arguments. Untrusted Whisper transcripts can never select arbitrary
MCP method names, file paths, shell commands, or write operations.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

READ_TOOLS = (
    "triforce_server_status",
    "triforce_model_catalog",
)
_TOOL_TIMEOUT_SECONDS = 14


def choose_read_tool(question: str) -> str | None:
    if not isinstance(question, str) or not 1 <= len(question) <= 550:
        return None
    text = question.casefold().strip()
    # Dangerous actions are never dispatched from untrusted voice transcripts.
    if re.search(r"\b(neu\s*starten|restart|reboot|löschen|loeschen|ändern|aendern|"
                 r"installieren|shell|terminal|sudo|rm\s+-|deploy|abschalten|"
                 r"herunterfahren|stoppen)\b", text):
        return None
    if re.search(r"\b(ki[- ]?modelle|ai[- ]?modelle|llm[- ]?modelle|modellkatalog|modelle)\b", text):
        if re.search(r"\b(triforce|mcp|ki|ai|llm|katalog|verfügbar|verfuegbar|"
                     r"registriert|auflisten|liste|wieviele|wie viele)\b", text):
            return "triforce_model_catalog"
    if re.search(r"\b(triforce|trai?force|hub|ailinux|server|backend)\b", text):
        if re.search(r"\b(status|serverstatus|backendstatus|systemstatus|zustand|läuft|laeuft|online|gesund|health|"
                     r"prüf|pruef|check|kontrollier|funktioniert)\b", text):
            return "triforce_server_status"
    return None


async def run_read_tool(tool_name: str) -> tuple[str, dict[str, Any]]:
    if tool_name not in READ_TOOLS:
        raise PermissionError("Discord voice tool not allowlisted")

    if tool_name == "triforce_server_status":
        from app.mcp.server_control import handle_server_control

        payload = await asyncio.wait_for(
            handle_server_control({"action": "status", "detail": "summary"}),
            timeout=_TOOL_TIMEOUT_SECONDS,
        )
        if not payload.get("ok"):
            raise RuntimeError("MCP server status returned no data")
        summary = payload.get("summary") or {}
        if not summary.get("ok"):
            raise RuntimeError("MCP host status probe failed")
        output = str(summary.get("stdout") or "")
        uptime = output.splitlines()[0].strip()[:78] if output else "Uptime unbekannt"
        memory = next((ln.strip() for ln in output.splitlines() if ln.startswith("Mem:")), "")
        available = memory.split()[-1] if len(memory.split()) >= 7 else "unbekannt"
        # Only expose narrow, non-sensitive aggregates; no process lists/logs.
        answer = (
            f"TriForce-MCP-Server auf {str(payload.get('hostname') or 'ailinux')[:50]} "
            f"antwortet. Laufzeit: {uptime}. Verfügbarer RAM: {available}. "
            "Das ist eine echte, nur lesende MCP-Statusabfrage."
        )
        return answer[:380], {"tool": tool_name, "read_only": True, "ok": True}

    from app.services.mcp_service import handle_models_list

    payload = await asyncio.wait_for(
        handle_models_list({}),
        timeout=_TOOL_TIMEOUT_SECONDS,
    )
    count = int((payload.get("summary") or {}).get("total_models", 0))
    if count <= 0:
        raise RuntimeError("MCP model catalog was empty")
    providers = (payload.get("summary") or {}).get("providers") or []
    names = ", ".join(str(x)[:25] for x in providers[:5])
    answer = (
        f"Ich habe den TriForce-MCP-Modellkatalog geprüft: "
        f"{count} Modelleinträge bei mehreren Anbietern, darunter {names}. "
        "Das sind Katalogeinträge, nicht automatisch getestete erreichbare Modelle."
    )
    return answer[:380], {"tool": tool_name, "read_only": True, "ok": True}


def describe_read_tools() -> str:
    return (
        "Im Discord darf ich derzeit zwei echte TriForce-MCP-Leseaktionen "
        "ausführen: den Hub-Serverstatus prüfen und den KI-Modellkatalog abfragen. "
        "Shell, Dateizugriff und Änderungen bleiben für Sprache gesperrt."
    )
