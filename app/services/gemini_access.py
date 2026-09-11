"""
Gemini Access Point Service
============================

Gemini als zentraler Access Point für Modelle mit:
- Interner Recherche vor Antworten
- Automatisches Memory-Update mit Erkenntnissen
- Koordination anderer LLMs
- Web-Search Integration
- Function Calling (v2.80) - Native Tool-Ausführung
- Code Execution (v2.80) - Sandbox-Code-Ausführung

Version: 2.80
"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from ..config import get_settings

logger = logging.getLogger("ailinux.gemini_access")

settings = get_settings()

# Optional: Google GenAI SDK für Function Calling
try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    genai = None
    genai_types = None
    logger.info("google-genai not installed, Gemini SDK features disabled")


class GeminiAccessPoint:
    """
    Gemini als Access Point für alle Modelle.

    Gemini gibt die Richtung vor, recherchiert intern und
    aktualisiert automatisch das Memory mit Erkenntnissen.

    Erweitert um Function Calling und Code Execution (v2.80).
    """

    def __init__(self):
        self._research_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 300  # 5 Minuten
        self._function_handlers: Dict[str, Callable[..., Awaitable[Any]]] = {}
        self._genai_initialized = False
        self._genai_client = None
        self._init_genai()

    def _init_genai(self):
        """Initialize Google GenAI with API key."""
        if not GENAI_AVAILABLE:
            logger.debug("GenAI SDK not available")
            return

        try:
            from .google_genai import create_client, resolve_api_key
            api_key = resolve_api_key()
            if api_key:
                self._genai_client = create_client(api_key)
                self._genai_initialized = True
                logger.info("Google Gen AI SDK initialized for Gemini / AI Studio")
        except Exception as e:
            logger.warning("Failed to initialize Google Gen AI SDK: %s", e)

    def register_function(self, name: str, handler: Callable[..., Awaitable[Any]]):
        """Register a function handler for Gemini to call."""
        self._function_handlers[name] = handler
        logger.debug(f"Registered function handler: {name}")

    async def _call_gemini(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
    ) -> str:
        """Call Gemini model directly."""
        from ..services import chat as chat_service
        from ..services.model_registry import registry

        model = await registry.get_model("gemini/gemini-2.5-flash")
        if not model:
            model = await registry.get_model("gemini/gemini-2.0-flash")
        if not model:
            raise ValueError("Gemini model not available")

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        chunks = []
        async for chunk in chat_service.stream_chat(
            model, model.id, iter(messages), stream=True, temperature=temperature
        ):
            chunks.append(chunk)

        return "".join(chunks)

    async def _internal_research(self, query: str) -> Dict[str, Any]:
        """
        Führt interne Recherche durch:
        - Memory durchsuchen
        - Ollama Modelle abfragen
        - System-Status prüfen
        - Optional: Web-Search
        """
        research_results = {
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sources": [],
        }

        # 1. Memory durchsuchen
        try:
            from ..services.tristar_mcp import tristar_mcp
            from ..routes.mcp_remote import TOOL_HANDLERS

            memory_handler = TOOL_HANDLERS.get("tristar.memory.search")
            if memory_handler:
                memory_result = await memory_handler({"query": query, "limit": 10})
                if memory_result.get("entries"):
                    research_results["memory"] = memory_result["entries"]
                    research_results["sources"].append("tristar_memory")
        except Exception as e:
            logger.warning(f"Memory search failed: {e}")

        # 2. System-Status prüfen wenn relevant
        if any(word in query.lower() for word in ["status", "system", "health", "service"]):
            try:
                status_result = await tristar_mcp.get_status()
                research_results["system_status"] = status_result
                research_results["sources"].append("system_status")
            except Exception as e:
                logger.warning(f"Status check failed: {e}")

        # 3. Ollama-Modelle prüfen wenn relevant
        if any(word in query.lower() for word in ["model", "modell", "ollama", "llm"]):
            try:
                from ..services.ollama_mcp import ollama_mcp
                models_result = await ollama_mcp.list_models()
                research_results["ollama_models"] = models_result.get("models", [])
                research_results["sources"].append("ollama")
            except Exception as e:
                logger.warning(f"Ollama check failed: {e}")

        # 4. Prompts prüfen wenn relevant
        if any(word in query.lower() for word in ["prompt", "agent", "konfig"]):
            try:
                prompts_result = await tristar_mcp.list_prompts()
                research_results["prompts"] = prompts_result.get("prompts", [])
                research_results["sources"].append("prompts")
            except Exception as e:
                logger.warning(f"Prompts check failed: {e}")

        # 5. Web-Search für externe Informationen
        if any(word in query.lower() for word in ["aktuell", "neu", "2024", "2025", "web", "search", "suche"]):
            try:
                from ..services import web_search
                search_results = await web_search.search_web(query)
                if search_results:
                    research_results["web_search"] = search_results[:5]
                    research_results["sources"].append("web_search")
            except Exception as e:
                logger.warning(f"Web search failed: {e}")

        return research_results

    async def _store_findings(
        self,
        findings: str,
        query: str,
        confidence: float = 0.8,
        tags: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Speichert Erkenntnisse im Memory."""
        try:
            from ..routes.mcp_remote import TOOL_HANDLERS

            memory_handler = TOOL_HANDLERS.get("tristar.memory.store")
            if memory_handler:
                result = await memory_handler({
                    "content": f"Gemini Research: {query}\n\nFindings:\n{findings}",
                    "memory_type": "summary",
                    "tags": tags or ["gemini-research", "auto-update"],
                    "initial_confidence": confidence,
                    "llm_id": "gemini-access",
                })
                return result.get("entry_id")
        except Exception as e:
            logger.warning(f"Failed to store findings: {e}")
        return None

    async def process_request(
        self,
        query: str,
        research: bool = True,
        store_findings: bool = True,
        include_context: bool = True,
    ) -> Dict[str, Any]:
        """
        Verarbeitet eine Anfrage mit optionaler Recherche und Auto-Update.

        Args:
            query: Die Benutzeranfrage
            research: Ob interne Recherche durchgeführt werden soll
            store_findings: Ob Erkenntnisse gespeichert werden sollen
            include_context: Ob Kontext in die Antwort einbezogen werden soll

        Returns:
            Dict mit Antwort, Recherche-Ergebnissen und Memory-Updates
        """
        result = {
            "query": query,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "research_performed": research,
        }

        # Schritt 1: Interne Recherche
        research_data = {}
        if research:
            research_data = await self._internal_research(query)
            result["research"] = research_data

        # Schritt 2: Gemini Prompt vorbereiten
        system_prompt = """Du bist Gemini, der zentrale Access Point im TriForce System.

Deine Aufgaben:
1. Analysiere die bereitgestellten Recherche-Ergebnisse
2. Beantworte die Anfrage basierend auf den Daten
3. Identifiziere wichtige Erkenntnisse zum Speichern
4. Schlage ggf. weitere Aktionen vor

Formatiere deine Antwort strukturiert mit Markdown."""

        user_prompt = f"## Anfrage\n{query}\n\n"

        if research_data and include_context:
            user_prompt += "## Recherche-Ergebnisse\n"

            if research_data.get("memory"):
                user_prompt += "\n### Aus Memory:\n"
                for entry in research_data["memory"][:5]:
                    user_prompt += f"- {entry.get('content', '')[:200]}...\n"

            if research_data.get("system_status"):
                status = research_data["system_status"]
                user_prompt += f"\n### System Status:\n"
                user_prompt += f"- Services: {status.get('services', {})}\n"

            if research_data.get("ollama_models"):
                user_prompt += f"\n### Verfügbare Ollama Modelle:\n"
                for m in research_data["ollama_models"][:5]:
                    user_prompt += f"- {m.get('name')}\n"

            if research_data.get("web_search"):
                user_prompt += f"\n### Web-Suche:\n"
                for r in research_data["web_search"][:3]:
                    user_prompt += f"- {r.get('title', '')}: {r.get('snippet', '')[:100]}...\n"

        # Schritt 3: Gemini aufrufen
        try:
            response = await self._call_gemini(user_prompt, system_prompt)
            result["response"] = response
            result["success"] = True
        except Exception as e:
            logger.error(f"Gemini call failed: {e}")
            result["response"] = f"Fehler bei Gemini-Aufruf: {e}"
            result["success"] = False
            return result

        # Schritt 4: Erkenntnisse speichern
        if store_findings and result["success"]:
            # Extrahiere wichtige Erkenntnisse
            findings = self._extract_key_findings(response)
            if findings:
                memory_id = await self._store_findings(
                    findings,
                    query,
                    confidence=0.85,
                    tags=["gemini-access", "auto-research"]
                )
                if memory_id:
                    result["memory_updated"] = memory_id

        return result

    def _extract_key_findings(self, response: str) -> Optional[str]:
        """Extrahiert wichtige Erkenntnisse aus einer Antwort."""
        # Suche nach Schlüsselabschnitten
        findings = []

        # Erkenntnisse, Zusammenfassung, Fazit
        patterns = [
            r"(?:Erkenntnis|Finding|Wichtig|Key|Summary|Zusammenfassung|Fazit)[:\s]*(.+?)(?:\n\n|\Z)",
            r"(?:\d+\.\s*)?(.+?)(?:ist wichtig|sollte beachtet|zu bemerken)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, response, re.IGNORECASE | re.DOTALL)
            findings.extend(matches)

        if findings:
            return "\n".join(f"- {f.strip()}" for f in findings[:5])

        # Fallback: Erste 500 Zeichen
        if len(response) > 100:
            return response[:500]

        return None

    async def coordinate_task(
        self,
        task: str,
        targets: Optional[List[str]] = None,
        strategy: str = "sequential",  # sequential, parallel, consensus
    ) -> Dict[str, Any]:
        """
        Gemini koordiniert eine Aufgabe mit anderen LLMs.

        Args:
            task: Die zu koordinierende Aufgabe
            targets: Liste der Ziel-LLMs (default: auto-select)
            strategy: Koordinationsstrategie
        """
        result = {
            "task": task,
            "strategy": strategy,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "responses": {},
        }

        # Auto-select targets basierend auf Aufgabe
        if not targets:
            targets = self._select_targets_for_task(task)

        result["targets"] = targets

        # Gemini gibt Richtung vor
        direction_prompt = f"""Analysiere diese Aufgabe und gib eine klare Richtung vor:

Aufgabe: {task}

Beteiligte LLMs: {', '.join(targets)}

Erstelle:
1. Klare Anweisungen für jedes LLM
2. Erwartete Ergebnisse
3. Wie die Ergebnisse zusammengeführt werden sollen"""

        try:
            direction = await self._call_gemini(direction_prompt)
            result["direction"] = direction
        except Exception as e:
            result["direction_error"] = str(e)
            return result

        # Führe Strategie aus
        if strategy == "parallel":
            result["responses"] = await self._parallel_execution(task, targets, direction)
        elif strategy == "consensus":
            result["responses"] = await self._consensus_execution(task, targets, direction)
        else:
            result["responses"] = await self._sequential_execution(task, targets, direction)

        # Gemini fasst zusammen
        summary_prompt = f"""Fasse die Ergebnisse zusammen:

Aufgabe: {task}
Richtung: {direction}

Ergebnisse:
{json.dumps(result['responses'], indent=2, ensure_ascii=False)[:3000]}

Erstelle eine strukturierte Zusammenfassung und identifiziere:
1. Haupterkenntnisse
2. Konsens und Abweichungen
3. Empfohlene nächste Schritte"""

        try:
            summary = await self._call_gemini(summary_prompt)
            result["summary"] = summary

            # Speichere Zusammenfassung
            await self._store_findings(
                summary,
                f"Task Coordination: {task}",
                confidence=0.9,
                tags=["coordination", "task-result"]
            )
        except Exception as e:
            result["summary_error"] = str(e)

        return result

    def _select_targets_for_task(self, task: str) -> List[str]:
        """Wählt automatisch passende LLMs für eine Aufgabe."""
        task_lower = task.lower()

        targets = []

        # Code-Aufgaben
        if any(w in task_lower for w in ["code", "implement", "program", "develop", "fix", "bug"]):
            targets.extend(["deepseek", "qwen-coder", "claude"])

        # Review-Aufgaben
        if any(w in task_lower for w in ["review", "check", "prüf", "audit", "security"]):
            targets.extend(["claude", "mistral", "cogito"])

        # Recherche-Aufgaben
        if any(w in task_lower for w in ["research", "recherch", "such", "find", "analys"]):
            targets.extend(["kimi", "nova"])

        # Kreative Aufgaben
        if any(w in task_lower for w in ["write", "schreib", "text", "content", "artikel"]):
            targets.extend(["claude", "mistral"])

        # Default
        if not targets:
            targets = ["claude", "deepseek", "kimi"]

        # Deduplizieren und limitieren
        return list(dict.fromkeys(targets))[:4]

    async def _parallel_execution(
        self,
        task: str,
        targets: List[str],
        direction: str,
    ) -> Dict[str, Any]:
        """Führt Task parallel auf allen Targets aus."""
        from ..services.triforce import llm_call

        async def call_llm(target: str) -> Tuple[str, Dict[str, Any]]:
            try:
                result = await llm_call(
                    target=target,
                    prompt=f"{direction}\n\nAufgabe: {task}",
                    caller_llm="gemini-access",
                    timeout=120
                )
                return target, result
            except Exception as e:
                return target, {"error": str(e)}

        tasks = [call_llm(t) for t in targets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {t: r for t, r in results if not isinstance(r, Exception)}

    async def _sequential_execution(
        self,
        task: str,
        targets: List[str],
        direction: str,
    ) -> Dict[str, Any]:
        """Führt Task sequentiell aus, jedes LLM baut auf dem vorherigen auf."""
        from ..services.triforce import llm_call

        results = {}
        context = direction

        for target in targets:
            try:
                result = await llm_call(
                    target=target,
                    prompt=f"Bisheriger Kontext:\n{context}\n\nDeine Aufgabe: {task}",
                    caller_llm="gemini-access",
                    timeout=120
                )
                results[target] = result

                # Update context für nächstes LLM
                if result.get("response"):
                    context += f"\n\n{target} Ergebnis: {result['response'][:500]}"
            except Exception as e:
                results[target] = {"error": str(e)}

        return results

    async def _consensus_execution(
        self,
        task: str,
        targets: List[str],
        direction: str,
    ) -> Dict[str, Any]:
        """Führt Task aus und sucht Konsens zwischen LLMs."""
        from ..services.triforce import llm_consensus

        try:
            result = await llm_consensus(
                targets=targets,
                question=f"{direction}\n\nFrage: {task}",
                caller_llm="gemini-access",
                min_agreement=0.6
            )
            return result
        except Exception as e:
            return {"error": str(e)}

    async def quick_research(self, topic: str) -> Dict[str, Any]:
        """
        Schnelle interne Recherche ohne Gemini-Aufruf.
        Gut für Status-Checks und System-Info.
        """
        return await self._internal_research(topic)

    async def update_memory(
        self,
        content: str,
        memory_type: str = "summary",
        tags: Optional[List[str]] = None,
        confidence: float = 0.8,
    ) -> Dict[str, Any]:
        """Direkter Memory-Update durch Gemini."""
        try:
            from ..routes.mcp_remote import TOOL_HANDLERS

            handler = TOOL_HANDLERS.get("tristar.memory.store")
            if handler:
                result = await handler({
                    "content": content,
                    "memory_type": memory_type,
                    "tags": tags or ["gemini-update"],
                    "initial_confidence": confidence,
                    "llm_id": "gemini-access",
                })
                return {"success": True, **result}
        except Exception as e:
            return {"success": False, "error": str(e)}

        return {"success": False, "error": "Handler not found"}

    # =========================================================================
    # FUNCTION CALLING (v2.80)
    # =========================================================================

    async def _call_model(
        self,
        prompt: str,
        model: str,
        tools: Optional[List[Any]] = None,
        timeout: int = 40,
        temperature: float = 0.2,
        max_retries: int = 2,
    ):
        """Call Gemini GenerativeModel with retries and timeout."""
        if not GENAI_AVAILABLE or not self._genai_initialized:
            raise RuntimeError("Gemini SDK not available or not initialized")

        last_error: Optional[Exception] = None

        for attempt in range(max_retries + 1):
            try:
                if self._genai_client is None:
                    raise RuntimeError("Google Gen AI client is not initialized")
                response = await asyncio.wait_for(
                    self._genai_client.aio.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=genai_types.GenerateContentConfig(
                            temperature=temperature,
                            tools=tools or None,
                            automatic_function_calling=(
                                genai_types.AutomaticFunctionCallingConfig(disable=True)
                                if tools else None
                            ),
                        ),
                    ),
                    timeout=timeout,
                )
                return response
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    "Gemini model call timed out on attempt %s/%s",
                    attempt + 1,
                    max_retries + 1,
                )
            except Exception as e:  # noqa: BLE001
                last_error = e
                logger.warning(
                    "Gemini model call failed on attempt %s/%s: %s",
                    attempt + 1,
                    max_retries + 1,
                    e,
                )

            await asyncio.sleep(1 + attempt)

        raise RuntimeError(f"Gemini model call failed: {last_error}")

    def _extract_function_payload(self, response_text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON payload for function calls from Gemini responses."""
        if not response_text:
            return None

        try:
            return json.loads(response_text)
        except (TypeError, json.JSONDecodeError):
            pass

        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                logger.debug("Regex JSON extraction failed", exc_info=True)

        return None

    async def _execute_function_calls(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Execute parsed function calls using registered handlers or MCP tools."""
        executed: List[Dict[str, Any]] = []

        try:
            from ..routes.mcp_remote import TOOL_HANDLERS
        except ImportError:
            TOOL_HANDLERS = {}

        for call in calls:
            name = call.get("name")
            args = call.get("args", {}) if isinstance(call, dict) else {}
            if not name:
                executed.append({"error": "Missing function name", "call": call})
                continue

            handler = self._function_handlers.get(name) if name else None
            if not handler:
                handler = TOOL_HANDLERS.get(name) if name else None

            if not handler:
                executed.append({"name": name, "error": "Handler not found"})
                continue

            try:
                result = await handler(args if isinstance(args, dict) else {})
                executed.append({"name": name, "result": result})
            except Exception as e:  # noqa: BLE001
                logger.warning("Function call %s failed: %s", name, e)
                executed.append({"name": name, "error": str(e)})

        return executed

    async def function_call(
        self,
        prompt: str,
        tools: Optional[List[str]] = None,
        auto_execute: bool = True,
        max_iterations: int = 5,
    ) -> Dict[str, Any]:
        """
        Execute Gemini with function calling support via SDK or fallback.
        """
        result: Dict[str, Any] = {
            "prompt": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "function_calls": [],
            "success": False,
            "max_iterations": max_iterations,
        }

        # Fallback if SDK is unavailable
        if not GENAI_AVAILABLE or not self._genai_initialized:
            return await self._function_call_fallback(prompt, tools, auto_execute, max_iterations)

        try:
            gemini_tools = self._triforce_tools_to_gemini(tools)
            response = await self._call_model(
                prompt=prompt,
                model="gemini-2.5-flash",
                tools=gemini_tools,
                timeout=60,
                temperature=0.3,
                max_retries=2,
            )

            raw_text = getattr(response, "text", None) or ""
            if not raw_text and hasattr(response, "to_dict"):
                raw_text = json.dumps(response.to_dict())

            result["response"] = raw_text

            payloads: List[Dict[str, Any]] = []
            extracted = self._extract_function_payload(raw_text)
            if extracted:
                payloads = extracted if isinstance(extracted, list) else [extracted]

            if payloads and auto_execute:
                executed = await self._execute_function_calls(payloads[:max_iterations])
                result["function_calls"] = executed
                result["function_calls_found"] = len(payloads)

            result["success"] = True
            result["fallback_mode"] = False
        except Exception as e:  # noqa: BLE001
            logger.error("Gemini function_call failed: %s", e)
            result["error"] = str(e)
            fallback = await self._function_call_fallback(prompt, tools, auto_execute, max_iterations)
            result.update({k: v for k, v in fallback.items() if k not in result or not result[k]})

        return result

    def _triforce_tools_to_gemini(self, tool_names: Optional[List[str]] = None) -> List[Any]:
        """
        Convert TriForce tools to Gemini FunctionDeclarations.

        Args:
            tool_names: Specific tools to include, or None for all
        """
        if not GENAI_AVAILABLE:
            return []

        try:
            from ..services.triforce.tool_registry import TOOL_INDEX, get_tool_by_name
        except ImportError:
            logger.warning("Tool registry not available")
            return []

        declarations = []
        tools_to_convert = tool_names or [t["name"] for t in TOOL_INDEX.get("tools", [])]

        for tool_name in tools_to_convert:
            tool = get_tool_by_name(tool_name)
            if not tool:
                continue

            # Convert params to Gemini schema (JSON Schema)
            properties = {}
            required = []

            type_map = {
                "string": "string",
                "str": "string",
                "int": "integer",
                "integer": "integer",
                "float": "number",
                "number": "number",
                "bool": "boolean",
                "boolean": "boolean",
                "array": "array",
                "list": "array",
                "object": "object",
                "dict": "object",
            }

            for param_name, param_def in tool.get("params", {}).items():
                param_type = str(param_def.get("type", "string")).lower()

                gemini_type = type_map.get(param_type, "string")
                
                prop = {
                    "type": gemini_type,
                    "description": param_def.get("description", ""),
                }
                
                # Fix: Arrays need items definition
                if gemini_type == "array":
                    # Try to get item type from definition, default to STRING
                    items_def = param_def.get("items", {})
                    if isinstance(items_def, dict):
                        item_type_str = items_def.get("type", "string")
                    else:
                        item_type_str = "string"
                    
                    prop["items"] = {"type": type_map.get(str(item_type_str).lower(), "string")}
                
                properties[param_name] = prop

                if param_def.get("required"):
                    required.append(param_name)

            try:
                # Ensure correct schema structure for FunctionDeclaration
                parameters_schema: Dict[str, Any] = {
                    "type": "object",
                    "properties": properties,
                }
                if required:
                    parameters_schema["required"] = required

                declaration = genai_types.FunctionDeclaration(
                    name=tool_name,
                    description=tool.get("description", ""),
                    parameters_json_schema=parameters_schema,
                )
                declarations.append(declaration)
            except Exception as e:
                logger.warning(f"Failed to create declaration for {tool_name}: {e}")

        if declarations:
            return [genai_types.Tool(function_declarations=declarations)]
        return []

    async def function_call(
        self,
        prompt: str,
        tools: Optional[List[str]] = None,
        auto_execute: bool = True,
        max_iterations: int = 5,
    ) -> Dict[str, Any]:
        """
        Execute Gemini with function calling (GenAI SDK) and optional execution of calls.
        Falls back to prompt-based function calling when SDK is unavailable.
        """
        result: Dict[str, Any] = {
            "prompt": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "function_calls": [],
            "success": False,
            "fallback_mode": False,
        }

        if not GENAI_AVAILABLE or not self._genai_initialized:
            logger.info("GenAI SDK unavailable, using fallback function calling")
            return await self._function_call_fallback(prompt, tools, auto_execute, max_iterations)

        try:
            tool_declarations = self._triforce_tools_to_gemini(tools)
            response = await self._call_model(
                prompt=prompt,
                model="gemini-2.5-flash",
                tools=tool_declarations,
                temperature=0.2,
                timeout=30,
                max_retries=1,
            )

            function_calls: List[Dict[str, Any]] = []
            for candidate in getattr(response, "candidates", []) or []:
                content = getattr(candidate, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    fn_call = getattr(part, "function_call", None)
                    if fn_call:
                        function_calls.append({
                            "name": getattr(fn_call, "name", None),
                            "args": getattr(fn_call, "args", {}) or {},
                        })

            if not function_calls:
                payload = self._extract_function_payload(getattr(response, "text", "") or "")
                if isinstance(payload, dict):
                    function_calls = [payload]
                elif isinstance(payload, list):
                    function_calls = [p for p in payload if isinstance(p, dict)]

            try:
                result["raw_response"] = response.text
            except ValueError:
                # Response contains function_call, not text - this is expected
                result["raw_response"] = ""
            result["function_calls_found"] = len(function_calls)

            if function_calls and auto_execute:
                result["function_calls"] = await self._execute_function_calls(
                    function_calls[:max_iterations]
                )
            else:
                result["function_calls"] = function_calls

            result["success"] = True
            return result
        except Exception as exc:
            logger.error("Gemini function_call failed, falling back: %s", exc, exc_info=True)
            return await self._function_call_fallback(prompt, tools, auto_execute, max_iterations)

    async def _function_call_fallback(
        self,
        prompt: str,
        tools: Optional[List[str]],
        auto_execute: bool,
        max_iterations: int,
    ) -> Dict[str, Any]:
        """
        Fallback function calling without GenAI SDK.
        Uses regular Gemini with tool descriptions in the prompt.
        """
        result = {
            "prompt": prompt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "function_calls": [],
            "success": False,
            "fallback_mode": True,
        }

        try:
            # Get tool descriptions
            tool_info = ""
            if tools:
                try:
                    from ..services.triforce.tool_registry import get_tool_by_name
                    for tool_name in tools[:5]:
                        tool = get_tool_by_name(tool_name)
                        if tool:
                            tool_info += f"- {tool_name}: {tool.get('description', '')}\n"
                except ImportError:
                    pass

            system_prompt = """Du bist Gemini mit Tool-Ausführung.
Wenn du ein Tool aufrufen möchtest, antworte im Format:
@TOOL_CALL: {"name": "tool_name", "args": {"param": "value"}}

Verfügbare Tools:
""" + (tool_info or "- memory_recall: Durchsucht das Memory\n- web_search: Websuche\n")

            response = await self._call_gemini(prompt, system_prompt)
            result["response"] = response

            # Robust parsing of tool calls
            calls_found = []

            payload = self._extract_function_payload(response)
            if isinstance(payload, dict):
                calls_found.append(payload)
            elif isinstance(payload, list):
                calls_found.extend([p for p in payload if isinstance(p, dict)])
            
            # Pattern to find start of tool call
            start_marker = "@TOOL_CALL:"
            start_indices = [m.start() for m in re.finditer(re.escape(start_marker), response)]
            
            for start_idx in start_indices:
                # Find the first '{' after the marker
                json_start = response.find('{', start_idx)
                if json_start == -1:
                    continue
                
                # Count braces to find matching '}'
                balance = 0
                for i in range(json_start, len(response)):
                    if response[i] == '{':
                        balance += 1
                    elif response[i] == '}':
                        balance -= 1
                        if balance == 0:
                            # Found potential JSON end
                            json_str = response[json_start:i+1]
                            try:
                                call_data = json.loads(json_str)
                                if "name" in call_data:
                                    calls_found.append({
                                        "name": call_data.get("name"),
                                        "args": call_data.get("args", {})
                                    })
                            except json.JSONDecodeError:
                                pass
                            break

            if calls_found and auto_execute:
                result["function_calls_found"] = len(calls_found)
                # Execute only the first few to avoid loops
                executed = await self._execute_function_calls(calls_found[:3])
                result["function_calls"].extend(executed)

            result["success"] = True

        except Exception as e:
            logger.error(f"Function call fallback failed: {e}")
            result["error"] = str(e)

        return result

    # =========================================================================
    # CODE EXECUTION (v2.80)
    # =========================================================================

    async def code_execution(
        self,
        code: str,
        language: str = "python",
        context: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Execute code via Gemini's native code execution capability.

        Note: Gemini 2.0 supports native Python code execution in a sandbox.
        Falls back to local execution if not available.

        Args:
            code: Code to execute
            language: Programming language (currently only 'python' supported)
            context: Optional context/description
            timeout: Execution timeout in seconds

        Returns:
            Dict with output, errors, and execution details
        """
        result = {
            "code": code,
            "language": language,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
        }

        if language.lower() != "python":
            result["error"] = f"Language '{language}' not supported. Only Python is available."
            return result

        # Try Gemini native code execution first
        if GENAI_AVAILABLE and self._genai_initialized:
            try:
                gemini_result = await self._gemini_code_exec(code, context)
                if gemini_result.get("success"):
                    return gemini_result
            except Exception as e:
                logger.warning(f"Gemini code exec failed, trying fallback: {e}")

        # Fallback: Local execution via code_exec tool
        try:
            result = await self._local_code_exec(code, timeout)
        except Exception as e:
            result["error"] = str(e)

        return result

    async def _gemini_code_exec(self, code: str, context: Optional[str]) -> Dict[str, Any]:
        """Execute code using Gemini's native code execution."""
        result = {
            "code": code,
            "language": "python",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "execution_mode": "gemini_native",
        }

        try:
            if self._genai_client is None:
                raise RuntimeError("Google Gen AI client is not initialized")
            code_tool = genai_types.Tool(code_execution=genai_types.ToolCodeExecution())

            prompt = f"""Execute this Python code and return the results:

```python
{code}
```

{f"Context: {context}" if context else ""}

Return the output, any errors, and explain what the code does."""

            response = await self._genai_client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=genai_types.GenerateContentConfig(tools=[code_tool]),
            )

            # Parse response for code execution results
            output_text = response.text
            result["response"] = output_text

            # Check for execution results in parts
            for part in response.parts:
                if hasattr(part, 'executable_code'):
                    result["executed_code"] = part.executable_code.code
                if hasattr(part, 'code_execution_result'):
                    execution_result = part.code_execution_result
                    result["stdout"] = execution_result.output if hasattr(execution_result, 'output') else ""
                    result["outcome"] = execution_result.outcome.name if hasattr(execution_result, 'outcome') else "UNKNOWN"

            result["success"] = True
            logger.info("Gemini native code execution completed")

        except Exception as e:
            logger.error(f"Gemini code execution failed: {e}")
            result["error"] = str(e)

        return result

    async def _local_code_exec(self, code: str, timeout: int) -> Dict[str, Any]:
        """Execute code locally via the code_exec tool."""
        result = {
            "code": code,
            "language": "python",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "execution_mode": "local_sandbox",
        }

        try:
            from ..routes.mcp_remote import TOOL_HANDLERS

            handler = TOOL_HANDLERS.get("code_exec")
            if handler:
                exec_result = await handler({
                    "language": "python",
                    "code": code,
                    "timeout": timeout,
                })
                result["stdout"] = exec_result.get("stdout", "")
                result["stderr"] = exec_result.get("stderr", "")
                result["return_code"] = exec_result.get("return_code", -1)
                result["success"] = exec_result.get("return_code", -1) == 0
            else:
                # SECURITY: Direct execution is DISABLED - too dangerous without sandbox
                # Original code executed arbitrary Python without isolation
                # To re-enable, implement proper sandboxing (Docker, nsjail, or firejail)
                logger.warning("Local code execution attempted but disabled for security")
                result["error"] = "Local code execution is disabled. Use Gemini's native code execution instead."
                result["stdout"] = ""
                result["stderr"] = "SECURITY: Direct code execution without sandbox is not allowed."
                result["return_code"] = -1
                result["success"] = False

        except asyncio.TimeoutError:
            result["error"] = f"Execution timed out after {timeout} seconds"
        except Exception as e:
            result["error"] = str(e)

        return result


# Singleton instance
gemini_access = GeminiAccessPoint()


# ============================================================================
# MCP Tool Definitions
# ============================================================================

GEMINI_ACCESS_TOOLS = [
    {
        "name": "gemini_research",
        "description": "Gemini führt interne Recherche durch (Memory, System, Ollama, Web) und antwortet mit Kontext",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Die Recherche-Anfrage"},
                "store_findings": {"type": "boolean", "default": True, "description": "Erkenntnisse speichern"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gemini_coordinate",
        "description": "Gemini koordiniert eine Aufgabe mit mehreren LLMs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "Die zu koordinierende Aufgabe"},
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Ziel-LLMs (optional, auto-select wenn leer)",
                },
                "strategy": {
                    "type": "string",
                    "enum": ["sequential", "parallel", "consensus"],
                    "default": "sequential",
                },
            },
            "required": ["task"],
        },
    },
    {
        "name": "gemini_quick",
        "description": "Schnelle interne Recherche ohne LLM-Aufruf (Status, Memory, Models)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Das Thema für die Recherche"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "gemini_update",
        "description": "Gemini aktualisiert das Memory mit neuen Informationen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Zu speichernder Inhalt"},
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "decision", "code", "summary", "context", "todo"],
                    "default": "summary",
                },
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.8},
            },
            "required": ["content"],
        },
    },
    # =========================================================================
    # NEW: Function Calling & Code Execution Tools (v2.80)
    # =========================================================================
    {
        "name": "gemini_function_call",
        "description": "Execute Gemini with function calling - Gemini can call TriForce tools autonomously",
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The task/question for Gemini"},
                "tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "TriForce tools to expose (default: memory, web_search, llm_call, code_exec)",
                },
                "auto_execute": {
                    "type": "boolean",
                    "default": True,
                    "description": "Auto-execute function calls",
                },
                "max_iterations": {
                    "type": "integer",
                    "default": 5,
                    "description": "Max function call rounds",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "gemini_code_exec",
        "description": "Execute Python code in Gemini's secure sandbox or local fallback",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"},
                "context": {"type": "string", "description": "Optional context/description"},
                "timeout": {"type": "integer", "default": 30, "description": "Timeout in seconds"},
            },
            "required": ["code"],
        },
    },
]


