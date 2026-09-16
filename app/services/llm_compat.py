"""
LLM Compatibility Layer v1.0
============================

Universeller Compatibility Layer für alle LLM-Provider.
Ermöglicht einheitliche Tool-Nutzung über:
- OpenAI (GPT-4, GPT-4o, GPT-4-turbo)
- Google (Gemini Pro, Gemini Flash)
- Anthropic (Claude 3, Claude 3.5)
- Ollama (lokale Modelle)
- Mistral
- DeepSeek
- Qwen

Features:
- Automatische Format-Konvertierung
- Tool-Schema-Normalisierung
- Response-Parsing für alle Provider
- Einheitliches Error-Handling
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger("ailinux.llm_compat")


class LLMProvider(Enum):
    """Unterstützte LLM-Provider"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    OLLAMA = "ollama"
    MISTRAL = "mistral"
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    UNKNOWN = "unknown"


@dataclass
class ToolCall:
    """Normalisierter Tool-Aufruf"""
    name: str
    arguments: Dict[str, Any]
    id: Optional[str] = None
    raw: Optional[Dict] = None


@dataclass
class ToolResult:
    """Normalisiertes Tool-Ergebnis"""
    tool_call_id: str
    content: Any
    is_error: bool = False


@dataclass
class NormalizedMessage:
    """Normalisierte Nachricht für alle Provider"""
    role: str  # system, user, assistant, tool
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    tool_results: List[ToolResult] = field(default_factory=list)
    raw: Optional[Dict] = None


