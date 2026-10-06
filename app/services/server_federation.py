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
    optional: bool = True
    draining: bool = False
    placement_weight: float = 1.0

    # Capabilities
    models: List[str] = field(default_factory=list)
    max_concurrent: int = 10
    current_load: int = 0

    # Resource metrics learned from the public /health heartbeat.
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    load_ratio: float = 0.0
    memory_available_mb: int = 0
    swap_free_mb: int = 0
    disk_free_gb: float = 0.0
    metrics_updated_at: Optional[datetime] = None

    # Stats
    total_requests: int = 0
    total_errors: int = 0
    avg_latency_ms: float = 0

    def heartbeat_age_seconds(self) -> float:
        if self.last_heartbeat is None:
            return float("inf")
        return max(0.0, (datetime.now() - self.last_heartbeat).total_seconds())

    def is_available(self, stale_after: float = 75.0) -> bool:
        """Return whether this node may receive new work.

        Optional nodes are never required for cluster health. A draining,
        degraded, offline or stale node is simply omitted from scheduling.
        """
        return (
            self.status == NodeStatus.HEALTHY
            and not self.draining
            and self.current_load < self.max_concurrent
            and self.heartbeat_age_seconds() <= stale_after
        )

    def selection_score(self, stale_after: float = 75.0) -> float:
        """Resource-aware scheduling score in the range 0..100."""
        if not self.is_available(stale_after=stale_after):
            return 0.0

        queue_headroom = max(
            0.0,
            1.0 - (self.current_load / max(self.max_concurrent, 1)),
        )
        latency_headroom = max(0.20, 1.0 - (self.avg_latency_ms / 1500.0))

        metrics_fresh = (
            self.metrics_updated_at is not None
            and (datetime.now() - self.metrics_updated_at).total_seconds() <= stale_after * 2
        )
        if not metrics_fresh:
            # Older/downgraded nodes that do not expose fresh resource metrics
            # remain usable, but never look artificially idle.
            resource_score = 0.65
        else:
            cpu_headroom = max(0.0, 1.0 - min(self.cpu_percent, 100.0) / 100.0)
            memory_headroom = max(0.0, 1.0 - min(self.memory_percent, 100.0) / 100.0)
            load_headroom = max(0.0, 1.0 - min(self.load_ratio, 1.5) / 1.5)
            resource_score = (
                0.32 * cpu_headroom
                + 0.32 * memory_headroom
                + 0.16 * load_headroom
                + 0.12 * queue_headroom
                + 0.08 * latency_headroom
            )
            if self.memory_available_mb and self.memory_available_mb < 1024:
                resource_score *= 0.55
            if self.swap_free_mb and self.swap_free_mb < 512:
                resource_score *= 0.85

        score = resource_score * self.placement_weight * 100.0
        return max(0.0, min(100.0, score))
    
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
            "optional": self.optional,
            "draining": self.draining,
            "placement_weight": self.placement_weight,
            "selection_score": round(self.selection_score(), 2),
            "heartbeat_age_seconds": (
                round(self.heartbeat_age_seconds(), 2)
                if self.last_heartbeat else None
            ),
            "metrics": {
                "cpu_percent": self.cpu_percent,
                "memory_percent": self.memory_percent,
                "load_ratio": self.load_ratio,
                "memory_available_mb": self.memory_available_mb,
                "swap_free_mb": self.swap_free_mb,
                "disk_free_gb": self.disk_free_gb,
                "updated_at": self.metrics_updated_at.isoformat() if self.metrics_updated_at else None,
            },
        }


