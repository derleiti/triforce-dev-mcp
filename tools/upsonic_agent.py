import os
os.environ["UPSONIC_TELEMETRY"] = "False"

from dotenv import load_dotenv
load_dotenv("/home/zombie/workspace/triforce/.env")

from upsonic import Agent, Task
from upsonic_triforce import TriForceMCP

mcp = TriForceMCP()

def web_search(query: str) -> str:
    """Durchsucht das Web"""
    return str(mcp.call_tool("web_search", query=query))

def ollama_chat(prompt: str, model: str = "llama3.2") -> str:
    """Chat mit Ollama"""
    return str(mcp.call_tool("ollama_generate", prompt=prompt, model=model))

def create_agent(model: str = "groq/llama-3.3-70b-versatile"):
    """Erstellt Agent mit Groq (kostenlos)"""
    return Agent(name="TriForceAgent", model=model, tools=[web_search, ollama_chat])

if __name__ == "__main__":
    print("=== TriForce + Upsonic (Groq) ===")
    tools = mcp.list_tools()
    print(f"MCP Tools: {len(tools)}")
    agent = create_agent()
    print(f"Agent: {agent.name}")
    print(f"Model: {agent.model}")
    print(f"Tools: {len(agent.tools)}")
    print("Ready!")