class ToolSchemaConverter:
    """
    Konvertiert Tool-Schemas zwischen verschiedenen Formaten.

    Unterstützte Formate:
    - MCP (Model Context Protocol)
    - OpenAI Function Calling
    - Anthropic Tool Use
    - Google Gemini Tools
    """

    @staticmethod
    def mcp_to_openai(mcp_tool: Dict) -> Dict:
        """Konvertiert MCP-Tool zu OpenAI Function-Format"""
        return {
            "type": "function",
            "function": {
                "name": mcp_tool["name"],
                "description": mcp_tool.get("description", ""),
                "parameters": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
            }
        }

    @staticmethod
    def mcp_to_anthropic(mcp_tool: Dict) -> Dict:
        """Konvertiert MCP-Tool zu Anthropic Tool-Format"""
        return {
            "name": mcp_tool["name"],
            "description": mcp_tool.get("description", ""),
            "input_schema": mcp_tool.get("inputSchema", {"type": "object", "properties": {}}),
        }

    @staticmethod
    def mcp_to_gemini(mcp_tool: Dict) -> Dict:
        """Konvertiert MCP-Tool zu Gemini FunctionDeclaration-Format"""
        schema = mcp_tool.get("inputSchema", {"type": "object", "properties": {}})

        # Gemini erwartet leicht anderes Format
        return {
            "name": mcp_tool["name"],
            "description": mcp_tool.get("description", ""),
            "parameters": {
                "type": "object",
                "properties": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
        }

    @staticmethod
    def openai_to_mcp(openai_tool: Dict) -> Dict:
        """Konvertiert OpenAI Function zu MCP-Format"""
        func = openai_tool.get("function", openai_tool)
        return {
            "name": func["name"],
            "description": func.get("description", ""),
            "inputSchema": func.get("parameters", {"type": "object", "properties": {}}),
        }

    @classmethod
    def convert_tools(
        cls,
        tools: List[Dict],
        from_format: str = "mcp",
        to_format: str = "openai"
    ) -> List[Dict]:
        """
        Konvertiert eine Liste von Tools zwischen Formaten.

        Formate: mcp, openai, anthropic, gemini
        """
        converters = {
            ("mcp", "openai"): cls.mcp_to_openai,
            ("mcp", "anthropic"): cls.mcp_to_anthropic,
            ("mcp", "gemini"): cls.mcp_to_gemini,
            ("openai", "mcp"): cls.openai_to_mcp,
        }

        key = (from_format, to_format)
        if key not in converters:
            logger.warning(f"No converter for {from_format} -> {to_format}")
            return tools

        converter = converters[key]
        return [converter(tool) for tool in tools]


class ResponseParser:
    """
    Parst Tool-Aufrufe aus verschiedenen LLM-Response-Formaten.
    """

    @staticmethod
    def parse_openai_response(response: Dict) -> List[ToolCall]:
        """Parst OpenAI-Response auf Tool-Calls"""
        tool_calls = []

        # Neues Format (GPT-4, GPT-4o)
        message = response.get("choices", [{}])[0].get("message", {})
        if "tool_calls" in message:
            for tc in message["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}

                tool_calls.append(ToolCall(
                    name=tc["function"]["name"],
                    arguments=args,
                    id=tc.get("id"),
                    raw=tc
                ))

        # Legacy function_call Format
        elif "function_call" in message:
            fc = message["function_call"]
            try:
                args = json.loads(fc.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            tool_calls.append(ToolCall(
                name=fc["name"],
                arguments=args,
                raw=fc
            ))

        return tool_calls

    @staticmethod
    def parse_anthropic_response(response: Dict) -> List[ToolCall]:
        """Parst Anthropic-Response auf Tool-Uses"""
        tool_calls = []

        for block in response.get("content", []):
            if block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    name=block["name"],
                    arguments=block.get("input", {}),
                    id=block.get("id"),
                    raw=block
                ))

        return tool_calls

    @staticmethod
    def parse_gemini_response(response: Dict) -> List[ToolCall]:
        """Parst Gemini-Response auf Function-Calls"""
        tool_calls = []

        # Gemini SDK Format
        candidates = response.get("candidates", [])
        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                if "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls.append(ToolCall(
                        name=fc["name"],
                        arguments=fc.get("args", {}),
                        raw=fc
                    ))

        return tool_calls

    @staticmethod
    def parse_ollama_response(response: Dict) -> List[ToolCall]:
        """Parst Ollama-Response (OpenAI-kompatibel)"""
        # Ollama nutzt meist OpenAI-Format
        return ResponseParser.parse_openai_response(response)

    @classmethod
    def parse(cls, response: Dict, provider: LLMProvider) -> List[ToolCall]:
        """Universeller Parser für alle Provider"""
        parsers = {
            LLMProvider.OPENAI: cls.parse_openai_response,
            LLMProvider.ANTHROPIC: cls.parse_anthropic_response,
            LLMProvider.GOOGLE: cls.parse_gemini_response,
            LLMProvider.OLLAMA: cls.parse_ollama_response,
            LLMProvider.MISTRAL: cls.parse_openai_response,  # OpenAI-kompatibel
            LLMProvider.DEEPSEEK: cls.parse_openai_response,  # OpenAI-kompatibel
            LLMProvider.QWEN: cls.parse_openai_response,  # OpenAI-kompatibel
        }

        parser = parsers.get(provider, cls.parse_openai_response)
        return parser(response)


