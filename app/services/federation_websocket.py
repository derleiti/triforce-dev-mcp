#!/usr/bin/env python3
"""
AILinux Federation WebSocket Layer v1.0
========================================

Persistente WebSocket-Verbindungen zwischen Server-Nodes für:
- Real-time Status Sync
- Task Distribution & Load Balancing
- Automatic Failover
- Bidirektionale Kommunikation

Architektur:
```
┌─────────────┐  WebSocket  ┌─────────────┐
│   Hetzner   │◄═══════════►│   Backup    │
│    (Hub)    │  Persistent │    (Hub)    │
└─────────────┘             └─────────────┘
      │                           │
      └─────────┬─────────────────┘
                │
         Task Router
         Load Balancer
```
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
import websockets
from websockets.client import WebSocketClientProtocol
from websockets.exceptions import ConnectionClosed

from .server_federation import (
    create_signed_request,
    verify_signed_request,
    FEDERATION_PSK,
    FEDERATION_NODES
)

logger = logging.getLogger("ailinux.federation.ws")

# =============================================================================
# Configuration
# =============================================================================

# Node ID aus Hostname ableiten
import socket
_hostname = socket.gethostname()
# Determine NODE_ID from hostname
_hostname_lower = socket.gethostname().lower()
if "backup" in _hostname_lower:
    NODE_ID = "backup"
elif "zombie" in _hostname_lower:
    NODE_ID = "zombie-pc"
else:
    NODE_ID = os.getenv("FEDERATION_NODE_ID", "hetzner")
WS_RECONNECT_DELAY = max(1, int(os.getenv("FEDERATION_WS_RECONNECT_BASE", "5")))
WS_RECONNECT_MAX_DELAY = max(
    WS_RECONNECT_DELAY,
    int(os.getenv("FEDERATION_WS_RECONNECT_MAX", "300")),
)
WS_HEARTBEAT_INTERVAL = 10  # Sekunden
WS_PORT = 9001  # Legacy standalone WS server port; FastAPI federation uses backend_port.
DEFAULT_BACKEND_PORT = 9100


def _peer_backend_port(config: dict) -> int:
    """Return the peer FastAPI port used by /v1/federation/ws.

    New configs expose backend_port explicitly.  Fall back to port for older
    deployments and finally to the canonical TriForce backend default.
    """
    raw = config.get("backend_port", config.get("port", DEFAULT_BACKEND_PORT))
    try:
        port = int(raw)
    except (TypeError, ValueError):
        port = DEFAULT_BACKEND_PORT
    return port if 1 <= port <= 65535 else DEFAULT_BACKEND_PORT


# =============================================================================
# Message Types
# =============================================================================

class MessageType(str, Enum):
    # Connection
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    
    # Status
    STATUS_UPDATE = "status_update"
    STATUS_REQUEST = "status_request"
    
    # Tasks
    TASK_SUBMIT = "task_submit"
    TASK_RESULT = "task_result"
    TASK_ROUTE = "task_route"
    
    # Load Balancing
    LOAD_UPDATE = "load_update"
    CAPACITY_QUERY = "capacity_query"
    CAPACITY_RESPONSE = "capacity_response"


@dataclass
class NodeMetrics:
    """Echtzeit-Metriken eines Nodes"""
    node_id: str
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    active_requests: int = 0
    queue_depth: int = 0
    ollama_available: bool = True
    ollama_models: List[str] = field(default_factory=list)
    last_update: float = field(default_factory=time.time)
    
    @property
    def load_score(self) -> float:
        """Berechnet Load Score (0-1, niedriger = besser)"""
        cpu_weight = 0.4
        mem_weight = 0.2
        req_weight = 0.3
        queue_weight = 0.1
        
        cpu_score = self.cpu_percent / 100.0
        mem_score = self.memory_percent / 100.0
        req_score = min(self.active_requests / 10.0, 1.0)
        queue_score = min(self.queue_depth / 20.0, 1.0)
        
        return (cpu_weight * cpu_score + 
                mem_weight * mem_score + 
                req_weight * req_score + 
                queue_weight * queue_score)


# =============================================================================
# WebSocket Connection
# =============================================================================

class FederationPeer:
    """Verwaltet WebSocket-Verbindung zu einem Peer Node"""
    
    def __init__(self, node_id: str, ws_url: str):
        self.node_id = node_id
        self.ws_url = ws_url
        self.websocket: Optional[WebSocketClientProtocol] = None
        self.connected = False
        self.metrics = NodeMetrics(node_id=node_id)
        self.last_heartbeat = 0.0
        self._reconnect_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._message_handlers: Dict[str, Callable] = {}
        self._reconnect_attempt = 0
        self._stop_requested = False
        
    async def connect(self):
        """Verbindet zum Peer Node mit begrenztem exponentiellem Backoff."""
        self._stop_requested = False
        while not self._stop_requested:
            connected_since: Optional[float] = None
            try:
                logger.info(f"Connecting to peer {self.node_id} at {self.ws_url}")
                self.websocket = await websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                    open_timeout=10,
                )
                self.connected = True
                connected_since = time.monotonic()

                # Send HELLO
                await self._send_hello()

                # Start heartbeat
                self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

                # Message loop
                await self._message_loop()

            except ConnectionClosed as e:
                logger.warning(f"Connection to {self.node_id} closed: {e}")
            except Exception as e:
                logger.error(f"Connection error to {self.node_id}: {e}")

            self.connected = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()

            if self._stop_requested:
                break

            # Kurze/fehlgeschlagene Verbindungen bekommen exponentielles
            # Backoff. Nach mindestens drei Heartbeats gilt die Verbindung
            # als stabil und der Zähler wird zurückgesetzt.
            connection_age = (
                time.monotonic() - connected_since
                if connected_since is not None
                else 0.0
            )
            if connection_age >= max(30, WS_HEARTBEAT_INTERVAL * 3):
                self._reconnect_attempt = 0
            else:
                self._reconnect_attempt += 1

            exponent = min(max(self._reconnect_attempt - 1, 0), 8)
            reconnect_delay = min(
                WS_RECONNECT_DELAY * (2 ** exponent),
                WS_RECONNECT_MAX_DELAY,
            )
            logger.info(
                f"Reconnecting to {self.node_id} in {reconnect_delay}s "
                f"(attempt {self._reconnect_attempt})..."
            )
            await asyncio.sleep(reconnect_delay)
    
    async def _send_hello(self):
        """Sendet HELLO mit Token-Authentifizierung"""
        import os
        token = os.getenv("FEDERATION_TOKEN", "")
        msg = create_signed_request({
            "type": MessageType.HELLO,
            "node_id": NODE_ID,
            "token": token,
            "capabilities": FEDERATION_NODES.get(NODE_ID, {}).get("capabilities", [])
        })
        await self.send(msg)
    
    async def _heartbeat_loop(self):
        """Sendet periodische Heartbeats"""
        while self.connected:
            try:
                await asyncio.sleep(WS_HEARTBEAT_INTERVAL)
                await self.send_heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
    
    async def send_heartbeat(self):
        """Sendet Heartbeat mit aktuellen Metriken"""
        import psutil
        
        msg = create_signed_request({
            "type": MessageType.HEARTBEAT,
            "node_id": NODE_ID,
            "metrics": {
                "cpu_percent": psutil.cpu_percent(),
                "memory_percent": psutil.virtual_memory().percent,
                "active_requests": 0,  # TODO: Von Request Counter
                "queue_depth": 0
            }
        })
        await self.send(msg)
    
    async def _message_loop(self):
        """Empfängt und verarbeitet Nachrichten"""
        async for message in self.websocket:
            try:
                data = json.loads(message)

                payload = verify_signed_request(data)
                if payload is None:
                    logger.warning(f"Invalid/unsigned message from {self.node_id} rejected")
                    continue
                if payload.get("node_id") != self.node_id:
                    logger.warning(
                        f"Message identity mismatch from {self.node_id}: "
                        f"claimed {payload.get('node_id')}"
                    )
                    continue
                
                msg_type = payload.get("type")
                if msg_type in (MessageType.HEARTBEAT, MessageType.HEARTBEAT_ACK):
                    logger.debug("Federation heartbeat from %s: %s", self.node_id, msg_type)
                elif msg_type:
                    logger.info("Federation message from %s: %s", self.node_id, msg_type)
                if msg_type:
                    await self._handle_message(msg_type, payload)
                
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from {self.node_id}")
            except Exception as e:
                logger.error(f"Message handling error: {e}")
    
    async def _handle_message(self, msg_type: str, payload: dict):
        """Verarbeitet empfangene Nachricht"""
        if msg_type == MessageType.HELLO_ACK:
            logger.info(f"Connected to peer {self.node_id}")
            
        elif msg_type == MessageType.HEARTBEAT:
            # Update peer metrics
            metrics = payload.get("metrics", {})
            self.metrics.cpu_percent = metrics.get("cpu_percent", 0)
            self.metrics.memory_percent = metrics.get("memory_percent", 0)
            self.metrics.active_requests = metrics.get("active_requests", 0)
            self.metrics.queue_depth = metrics.get("queue_depth", 0)
            self.metrics.last_update = time.time()
            self.last_heartbeat = time.time()
            
            # Send ACK
            await self.send(create_signed_request({
                "type": MessageType.HEARTBEAT_ACK,
                "node_id": NODE_ID
            }))
            
        elif msg_type == MessageType.HEARTBEAT_ACK:
            self.last_heartbeat = time.time()
            
        elif msg_type == MessageType.TASK_SUBMIT:
            # Task von Peer empfangen
            handler = self._message_handlers.get("task_submit")
            if handler:
                await handler(payload)
                
        elif msg_type == MessageType.TASK_RESULT:
            # Task-Ergebnis von Peer
            handler = self._message_handlers.get("task_result")
            if handler:
                await handler(payload)
    
    async def send(self, data: dict):
        """Sendet Nachricht zum Peer"""
        if self.websocket and self.connected:
            await self.websocket.send(json.dumps(data))
    
    async def send_task(self, task_id: str, task_type: str, task_data: dict):
        """Sendet Task zum Peer"""
        msg = create_signed_request({
            "type": MessageType.TASK_SUBMIT,
            "node_id": NODE_ID,
            "task_id": task_id,
            "task_type": task_type,
            "task_data": task_data
        })
        await self.send(msg)
    
    def on_message(self, msg_type: str, handler: Callable):
        """Registriert Message Handler"""
        self._message_handlers[msg_type] = handler
    
    async def disconnect(self):
        """Trennt Verbindung und verhindert automatischen Neuaufbau."""
        self._stop_requested = True
        self.connected = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self.websocket:
            await self.websocket.close()


# =============================================================================
# Load Balancer
# =============================================================================

class FederationLoadBalancer:
    """
    Load Balancer für Server Federation.
    Verteilt Tasks basierend auf Node-Kapazität.
    """
    
    def __init__(self):
        self.node_id = NODE_ID
        self.peers: Dict[str, FederationPeer] = {}
        self.local_metrics = NodeMetrics(node_id=NODE_ID)
        self._running = False
        self._server = None
        
        # Task Callbacks
        self._task_handlers: Dict[str, Callable] = {}
        self._pending_tasks: Dict[str, asyncio.Future] = {}
    
    async def start(self):
        """Startet Load Balancer"""
        if self._running:
            return
            
        logger.info(f"Starting Federation Load Balancer as {self.node_id}")
        self._running = True
        
        # Start WebSocket Server
        # WS Server läuft via FastAPI Endpoint
        
        # Connect to peers
        for node_id, config in FEDERATION_NODES.items():
            if node_id != self.node_id:
                peer_port = _peer_backend_port(config)
                ws_url = f"ws://{config['vpn_ip']}:{peer_port}/v1/federation/ws"
                peer = FederationPeer(node_id, ws_url)
                peer.on_message("task_submit", self._handle_incoming_task)
                peer.on_message("task_result", self._handle_task_result)
                self.peers[node_id] = peer
                asyncio.create_task(peer.connect())
        
        logger.info(f"Federation Load Balancer ready with {len(self.peers)} peers")
    
    async def _start_ws_server(self):
        """Startet WebSocket Server für eingehende Peer-Verbindungen"""
        async def handler(websocket, path):
            peer_id = None
            try:
                async for message in websocket:
                    data = json.loads(message)
                    
                    payload = verify_signed_request(data)
                    if payload is None:
                        logger.warning("Invalid/unsigned federation message rejected")
                        continue
                    
                    msg_type = payload.get("type")
                    
                    if msg_type == MessageType.HELLO:
                        peer_id = payload.get("node_id")
                        logger.info(f"Peer {peer_id} connected")
                        
                        # Send ACK
                        ack = create_signed_request({
                            "type": MessageType.HELLO_ACK,
                            "node_id": self.node_id
                        })
                        await websocket.send(json.dumps(ack))
                        
                    elif peer_id is None or payload.get("node_id") != peer_id:
                        logger.warning(
                            f"Federation message identity mismatch: established={peer_id}, "
                            f"claimed={payload.get('node_id')}"
                        )
                        continue

                    elif msg_type == MessageType.HEARTBEAT:
                        # Update metrics
                        if peer_id and peer_id in self.peers:
                            metrics = payload.get("metrics", {})
                            self.peers[peer_id].metrics.cpu_percent = metrics.get("cpu_percent", 0)
                            self.peers[peer_id].metrics.memory_percent = metrics.get("memory_percent", 0)
                            self.peers[peer_id].metrics.last_update = time.time()
                        
                        # Send ACK
                        ack = create_signed_request({
                            "type": MessageType.HEARTBEAT_ACK,
                            "node_id": self.node_id
                        })
                        await websocket.send(json.dumps(ack))
                        
            except ConnectionClosed:
                logger.info(f"Peer {peer_id} disconnected")
            except Exception as e:
                logger.error(f"WS handler error: {e}")
        
        try:
            self._server = await websockets.serve(
                handler,
                "0.0.0.0",
                WS_PORT,
                ping_interval=20,
                ping_timeout=10
            )
            logger.info(f"Federation WS Server listening on port {WS_PORT}")
        except Exception as e:
            logger.error(f"Failed to start WS server: {e}")
    
    def get_best_node(self, task_type: str = None) -> str:
        """
        Wählt besten Node für Task basierend auf Load.
        Returns node_id des besten Nodes.
        """
        candidates = []
        
        # Lokaler Node
        import psutil
        self.local_metrics.cpu_percent = psutil.cpu_percent()
        self.local_metrics.memory_percent = psutil.virtual_memory().percent
        candidates.append((self.node_id, self.local_metrics.load_score))
        
        # Peer Nodes
        for peer_id, peer in self.peers.items():
            if peer.connected and (time.time() - peer.metrics.last_update) < 30:
                candidates.append((peer_id, peer.metrics.load_score))
        
        if not candidates:
            return self.node_id
        
        # Sortiere nach Load Score (aufsteigend)
        candidates.sort(key=lambda x: x[1])
        
        best_node = candidates[0][0]
        logger.debug(f"Best node for task: {best_node} (score: {candidates[0][1]:.2f})")
        
        return best_node
    
    async def route_task(self, task_type: str, task_data: dict, timeout: float = 60.0) -> dict:
        """
        Routet Task zum besten Node und wartet auf Ergebnis.
        """
        import uuid
        task_id = str(uuid.uuid4())
        
        best_node = self.get_best_node(task_type)
        
        if best_node == self.node_id:
            # Lokal ausführen
            return await self._execute_local_task(task_type, task_data)
        
        # An Peer senden
        peer = self.peers.get(best_node)
        if not peer or not peer.connected:
            # Fallback zu lokal
            return await self._execute_local_task(task_type, task_data)
        
        # Create Future für Ergebnis
        future = asyncio.get_event_loop().create_future()
        self._pending_tasks[task_id] = future
        
        try:
            await peer.send_task(task_id, task_type, task_data)
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.error(f"Task {task_id} timeout")
            return {"status": "error", "message": "Task timeout"}
        finally:
            self._pending_tasks.pop(task_id, None)
    
    async def _execute_local_task(self, task_type: str, task_data: dict) -> dict:
        """Führt Task lokal aus"""
        handler = self._task_handlers.get(task_type)
        if handler:
            return await handler(task_data)
        return {"status": "error", "message": f"Unknown task type: {task_type}"}
    
    async def _handle_incoming_task(self, payload: dict):
        """Verarbeitet eingehenden Task von Peer"""
        task_id = payload.get("task_id")
        task_type = payload.get("task_type")
        task_data = payload.get("task_data", {})
        from_node = payload.get("node_id")
        
        logger.info(f"Received task {task_id} ({task_type}) from {from_node}")
        
        result = await self._execute_local_task(task_type, task_data)
        
        # Send result back
        peer = self.peers.get(from_node)
        if peer and peer.connected:
            msg = create_signed_request({
                "type": MessageType.TASK_RESULT,
                "node_id": self.node_id,
                "task_id": task_id,
                "result": result
            })
            await peer.send(msg)
    
    async def _handle_task_result(self, payload: dict):
        """Verarbeitet Task-Ergebnis von Peer"""
        task_id = payload.get("task_id")
        result = payload.get("result", {})
        
        future = self._pending_tasks.get(task_id)
        if future and not future.done():
            future.set_result(result)
    
    def register_task_handler(self, task_type: str, handler: Callable):
        """Registriert Handler für Task-Typ"""
        self._task_handlers[task_type] = handler
    
    def get_cluster_status(self) -> dict:
        """Gibt Cluster-Status zurück"""
        import psutil
        
        nodes = [{
            "id": self.node_id,
            "status": "online",
            "is_self": True,
            "load_score": self.local_metrics.load_score,
            "cpu_percent": psutil.cpu_percent(),
            "memory_percent": psutil.virtual_memory().percent
        }]
        
        for peer_id, peer in self.peers.items():
            nodes.append({
                "id": peer_id,
                "status": "online" if peer.connected else "offline",
                "is_self": False,
                "load_score": peer.metrics.load_score if peer.connected else 1.0,
                "cpu_percent": peer.metrics.cpu_percent,
                "memory_percent": peer.metrics.memory_percent,
                "last_seen": peer.metrics.last_update
            })
        
        return {
            "cluster_size": len(nodes),
            "online_nodes": sum(1 for n in nodes if n["status"] == "online"),
            "nodes": nodes
        }
    
    async def stop(self):
        """Stoppt Load Balancer"""
        self._running = False
        for peer in self.peers.values():
            await peer.disconnect()
        if self._server:
            self._server.close()


# =============================================================================
# Singleton Instance
# =============================================================================

federation_lb = FederationLoadBalancer()
