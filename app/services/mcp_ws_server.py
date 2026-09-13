"""
MCP Mesh WebSocket Server v2.0
==============================

Schwarm-fähiger WebSocket Server auf Port 44433.

Features:
- Node Registration & Discovery  
- Tool Aggregation (alle Tools aller Nodes)
- Message Routing (unicast, broadcast)
- Load Balancing für Tool-Calls
- Gossip Protocol für Peer-Discovery
- Health Monitoring

Jeder verbundene Client ist ein Node im Mesh.
"""

import asyncio
import socket
import json
import ssl
import logging
import uuid
from pathlib import Path
from typing import Dict, Set, Any, Optional, List
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import get_settings

try:
    import websockets
    from websockets.server import serve, WebSocketServerProtocol
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

logger = logging.getLogger("mcp_ws_server")

# Config
_settings = get_settings()
MCP_WS_HOST = _settings.mcp_ws_host
MCP_WS_PORT = _settings.mcp_ws_port
MCP_WS_ENABLE_IPV6 = _settings.mcp_ws_enable_ipv6
CERT_DIR = Path("/home/zombie/workspace/triforce/certs/client-auth")


def _port_available(host: str, port: int) -> bool:
    """Return True if the configured WebSocket bind port is available."""
    family = socket.AF_INET6 if MCP_WS_ENABLE_IPV6 else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((host, port))
            return True
    except OSError:
        return False


@dataclass
class MeshNode:
    """Connected mesh node"""
    node_id: str
    websocket: WebSocketServerProtocol
    session_id: str = ""
    machine_id: str = ""
    tier: str = "guest"
    hostname: str = ""
    platform: str = ""
    tools: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    connected_at: datetime = field(default_factory=datetime.now)
    last_ping: datetime = field(default_factory=datetime.now)
    request_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "session_id": self.session_id,
            "tier": self.tier,
            "hostname": self.hostname,
            "platform": self.platform,
            "tools": self.tools,
            "capabilities": self.capabilities,
            "connected_at": self.connected_at.isoformat(),
            "request_count": self.request_count,
        }