class MessageFormatter:
    """
    Formatiert Nachrichten für verschiedene LLM-APIs.
    """

    @staticmethod
    def format_for_openai(messages: List[NormalizedMessage], tools: List[Dict]) -> Dict:
        """Formatiert Request für OpenAI API"""
        formatted_messages = []

        for msg in messages:
            formatted = {"role": msg.role}

            if msg.content:
                formatted["content"] = msg.content

            if msg.tool_calls:
                formatted["tool_calls"] = [
                    {
                        "id": tc.id or f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments)
                        }
                    }
                    for i, tc in enumerate(msg.tool_calls)
                ]

            if msg.tool_results:
                for tr in msg.tool_results:
                    formatted_messages.append({
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": json.dumps(tr.content) if not isinstance(tr.content, str) else tr.content
                    })
                continue

            formatted_messages.append(formatted)

        return {
            "messages": formatted_messages,
            "tools": ToolSchemaConverter.convert_tools(tools, "mcp", "openai") if tools else None,
        }

    @staticmethod
    def format_for_anthropic(messages: List[NormalizedMessage], tools: List[Dict]) -> Dict:
        """Formatiert Request für Anthropic API"""
        system_prompt = None
        formatted_messages = []

        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
                continue

            content = []

            if msg.content:
                content.append({"type": "text", "text": msg.content})

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id or f"toolu_{hash(tc.name)}",
                        "name": tc.name,
                        "input": tc.arguments
                    })

            if msg.tool_results:
                for tr in msg.tool_results:
                    content.append({
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": json.dumps(tr.content) if not isinstance(tr.content, str) else tr.content,
                        "is_error": tr.is_error
                    })

            formatted_messages.append({
                "role": msg.role,
                "content": content if len(content) > 1 else content[0] if content else ""
            })

        result = {"messages": formatted_messages}
        if system_prompt:
            result["system"] = system_prompt
        if tools:
            result["tools"] = ToolSchemaConverter.convert_tools(tools, "mcp", "anthropic")

        return result

    @staticmethod
    def format_for_gemini(messages: List[NormalizedMessage], tools: List[Dict]) -> Dict:
        """Formatiert Request für Gemini API"""
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
                continue

            parts = []

            if msg.content:
                parts.append({"text": msg.content})

            if msg.tool_calls:
                for tc in msg.tool_calls:
                    parts.append({
                        "functionCall": {
                            "name": tc.name,
                            "args": tc.arguments
                        }
                    })

            if msg.tool_results:
                for tr in msg.tool_results:
                    parts.append({
                        "functionResponse": {
                            "name": tr.tool_call_id,  # Gemini verwendet name statt id
                            "response": {"result": tr.content}
                        }
                    })

            role = "model" if msg.role == "assistant" else msg.role
            contents.append({"role": role, "parts": parts})

        result = {"contents": contents}
        if system_instruction:
            result["system_instruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            result["tools"] = [{
                "function_declarations": ToolSchemaConverter.convert_tools(tools, "mcp", "gemini")
            }]

        return result


class LLMCompatibilityLayer:
    """
    Hauptklasse für universelle LLM-Kompatibilität.

    Verwendung:
        compat = LLMCompatibilityLayer()

        # Tools konvertieren
        openai_tools = compat.convert_tools(mcp_tools, to_provider=LLMProvider.OPENAI)

        # Request formatieren
        request = compat.format_request(messages, tools, LLMProvider.ANTHROPIC)

        # Response parsen
        tool_calls = compat.parse_response(response, LLMProvider.GOOGLE)
    """

    def __init__(self):
        self.schema_converter = ToolSchemaConverter()
        self.response_parser = ResponseParser()
        self.message_formatter = MessageFormatter()

    def detect_provider(self, model_id: str) -> LLMProvider:
        """Erkennt Provider anhand der Model-ID"""
        model_lower = model_id.lower()

        if any(x in model_lower for x in ["gpt-4", "gpt-3.5", "o1", "chatgpt"]):
            return LLMProvider.OPENAI
        elif any(x in model_lower for x in ["claude", "anthropic"]):
            return LLMProvider.ANTHROPIC
        elif any(x in model_lower for x in ["gemini", "palm"]):
            return LLMProvider.GOOGLE
        elif any(x in model_lower for x in ["llama", "mistral", "phi", "qwen"]):
            return LLMProvider.OLLAMA
        elif "deepseek" in model_lower:
            return LLMProvider.DEEPSEEK
        elif "mistral" in model_lower:
            return LLMProvider.MISTRAL
        elif "qwen" in model_lower:
            return LLMProvider.QWEN

        return LLMProvider.UNKNOWN

    def convert_tools(
        self,
        tools: List[Dict],
        from_format: str = "mcp",
        to_provider: LLMProvider = LLMProvider.OPENAI
    ) -> List[Dict]:
        """Konvertiert Tools für einen spezifischen Provider"""
        format_map = {
            LLMProvider.OPENAI: "openai",
            LLMProvider.ANTHROPIC: "anthropic",
            LLMProvider.GOOGLE: "gemini",
            LLMProvider.OLLAMA: "openai",  # OpenAI-kompatibel
            LLMProvider.MISTRAL: "openai",
            LLMProvider.DEEPSEEK: "openai",
            LLMProvider.QWEN: "openai",
        }

        to_format = format_map.get(to_provider, "openai")
        return ToolSchemaConverter.convert_tools(tools, from_format, to_format)

    def format_request(
        self,
        messages: List[NormalizedMessage],
        tools: List[Dict],
        provider: LLMProvider
    ) -> Dict:
        """Formatiert Request für einen spezifischen Provider"""
        formatters = {
            LLMProvider.OPENAI: MessageFormatter.format_for_openai,
            LLMProvider.ANTHROPIC: MessageFormatter.format_for_anthropic,
            LLMProvider.GOOGLE: MessageFormatter.format_for_gemini,
            LLMProvider.OLLAMA: MessageFormatter.format_for_openai,
            LLMProvider.MISTRAL: MessageFormatter.format_for_openai,
            LLMProvider.DEEPSEEK: MessageFormatter.format_for_openai,
            LLMProvider.QWEN: MessageFormatter.format_for_openai,
        }

        formatter = formatters.get(provider, MessageFormatter.format_for_openai)
        return formatter(messages, tools)

    def parse_response(self, response: Dict, provider: LLMProvider) -> List[ToolCall]:
        """Parst Tool-Calls aus Provider-Response"""
        return ResponseParser.parse(response, provider)

    def create_tool_result_message(
        self,
        tool_call: ToolCall,
        result: Any,
        is_error: bool = False,
        provider: LLMProvider = LLMProvider.OPENAI
    ) -> Dict:
        """Erstellt Tool-Result-Nachricht für Provider"""
        if provider == LLMProvider.ANTHROPIC:
            return {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps(result) if not isinstance(result, str) else result,
                    "is_error": is_error
                }]
            }
        elif provider == LLMProvider.GOOGLE:
            return {
                "role": "function",
                "parts": [{
                    "functionResponse": {
                        "name": tool_call.name,
                        "response": {"result": result}
                    }
                }]
            }
        else:
            # OpenAI und kompatible
            return {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result) if not isinstance(result, str) else result
            }