# ============================================================================
# MCP Tool Handlers
# ============================================================================

async def handle_gemini_research(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini.research tool."""
    query = arguments.get("query")
    if not query:
        raise ValueError("'query' is required")
    store = arguments.get("store_findings", True)
    return await gemini_access.process_request(query, research=True, store_findings=store)


async def handle_gemini_coordinate(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini.coordinate tool."""
    task = arguments.get("task")
    if not task:
        raise ValueError("'task' is required")
    targets = arguments.get("targets")
    strategy = arguments.get("strategy", "sequential")
    return await gemini_access.coordinate_task(task, targets, strategy)


async def handle_gemini_quick(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini.quick tool."""
    topic = arguments.get("topic")
    if not topic:
        raise ValueError("'topic' is required")
    return await gemini_access.quick_research(topic)


async def handle_gemini_update(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini.update tool."""
    content = arguments.get("content")
    if not content:
        raise ValueError("'content' is required")
    return await gemini_access.update_memory(
        content,
        memory_type=arguments.get("memory_type", "summary"),
        tags=arguments.get("tags"),
        confidence=arguments.get("confidence", 0.8),
    )


async def handle_gemini_function_call(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini_function_call tool - Execute Gemini with function calling."""
    prompt = arguments.get("prompt")
    if not prompt:
        raise ValueError("'prompt' is required")

    return await gemini_access.function_call(
        prompt=prompt,
        tools=arguments.get("tools"),
        auto_execute=arguments.get("auto_execute", True),
        max_iterations=arguments.get("max_iterations", 5),
    )


async def handle_gemini_code_exec(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Handle gemini_code_exec tool - Execute Python code."""
    code = arguments.get("code")
    if not code:
        raise ValueError("'code' is required")

    return await gemini_access.code_execution(
        code=code,
        context=arguments.get("context"),
        timeout=arguments.get("timeout", 30),
    )


GEMINI_ACCESS_HANDLERS = {
    "gemini_research": handle_gemini_research,
    "gemini_coordinate": handle_gemini_coordinate,
    "gemini_quick": handle_gemini_quick,
    "gemini_update": handle_gemini_update,
    # NEW: Function Calling & Code Execution (v2.80)
    "gemini_function_call": handle_gemini_function_call,
    "gemini_code_exec": handle_gemini_code_exec,
}


# Backward-compatible aliases used by mesh/group-chat services.
# Reuse the canonical singleton above so SDK/client initialization happens once.
gemini_access_point = gemini_access
gemini_service = gemini_access