class MCPMeshServer:
    """
    Mesh-fähiger WebSocket Server für MCP Nodes.
    """
    
    VERSION = "2.0.0"
    
    def __init__(self):
        self.nodes: Dict[str, MeshNode] = {}
        self.tool_providers: Dict[str, List[str]] = defaultdict(list)
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self._request_counter = 0
        self._lock = asyncio.Lock()
        self.server = None
        self._running = False
        
        # Stats
        self.stats = {
            "total_connections": 0,
            "total_messages": 0,
            "total_tool_calls": 0,
            "started_at": None,
        }
    
    def _get_ssl_context(self) -> Optional[ssl.SSLContext]:
        """Create SSL context (optional mTLS)"""
        try:
            ca_cert = CERT_DIR / "ca.crt"
            ca_key = CERT_DIR / "ca.key"
            
            if not ca_cert.exists() or not ca_key.exists():
                logger.warning("No certificates - running without TLS")
                return None
            
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(ca_cert), str(ca_key))
            ctx.verify_mode = ssl.CERT_OPTIONAL  # mTLS optional
            ctx.load_verify_locations(str(ca_cert))
            
            return ctx
        except Exception as e:
            logger.error(f"SSL setup failed: {e}")
            return None
    
    # =========================================================================
    # Node Management
    # =========================================================================
    
    async def register_node(self, ws: WebSocketServerProtocol, params: Dict) -> MeshNode:
        """Register a mesh node"""
        node_id = params.get("session_id", f"node_{uuid.uuid4().hex[:12]}")
        
        async with self._lock:
            # Disconnect existing if reconnect
            if node_id in self.nodes:
                old = self.nodes[node_id]
                try:
                    await old.websocket.close()
                except:
                    pass
            
            node = MeshNode(
                node_id=node_id,
                websocket=ws,
                session_id=params.get("session_id", ""),
                machine_id=params.get("machine_id", ""),
                tier=params.get("tier", "guest"),
                hostname=params.get("hostname", ""),
                platform=params.get("platform", ""),
                tools=params.get("tools", []),
                capabilities=params.get("capabilities", []),
            )
            
            self.nodes[node_id] = node
            self.stats["total_connections"] += 1
            
            # Update tool providers
            for tool in node.tools:
                if node_id not in self.tool_providers[tool]:
                    self.tool_providers[tool].append(node_id)
            
            logger.info(f"Node registered: {node_id} ({node.hostname}) - {len(node.tools)} tools, tier: {node.tier}")
            
            return node
    
    async def unregister_node(self, node_id: str):
        """Unregister a node"""
        async with self._lock:
            if node_id in self.nodes:
                node = self.nodes.pop(node_id)
                for tool in node.tools:
                    if node_id in self.tool_providers[tool]:
                        self.tool_providers[tool].remove(node_id)
                logger.info(f"Node unregistered: {node_id}")
    
    # =========================================================================
    # Message Routing
    # =========================================================================
    
    async def send_to_node(self, node_id: str, message: Dict) -> bool:
        """Send message to specific node"""
        node = self.nodes.get(node_id)
        if not node:
            return False
        try:
            await node.websocket.send(json.dumps(message))
            self.stats["total_messages"] += 1
            return True
        except:
            return False
    
    async def broadcast(self, message: Dict, exclude: Set[str] = None):
        """Broadcast to all nodes"""
        exclude = exclude or set()
        for node_id in list(self.nodes.keys()):
            if node_id not in exclude:
                await self.send_to_node(node_id, message)
    
    def find_tool_provider(self, tool_name: str) -> Optional[str]:
        """Find node that provides tool (load balanced)"""
        providers = self.tool_providers.get(tool_name, [])
        connected = [p for p in providers if p in self.nodes]
        
        if not connected:
            return None
        
        # Pick node with lowest request count
        min_req = min(self.nodes[p].request_count for p in connected)
        for p in connected:
            if self.nodes[p].request_count == min_req:
                return p
        return connected[0]
    
    async def route_tool_call(self, tool_name: str, args: Dict, timeout: float = 120) -> Dict:
        """Route tool call to appropriate node"""
        provider = self.find_tool_provider(tool_name)
        
        if not provider:
            return {"error": f"No provider for tool: {tool_name}"}
        
        node = self.nodes[provider]
        node.request_count += 1
        self.stats["total_tool_calls"] += 1
        
        # Create request
        self._request_counter += 1
        req_id = f"hub_{self._request_counter}"
        
        fut = asyncio.get_event_loop().create_future()
        self.pending_requests[req_id] = fut
        
        # Send to node
        await self.send_to_node(provider, {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": args}
        })
        
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self.pending_requests.pop(req_id, None)
            return {"error": f"Timeout after {timeout}s"}
    
    # =========================================================================
    # Client Handler
    # =========================================================================
    
    async def _handle_client(self, websocket: WebSocketServerProtocol):
        """Handle client connection"""
        client_addr = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"
        logger.info(f"New connection from {client_addr}")
        
        node = None
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    response = await self._handle_message(websocket, data, node)
                    
                    # Register on node/register
                    if data.get("method") == "node/register" and response:
                        node = self.nodes.get(response.get("result", {}).get("session_id"))
                    
                    if response and data.get("id"):
                        await websocket.send(json.dumps(response))
                        
                except json.JSONDecodeError:
                    await websocket.send(json.dumps({
                        "jsonrpc": "2.0",
                        "error": {"code": -32700, "message": "Parse error"},
                        "id": None
                    }))
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            if node:
                await self.unregister_node(node.node_id)
            logger.info(f"Connection closed: {client_addr}")
    
    async def _handle_message(self, ws, data: Dict, node: Optional[MeshNode]) -> Optional[Dict]:
        """Handle JSON-RPC message"""
        method = data.get("method", "")
        params = data.get("params", {})
        req_id = data.get("id")
        
        result = None
        error = None
        
        # Handle response to pending request
        if "result" in data or "error" in data:
            pending = self.pending_requests.pop(req_id, None)
            if pending:
                if "error" in data:
                    pending.set_exception(Exception(str(data["error"])))
                else:
                    pending.set_result(data.get("result"))
            return None
        
        try:
            # Node registration
            if method == "node/register":
                node = await self.register_node(ws, params)
                result = {
                    "session_id": node.node_id,
                    "hub_version": self.VERSION,
                    "connected_nodes": len(self.nodes),
                    "available_tools": len(self.tool_providers),
                }
                # Send acceptance notification
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "node/accepted",
                    "params": result
                }))
            
            # Initialize (MCP standard)
            elif method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "ailinux-mesh", "version": self.VERSION},
                    "capabilities": {"tools": {}, "resources": {}},
                }
            
            # Ping
            elif method == "ping":
                if node:
                    node.last_ping = datetime.now()
                result = {"pong": True}
            
            # List mesh nodes
            elif method == "mesh/nodes":
                nodes = [n.to_dict() for n in self.nodes.values()]
                result = {"nodes": nodes, "count": len(nodes)}
            
            # List aggregated tools
            elif method == "mesh/tools" or method == "tools/list":
                tools = []
                for tool_name, providers in self.tool_providers.items():
                    if providers:
                        tools.append({
                            "name": tool_name,
                            "providers": len(providers),
                        })
                result = {"tools": tools}
            
            # Call tool (routed to node)
            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                tool_result = await self.route_tool_call(tool_name, arguments)
                
                if "error" in tool_result:
                    result = {
                        "content": [{"type": "text", "text": f"Error: {tool_result['error']}"}],
                        "isError": True
                    }
                else:
                    result = {
                        "content": [{"type": "text", "text": json.dumps(tool_result, indent=2)}]
                    }
            
            # Broadcast
            elif method == "mesh/broadcast":
                msg = params.get("message", {})
                exclude = {node.node_id} if node else set()
                await self.broadcast(msg, exclude)
                result = {"sent_to": len(self.nodes) - 1}
            
            # Gossip
            elif method == "peer/gossip":
                # Share peer info between nodes
                peers = [n.to_dict() for n in self.nodes.values()]
                result = {"peers": peers}
            
            # Stats
            elif method == "mesh/stats":
                result = {
                    **self.stats,
                    "active_nodes": len(self.nodes),
                    "active_tools": len(self.tool_providers),
                    "pending_requests": len(self.pending_requests),
                }
            
            else:
                error = {"code": -32601, "message": f"Method not found: {method}"}
        
        except Exception as e:
            logger.error(f"Handler error: {e}")
            error = {"code": -32000, "message": str(e)}
        
        if req_id is None:
            return None
        
        response = {"jsonrpc": "2.0", "id": req_id}
        if error:
            response["error"] = error
        else:
            response["result"] = result
        return response
    
    # =========================================================================
    # Server Lifecycle
    # =========================================================================
    
    async def start(self):
        """Start the mesh server"""
        if not HAS_WEBSOCKETS:
            logger.error("websockets library not installed")
            return
        
        ssl_ctx = self._get_ssl_context()
        self._running = True
        self.stats["started_at"] = datetime.now().isoformat()
        
        try:
            if not _port_available(MCP_WS_HOST, MCP_WS_PORT):
                logger.warning(f"MCP WebSocket port already in use: {MCP_WS_HOST}:{MCP_WS_PORT}")
                self._running = False
                return

            self.server = await serve(
                self._handle_client,
                MCP_WS_HOST,
                MCP_WS_PORT,
                ssl=ssl_ctx,
                ping_interval=30,
                ping_timeout=10,
            )
            logger.info(f"MCP Mesh Server v{self.VERSION} started on port {MCP_WS_PORT}")
        except Exception as e:
            logger.error(f"Failed to start server: {e}")
            self._running = False
    
    async def stop(self):
        """Stop the mesh server"""
        self._running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("MCP Mesh Server stopped")
    
    def get_mesh_info(self) -> Dict:
        """Get mesh status"""
        return {
            "version": self.VERSION,
            "nodes": len(self.nodes),
            "tools": len(self.tool_providers),
            "stats": self.stats,
        }


# Global instance
mcp_ws_server = MCPMeshServer()