# Singleton
llm_compat = LLMCompatibilityLayer()


def get_llm_compat() -> LLMCompatibilityLayer:
    """Gibt die Singleton-Instanz zurück"""
    return llm_compat


# MCP Tool-Definition
LLM_COMPAT_TOOLS = [
    {
        "name": "llm_compat_convert",
        "description": "Konvertiert MCP-Tools für spezifischen LLM-Provider (OpenAI, Anthropic, Google, Ollama)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tools": {
                    "type": "array",
                    "description": "Liste der zu konvertierenden Tools"
                },
                "provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "google", "ollama", "mistral", "deepseek", "qwen"],
                    "description": "Ziel-Provider"
                }
            },
            "required": ["provider"]
        }
    },
    {
        "name": "llm_compat_parse",
        "description": "Parst Tool-Calls aus LLM-Response",
        "inputSchema": {
            "type": "object",
            "properties": {
                "response": {"type": "object", "description": "LLM-Response"},
                "provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "google", "ollama"],
                    "description": "Provider der Response"
                }
            },
            "required": ["response", "provider"]
        }
    }
]


async def handle_llm_compat_convert(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handler für llm_compat_convert"""
    provider_str = params.get("provider", "openai")
    provider = LLMProvider(provider_str)

    tools = params.get("tools", [])

    converted = llm_compat.convert_tools(tools, "mcp", provider)

    return {
        "provider": provider_str,
        "converted_tools": converted,
        "count": len(converted)
    }


async def handle_llm_compat_parse(params: Dict[str, Any]) -> Dict[str, Any]:
    """Handler für llm_compat_parse"""
    response = params.get("response", {})
    provider_str = params.get("provider", "openai")
    provider = LLMProvider(provider_str)

    tool_calls = llm_compat.parse_response(response, provider)

    return {
        "provider": provider_str,
        "tool_calls": [
            {
                "name": tc.name,
                "arguments": tc.arguments,
                "id": tc.id
            }
            for tc in tool_calls
        ],
        "count": len(tool_calls)
    }


LLM_COMPAT_HANDLERS = {
    "llm_compat_convert": handle_llm_compat_convert,
    "llm_compat_parse": handle_llm_compat_parse,
}
