"""
AILinux Support Service v1.0
============================
Claude Opus 4.5 als Support-Admin mit MCP-Zugang
"""
import httpx
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

from app.paths import TRISTAR_DIR

logger = logging.getLogger("ailinux.support")

# Anthropic API Config
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
SUPPORT_MODEL = "claude-opus-4-5-20251101"  # Neuestes & stärkstes

# Support-Prompt laden
SUPPORT_PROMPT_PATH = TRISTAR_DIR / "prompts/support-agent.txt"


class SupportService:
    """AILinux Support powered by Claude Opus 4.5"""
    
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        self.model = SUPPORT_MODEL
        self.system_prompt = self._load_system_prompt()
        self._conversation_history: Dict[str, List[Dict]] = {}
    
    def _load_system_prompt(self) -> str:
        """Lade Support-System-Prompt"""
        if SUPPORT_PROMPT_PATH.exists():
            return SUPPORT_PROMPT_PATH.read_text()
        return "Du bist der AILinux Support Agent."
    
    async def chat(
        self, 
        user_id: str, 
        message: str,
        include_mcp_context: bool = True
    ) -> Dict[str, Any]:
        """
        Support-Chat mit User
        
        Args:
            user_id: User-ID für Conversation-History
            message: User-Nachricht
            include_mcp_context: MCP-Status anhängen
            
        Returns:
            Response mit Antwort und Metadaten
        """
        if not self.api_key:
            return {"error": "No API key configured", "success": False}
        
        # Conversation History
        if user_id not in self._conversation_history:
            self._conversation_history[user_id] = []
        
        # MCP-Kontext hinzufügen wenn gewünscht
        system = self.system_prompt
        if include_mcp_context:
            system += f"\n\n[System-Zeit: {datetime.now().isoformat()}]"
        
        # User-Message zur History
        self._conversation_history[user_id].append({
            "role": "user",
            "content": message
        })
        
        # Nur letzte 10 Messages für Context
        messages = self._conversation_history[user_id][-10:]
        
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(
                    ANTHROPIC_API_URL,
                    headers={
                        "Content-Type": "application/json",
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01"
                    },
                    json={
                        "model": self.model,
                        "max_tokens": 4096,
                        "system": system,
                        "messages": messages
                    }
                )
                
                data = response.json()
                
                if "error" in data:
                    return {
                        "success": False,
                        "error": data["error"].get("message", str(data["error"]))
                    }
                
                # Assistant-Response zur History
                assistant_content = data["content"][0]["text"]
                self._conversation_history[user_id].append({
                    "role": "assistant",
                    "content": assistant_content
                })
                
                return {
                    "success": True,
                    "response": assistant_content,
                    "model": data.get("model"),
                    "usage": data.get("usage", {}),
                    "support_agent": "Claude Opus 4.5"
                }
                
        except Exception as e:
            logger.error(f"Support chat error: {e}")
            return {"success": False, "error": str(e)}
    
    def clear_history(self, user_id: str):
        """Lösche Conversation-History für User"""
        if user_id in self._conversation_history:
            del self._conversation_history[user_id]
    
    async def execute_mcp_tool(self, tool_name: str, params: Dict = None) -> Dict:
        """
        Führe MCP-Tool als Admin aus
        
        Erlaubte Admin-Tools:
        - tristar_status
        - triforce_logs_recent
        - triforce_logs_errors
        - cli-agents_list
        - queue_status
        - tristar_settings
        """
        ALLOWED_TOOLS = [
            "tristar_status", "triforce_logs_recent", "triforce_logs_errors",
            "cli-agents_list", "queue_status", "tristar_settings",
            "tristar_memory_search", "mesh_get_status"
        ]
        
        if tool_name not in ALLOWED_TOOLS:
            return {"error": f"Tool '{tool_name}' not allowed for support", "allowed": ALLOWED_TOOLS}
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    "http://localhost:9000/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": params or {}},
                        "id": 1
                    }
                )
                return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    async def get_system_status(self) -> Dict:
        """Hole aktuellen System-Status für Support-Kontext"""
        status = {}
        
        # TriStar Status
        tristar = await self.execute_mcp_tool("tristar_status")
        if "result" in tristar:
            status["tristar"] = tristar["result"]
        
        # Agent Status
        agents = await self.execute_mcp_tool("cli-agents_list")
        if "result" in agents:
            status["agents"] = agents["result"]
        
        return status


# Singleton
_support_service: Optional[SupportService] = None


def get_support_service(api_key: str = None) -> SupportService:
    """Hole Support-Service Singleton"""
    global _support_service
    if _support_service is None:
        _support_service = SupportService(api_key=api_key)
    return _support_service


def init_support_service(api_key: str) -> SupportService:
    """Initialisiere Support-Service mit API Key"""
    global _support_service
    _support_service = SupportService(api_key=api_key)
    return _support_service
