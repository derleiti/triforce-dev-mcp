"""
MCP (Model Context Protocol) Module for AILinux Backend

This module provides:
- API documentation for Claude Code integration
- Translation layer between REST API and MCP JSON-RPC
- Model specialist routing for expert task delegation
- Workflow orchestration and context management
- Bidirectional request translation (API ↔ MCP)

API Endpoints:
- /v1/mcp - Primary MCP JSON-RPC endpoint
- /v1/mcp/status - MCP status and health
- /mcp/mcp - Alias under MCP namespace
- /tristar/mcp/* - TriStar MCP integration
- /triforce/mcp/* - TriForce MCP integration

JSON-RPC Methods:
- initialize - Initialize MCP session
- tools/list - List available tools
- tools/call - Execute a tool

Integration:
- TriStar v2.80 chain orchestration
- TriForce v2.80 LLM mesh network
- CLI agents (Claude, Codex, Gemini)
"""

from .api_docs import API_DOCUMENTATION, get_api_docs
from .translation import APIToMCPTranslator, MCPToAPITranslator
from .specialists import ModelSpecialist, SpecialistRouter
from .context import ContextManager, ConversationContext

from app.config import VERSION as __version__

__all__ = [
    # Version
    "__version__",
    # API Docs
    "API_DOCUMENTATION",
    "get_api_docs",
    # Translation
    "APIToMCPTranslator",
    "MCPToAPITranslator",
    # Specialists
    "ModelSpecialist",
    "SpecialistRouter",
    # Context
    "ContextManager",
    "ConversationContext",
]