class ServerFederation:
    """
    Verwaltet Federation zwischen AILinux Servern
    """
    
    HEARTBEAT_INTERVAL = 15  # seconds
    HEARTBEAT_TIMEOUT = 2.5  # one missing optional node must not stall the mesh
    FAILURE_THRESHOLD = 2    # degraded immediately, offline on repeated failure
    STALE_AFTER = 60         # stale nodes never receive new work
    RECOVERY_CHECK = 30
    
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
                "role": "hub",
                "optional": False,
                "placement_weight": 0.72,
            },
            "backup": {
                "url": "http://10.10.0.3:9000",
                "vpn_ip": "10.10.0.3",
                "port": 9100,
                "role": "node",
                "optional": True,
                "placement_weight": 1.18,
            },
            "zombie-pc": {
                "url": "http://10.10.0.2:9000",
                "vpn_ip": "10.10.0.2",
                "port": 9100,
                "role": "node",
                "optional": True,
                "placement_weight": 1.08,
            }
        }
        
        secret = os.getenv("FEDERATION_SECRET", "")
        drained = {
            item.strip()
            for item in os.getenv("TRIFORCE_DRAIN_NODES", "").split(",")
            if item.strip()
        }

        for node_id, config in nodes_config.items():
            if node_id != self.my_node_id:
                role = NodeRole.HUB if config["role"] == "hub" else NodeRole.NODE
                self.nodes[node_id] = FederationNode(
                    node_id=node_id,
                    role=role,
                    base_url=f"http://{config['vpn_ip']}:{config['port']}",
                    secret_key=secret,
                    optional=bool(config.get("optional", True)),
                    draining=node_id in drained,
                    placement_weight=float(config.get("placement_weight", 1.0)),
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
        """Probe every peer concurrently so one dead node cannot stall others."""
        if not self.nodes:
            return
        await asyncio.gather(
            *(self._check_node(node) for node in self.nodes.values()),
            return_exceptions=True,
        )
    
    async def _check_node(self, node: FederationNode):
        """Health-check a peer with a short bounded timeout."""
        started = time.monotonic()
        try:
            timeout = httpx.Timeout(self.HEARTBEAT_TIMEOUT, connect=1.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                headers = {}
                if node.secret_key:
                    headers["X-Federation-Key"] = node.secret_key

                response = await client.get(f"{node.base_url}/health", headers=headers)

                if response.status_code == 200:
                    node.status = NodeStatus.HEALTHY
                    node.last_heartbeat = datetime.now()
                    node.consecutive_failures = 0
                    latency_ms = (time.monotonic() - started) * 1000.0
                    node.avg_latency_ms = (
                        latency_ms if node.avg_latency_ms <= 0
                        else (0.75 * node.avg_latency_ms + 0.25 * latency_ms)
                    )

                    data = response.json()
                    if "models" in data:
                        node.models = data["models"]

                    metrics = data.get("node_metrics")
                    if isinstance(metrics, dict):
                        node.cpu_percent = float(metrics.get("cpu_percent") or 0.0)
                        node.memory_percent = float(metrics.get("memory_percent") or 0.0)
                        node.load_ratio = float(metrics.get("load_ratio") or 0.0)
                        node.memory_available_mb = int(metrics.get("memory_available_mb") or 0)
                        node.swap_free_mb = int(metrics.get("swap_free_mb") or 0)
                        node.disk_free_gb = float(metrics.get("disk_free_gb") or 0.0)
                        node.metrics_updated_at = datetime.now()
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
    
    def rank_available_nodes(self, model: str = None) -> List[FederationNode]:
        """Return healthy nodes ordered by live capacity and placement policy."""
        available = [
            n for n in self.nodes.values()
            if n.is_available(stale_after=self.STALE_AFTER)
        ]
        if model:
            available = [n for n in available if model in n.models or not n.models]
        return sorted(
            available,
            key=lambda n: n.selection_score(stale_after=self.STALE_AFTER),
            reverse=True,
        )

    def get_available_node(self, model: str = None) -> Optional[FederationNode]:
        """Return the best live node, never requiring an optional peer."""
        ranked = self.rank_available_nodes(model)
        return ranked[0] if ranked else None
    
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
        "role": "hub",
        "optional": False,
        "placement_weight": 0.72,
    },
    "backup": {
        "url": "http://10.10.0.3:9000",
        "vpn_ip": "10.10.0.3",
        "port": 9100,
        "role": "node",
        "optional": True,
        "placement_weight": 1.18,
    },
    "zombie-pc": {
        "url": "http://10.10.0.2:9000",
        "vpn_ip": "10.10.0.2",
        "port": 9100,
        "role": "node",
        "optional": True,
        "placement_weight": 1.08,
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
        return int(round(node.selection_score(stale_after=self.federation.STALE_AFTER)))
    
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
