import json
import asyncio
import logging
import time
import httpx
import os
import hmac
import hashlib
import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum

"""
AILinux Server Federation v1.0
==============================

Server-to-Server Kommunikation und Healing:
- Nodes können sich gegenseitig registrieren
- Heartbeat-System für Health-Monitoring
- Auto-Failover bei Node-Ausfall
- Load-Sharing zwischen Nodes

Architektur:
┌─────────────────────────────────────────────────────────────┐
│                    FEDERATION MESH                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌─────────┐     ┌─────────┐     ┌─────────┐             │
│   │ Hetzner │────│ Backup  │────│ Client  │             │
│   │  (Hub)  │     │ (Node)  │     │ (Node)  │             │
│   └────┬────┘     └────┬────┘     └────┬────┘             │
│        │               │               │                   │
│        └───────────────┴───────────────┘                   │
│                    Heartbeat + Load Sharing                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
"""

logger = logging.getLogger("server_federation")


class NodeRole(str, Enum):
    HUB = "hub"           # Primärer Server (Hetzner)
    NODE = "node"         # Sekundärer Server (Backup)
    CONTRIBUTOR = "contributor"  # Client der Hardware teilt


class NodeStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class FederationNode:
    """Ein Node im Federation-Netzwerk"""
    node_id: str
    role: NodeRole
    base_url: str
    secret_key: str = ""  # Für Auth zwischen Nodes
    
    # Status
    status: NodeStatus = NodeStatus.UNKNOWN
    last_heartbeat: Optional[datetime] = None
    consecutive_failures: int = 0
    
    # Capabilities
    models: List[str] = field(default_factory=list)
    max_concurrent: int = 10
    current_load: int = 0
    
    # Stats
    total_requests: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 0
    
    def is_available(self) -> bool:
        """Check ob Node für Requests verfügbar"""
        return (
            self.status == NodeStatus.HEALTHY and
            self.current_load < self.max_concurrent
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "role": self.role.value,
            "base_url": self.base_url,
            "status": self.status.value,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "models": self.models,
            "current_load": self.current_load,
            "max_concurrent": self.max_concurrent,
            "total_requests": self.total_requests,
        }


class ServerFederation:
    """
    Verwaltet Federation zwischen AILinux Servern
    """
    
    HEARTBEAT_INTERVAL = 30  # Sekunden
    FAILURE_THRESHOLD = 3    # Nach X Failures -> offline
    RECOVERY_CHECK = 60      # Check offline nodes alle X Sekunden
    
    def __init__(self):
        self.nodes: Dict[str, FederationNode] = {}
        self.my_node_id: str = ""
        self.my_role: NodeRole = NodeRole.NODE
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
    
    async def initialize(self, node_id: str, role: NodeRole = NodeRole.NODE):
        """Initialisiere diesen Node"""
        self.my_node_id = node_id
        self.my_role = role
        
        # Registriere bekannte Nodes (aus Config)
        await self._load_known_nodes()
        
        # Starte Heartbeat Loop
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        logger.info(f"Federation initialized: {node_id} ({role.value})")
    
    async def _load_known_nodes(self):
        """Lade bekannte Nodes aus FEDERATION_NODES Config"""
        import os
        
        # Nutze FEDERATION_NODES Config (definiert weiter unten im File)
        # Die Config wird später importiert, also hier direkt definieren
        nodes_config = {
            "hetzner": {
                "url": "https://api.ailinux.me",
                "vpn_ip": "10.10.0.1",
                "port": 9100,
                "role": "hub"
            },
            "backup": {
                "url": "http://10.10.0.3:9000",
                "vpn_ip": "10.10.0.3",
                "port": 9100,
                "role": "node"
            },
            "zombie-pc": {
                "url": "http://10.10.0.2:9000",
                "vpn_ip": "10.10.0.2",
                "port": 9100,
                "role": "node"
            }
        }
        
        secret = os.getenv("FEDERATION_SECRET", "")
        
        for node_id, config in nodes_config.items():
            if node_id != self.my_node_id:
                role = NodeRole.HUB if config["role"] == "hub" else NodeRole.NODE
                self.nodes[node_id] = FederationNode(
                    node_id=node_id,
                    role=role,
                    base_url=f"http://{config['vpn_ip']}:{config['port']}",
                    secret_key=secret,
                )
    
    async def _heartbeat_loop(self):
        """Regelmäßige Heartbeats an alle Nodes"""
        while self._running:
            try:
                await self._check_all_nodes()
                await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat loop error: {e}")
                await asyncio.sleep(5)
    
    async def _check_all_nodes(self):
        """Checke alle Nodes"""
        for node_id, node in self.nodes.items():
            await self._check_node(node)
    
    async def _check_node(self, node: FederationNode):
        """Health-Check für einen Node"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                headers = {}
                if node.secret_key:
                    headers["X-Federation-Key"] = node.secret_key
                
                response = await client.get(
                    f"{node.base_url}/health",
                    headers=headers
                )
                
                if response.status_code == 200:
                    node.status = NodeStatus.HEALTHY
                    node.last_heartbeat = datetime.now()
                    node.consecutive_failures = 0
                    
                    # Parse capabilities from response
                    data = response.json()
                    if "models" in data:
                        node.models = data["models"]
                else:
                    await self._handle_node_failure(node, f"HTTP {response.status_code}")
                    
        except Exception as e:
            await self._handle_node_failure(node, str(e))
    
    async def _handle_node_failure(self, node: FederationNode, error: str):
        """Handle Node Failure"""
        node.consecutive_failures += 1
        node.total_errors += 1
        
        if node.consecutive_failures >= self.FAILURE_THRESHOLD:
            old_status = node.status
            node.status = NodeStatus.OFFLINE
            
            if old_status != NodeStatus.OFFLINE:
                logger.warning(f"Node {node.node_id} went OFFLINE: {error}")
                await self._trigger_failover(node)
        else:
            node.status = NodeStatus.DEGRADED
            logger.warning(f"Node {node.node_id} degraded ({node.consecutive_failures}x): {error}")
    
    async def _trigger_failover(self, failed_node: FederationNode):
        """Trigger Failover wenn ein Node ausfällt"""
        logger.info(f"Triggering failover for {failed_node.node_id}")
        
        # Finde gesunde Nodes
        healthy_nodes = [n for n in self.nodes.values() if n.status == NodeStatus.HEALTHY]
        
        if not healthy_nodes:
            logger.error("No healthy nodes available for failover!")
            return
        
        # Verteile Load auf gesunde Nodes
        for node in healthy_nodes:
            # TODO: Notify node to take over traffic
            pass
        
        logger.info(f"Failover complete: {len(healthy_nodes)} nodes taking over")
    
    async def register_contributor(
        self, 
        client_id: str, 
        hardware: Dict[str, Any],
        capabilities: List[str]
    ) -> FederationNode:
        """Registriere Client als Contributor Node"""
        node = FederationNode(
            node_id=f"contributor-{client_id}",
            role=NodeRole.CONTRIBUTOR,
            base_url="",  # Will use WebSocket
            status=NodeStatus.HEALTHY,
            last_heartbeat=datetime.now(),
            models=capabilities,
            max_concurrent=hardware.get("max_concurrent", 2),
        )
        
        self.nodes[node.node_id] = node
        logger.info(f"Contributor registered: {node.node_id} with {len(capabilities)} models")
        
        return node
    
    def get_available_node(self, model: str = None) -> Optional[FederationNode]:
        """Finde verfügbaren Node für Request"""
        available = [n for n in self.nodes.values() if n.is_available()]
        
        if model:
            # Filtere nach Model-Support
            available = [n for n in available if model in n.models or not n.models]
        
        if not available:
            return None
        
        # Wähle Node mit geringstem Load
        return min(available, key=lambda n: n.current_load / n.max_concurrent)
    
    def get_status(self) -> Dict[str, Any]:
        """Federation Status"""
        return {
            "my_node_id": self.my_node_id,
            "my_role": self.my_role.value,
            "nodes": {
                node_id: node.to_dict()
                for node_id, node in self.nodes.items()
            },
            "healthy_count": sum(1 for n in self.nodes.values() if n.status == NodeStatus.HEALTHY),
            "total_count": len(self.nodes),
        }
    
    async def shutdown(self):
        """Shutdown Federation"""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        logger.info("Federation shutdown complete")


# Singleton
federation = ServerFederation()


# =============================================================================
# Legacy Compatibility - für federation_websocket.py
# =============================================================================

import os
import hmac
import hashlib
import base64

FEDERATION_PSK = os.getenv("FEDERATION_SECRET", "")
if FEDERATION_PSK:
    logger.info("Federation PSK configured (len=%d)", len(FEDERATION_PSK))
else:
    # Federation is optional on fresh/local installations. Keep it fail-closed
    # at the signing boundary instead of preventing the entire backend from
    # importing when no federation secret was intentionally configured.
    logger.info("Federation disabled: FEDERATION_SECRET is not configured")

# Federation Node Configuration
# vpn_ip: WireGuard VPN address for direct communication
# port: Backend API port (internal, not Apache proxy)
FEDERATION_NODES = {
    "hetzner": {
        "url": "https://api.ailinux.me",
        "vpn_ip": "10.10.0.1",
        "port": 9100,
        "role": "hub"
    },
    "backup": {
        "url": "http://10.10.0.3:9000",
        "vpn_ip": "10.10.0.3",
        "port": 9100,
        "role": "node"
    },
    "zombie-pc": {
        "url": "http://10.10.0.2:9000",
        "vpn_ip": "10.10.0.2",
        "port": 9100,
        "role": "node"
    }
}


def create_signed_request(data: dict, secret: str = None) -> dict:
    """Signiere Request mit PSK"""
    secret = secret or FEDERATION_PSK
    if not secret:
        raise RuntimeError("Federation signing is disabled: FEDERATION_SECRET is not configured")
    timestamp = str(int(time.time()))
    
    # Create signature
    message = f"{timestamp}:{json.dumps(data, sort_keys=True)}"
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "data": data,
        "timestamp": timestamp,
        "signature": signature
    }


def verify_signed_request(request: dict, secret: str = None, max_age: int = 300) -> Optional[dict]:
    """
    Verifiziere signierte Anfrage.
    Returns: Das 'data' dict wenn Signatur gültig, sonst None.
    """
    secret = secret or FEDERATION_PSK
    if not secret:
        logger.warning("Signed federation request rejected: federation is disabled")
        return None

    try:
        data = request.get("data", {})
        timestamp = request.get("timestamp", "0")
        signature = request.get("signature", "")
        
        # Check timestamp
        if abs(int(time.time()) - int(timestamp)) > max_age:
            logger.warning(f"Signed request expired: age={int(time.time()) - int(timestamp)}s")
            return None
        
        # Verify signature
        message = f"{timestamp}:{json.dumps(data, sort_keys=True)}"
        expected = hmac.new(
            secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        if hmac.compare_digest(signature, expected):
            return data  # Gib das entpackte data dict zurück
        else:
            logger.warning("Signed request rejected: signature mismatch")
            return None
    except Exception as e:
        logger.error(f"Signed request verification error: {e}")
        return None


# Alias für main.py Kompatibilität
federation_manager = federation


# =============================================================================
# Load Balancer Integration - NEU
# =============================================================================

class LoadBalancerIntegration:
    """
    Integration mit externem Load Balancer (HAProxy/Nginx/Cloudflare)
    
    Features:
    - Dynamic Routing basierend auf Model
    - Weighted Backend Selection
    - Health Reporting für LB
    """
    
    def __init__(self, federation: ServerFederation):
        self.federation = federation
    
    def get_backend_for_model(self, model: str) -> Optional[Dict[str, Any]]:
        """
        Finde bestes Backend für ein Model.
        Für dynamisches Routing (z.B. HAProxy map-Lookup oder Cloudflare Worker)
        """
        node = self.federation.get_available_node(model)
        if not node:
            return None
        
        return {
            "node_id": node.node_id,
            "backend": node.base_url,
            "weight": self._calculate_weight(node),
            "status": node.status.value
        }
    
    def _calculate_weight(self, node: FederationNode) -> int:
        """
        Berechne Gewichtung für Load Balancer (0-100)
        Höher = mehr Traffic
        """
        if node.status != NodeStatus.HEALTHY:
            return 0
        
        # Basis: Verfügbare Kapazität
        capacity = 1.0 - (node.current_load / max(node.max_concurrent, 1))
        
        # Role-Bonus: Hub bevorzugen
        role_bonus = 1.2 if node.role == NodeRole.HUB else 1.0
        
        # Latenz-Malus (wenn verfügbar)
        latency_factor = max(0.5, 1.0 - (node.avg_latency_ms / 1000))
        
        weight = int(capacity * role_bonus * latency_factor * 100)
        return max(0, min(100, weight))
    
    def get_haproxy_server_state(self) -> str:
        """
        Generiere HAProxy Server-State für dynamisches Config
        Format: server <name> <ip>:<port> weight <w> check
        """
        lines = []
        for node in self.federation.nodes.values():
            weight = self._calculate_weight(node)
            state = "enabled" if weight > 0 else "disabled"
            
            # Parse host:port from base_url
            from urllib.parse import urlparse
            parsed = urlparse(node.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 9000
            
            lines.append(f"server {node.node_id} {host}:{port} weight {weight} check {state}")
        
        return "\n".join(lines)
    
    def get_nginx_upstream(self) -> str:
        """
        Generiere Nginx Upstream Config
        """
        lines = ["upstream triforce_backend {", "    least_conn;"]
        
        for node in self.federation.nodes.values():
            weight = self._calculate_weight(node)
            if weight == 0:
                continue
            
            from urllib.parse import urlparse
            parsed = urlparse(node.base_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 9000
            
            backup = " backup" if node.role == NodeRole.CONTRIBUTOR else ""
            lines.append(f"    server {host}:{port} weight={weight}{backup};")
        
        lines.append("}")
        return "\n".join(lines)
    
    def get_cloudflare_worker_config(self) -> Dict[str, Any]:
        """
        Config für Cloudflare Worker-basiertes Load Balancing
        """
        backends = []
        
        for node in self.federation.nodes.values():
            weight = self._calculate_weight(node)
            backends.append({
                "id": node.node_id,
                "url": node.base_url,
                "weight": weight,
                "healthy": node.status == NodeStatus.HEALTHY,
                "models": node.models
            })
        
        return {
            "backends": backends,
            "strategy": "weighted_least_conn",
            "health_check_path": "/health",
            "timeout_ms": 30000
        }


# Singleton
lb_integration = LoadBalancerIntegration(federation)
