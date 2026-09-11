"""
Federation Routes - Server-to-Server API
"""
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Any, Optional
import socket
import psutil

from ..services.server_federation import federation, NodeRole, FEDERATION_NODES

router = APIRouter(prefix="/federation", tags=["Federation"])

# Get local node ID - needs to be defined early for all endpoints
_hostname = socket.gethostname()
LOCAL_NODE_ID = "backup" if "backup" in _hostname.lower() else \
                "zombie-pc" if "zombie" in _hostname.lower() else "hetzner"


def _local_ipv4_addresses() -> set[str]:
    """Return addresses owned by this host, used to identify the local TCP proxy hop."""
    addresses = {"127.0.0.1"}
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family == socket.AF_INET:
                addresses.add(entry.address)
    return addresses


def _federation_auth_client_ip(peer_id: str, observed_ip: Optional[str]) -> Optional[str]:
    """Recover a peer's WG identity only when the TCP hop is demonstrably local.

    systemd-socket-proxyd cannot preserve the original source address.  The
    federation listener is bound to the WireGuard address and forwards to the
    private backend, so FastAPI sees an address owned by this host.  In that
    narrowly defined case, use the peer's canonical WireGuard IP for the
    vault's existing allowed_ips check.  Remote/non-local hops are never
    rewritten. PSK signature and node token validation still run first.
    """
    if not observed_ip or observed_ip not in _local_ipv4_addresses():
        return observed_ip
    peer = FEDERATION_NODES.get(peer_id, {})
    return peer.get("vpn_ip") or observed_ip


class ContributorRegisterRequest(BaseModel):
    hardware: Dict[str, Any]
    capabilities: List[str]


class ContributorRegisterResponse(BaseModel):
    node_id: str
    status: str
    message: str


@router.get("/status")
async def get_federation_status():
    """Get federation status and all nodes"""
    return federation.get_status()


@router.get("/nodes")
async def list_federation_nodes():
    """List all federation nodes"""
    return {
        "nodes": [node.to_dict() for node in federation.nodes.values()],
        "count": len(federation.nodes)
    }


@router.post("/contributor/register", response_model=ContributorRegisterResponse)
async def register_contributor(
    request: ContributorRegisterRequest,
    authorization: str = Header(None)
):
    """
    Register client as contributor node.
    
    Client shares hardware resources with the mesh.
    """
    # TODO: Extract client_id from auth token
    import uuid
    client_id = str(uuid.uuid4())[:8]
    
    node = await federation.register_contributor(
        client_id=client_id,
        hardware=request.hardware,
        capabilities=request.capabilities
    )
    
    return ContributorRegisterResponse(
        node_id=node.node_id,
        status="registered",
        message=f"Registered as contributor with {len(request.capabilities)} models"
    )


@router.post("/heartbeat")
async def receive_heartbeat(
    x_federation_key: str = Header(None, alias="X-Federation-Key"),
    x_node_id: str = Header(None, alias="X-Node-ID")
):
    """
    Receive heartbeat from another federation node.
    """
    if x_node_id and x_node_id in federation.nodes:
        node = federation.nodes[x_node_id]
        from datetime import datetime
        node.last_heartbeat = datetime.now()
        node.status = "healthy"
        return {"status": "ok", "node_id": x_node_id}
    
    return {"status": "unknown_node"}


@router.post("/health")
@router.get("/health")
async def federation_health_check():
    """
    Health check endpoint for federation nodes.
    Used by peers to verify connectivity.
    """
    import time
    return {
        "status": "ok",
        "node_id": LOCAL_NODE_ID,
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "timestamp": int(time.time())
    }


@router.get("/available")
async def get_available_node(model: str = None):
    """Find available node for processing"""
    node = federation.get_available_node(model)
    if node:
        return node.to_dict()
    raise HTTPException(status_code=503, detail="No available nodes")


# =============================================================================
# Load Balancer Endpoints - NEU
# =============================================================================

from ..services.server_federation import lb_integration


@router.get("/lb/backend/{model}")
async def get_backend_for_model(model: str):
    """
    Get best backend for a specific model.
    Used by dynamic load balancers (HAProxy, Nginx, Cloudflare Workers)
    """
    backend = lb_integration.get_backend_for_model(model)
    if not backend:
        raise HTTPException(status_code=503, detail=f"No backend available for model: {model}")
    return backend


@router.get("/lb/haproxy")
async def get_haproxy_config():
    """
    Generate HAProxy server state config.
    Can be used with HAProxy Runtime API or config reload.
    """
    config = lb_integration.get_haproxy_server_state()
    return {
        "format": "haproxy_server_state",
        "config": config
    }


@router.get("/lb/nginx")
async def get_nginx_config():
    """
    Generate Nginx upstream config.
    Save to file and reload nginx.
    """
    config = lb_integration.get_nginx_upstream()
    return {
        "format": "nginx_upstream",
        "config": config
    }


@router.get("/lb/cloudflare")
async def get_cloudflare_config():
    """
    Config for Cloudflare Worker-based load balancing.
    """
    return lb_integration.get_cloudflare_worker_config()


@router.get("/lb/weights")
async def get_all_weights():
    """
    Get current weights for all backends.
    Useful for monitoring and debugging.
    """
    weights = {}
    for node_id, node in federation.nodes.items():
        weights[node_id] = {
            "weight": lb_integration._calculate_weight(node),
            "status": node.status.value,
            "load": f"{node.current_load}/{node.max_concurrent}",
            "models": node.models[:5]  # First 5 models
        }
    return {"weights": weights, "count": len(weights)}


# =============================================================================
# WebSocket Endpoint für Federation Peer Communication
# =============================================================================

from fastapi import WebSocket, WebSocketDisconnect
from ..services.server_federation import verify_signed_request, create_signed_request, FEDERATION_NODES

# Store active peer connections
_peer_connections: dict = {}


@router.websocket("/ws")
async def federation_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for federation peer-to-peer communication.
    
    Protocol:
    1. Client sends HELLO with node_id and signature (may be wrapped in signed request)
    2. Server validates and sends HELLO_ACK
    3. Bidirectional message exchange begins
    """
    await websocket.accept()
    peer_id = None
    
    try:
        # Wait for HELLO message
        data = await websocket.receive_json()
        
        # Handle both signed and unsigned formats
        # Signed format: {"data": {"type": "hello", ...}, "signature": "...", "timestamp": "..."}
        # Plain format: {"type": "hello", ...}
        if "data" in data and isinstance(data.get("data"), dict):
            # Signed request - extract inner data
            inner_data = data["data"]
            msg_type = inner_data.get("type")
            peer_id = inner_data.get("node_id")
        else:
            # Plain format
            msg_type = data.get("type")
            peer_id = data.get("node_id")
        
        if msg_type != "hello":
            await websocket.close(code=4001, reason="Expected HELLO message")
            return
        
        # --- Authentifizierung (verschaerft 2026-08-16) --------------------
        # Vorher: ein fehlgeschlagener Token fiel auf "peer_id in FEDERATION_NODES"
        # zurueck. Damit kam jeder rein, der sich "hetzner"/"backup"/"zombie-pc"
        # nannte -- ohne jedes Geheimnis. Jetzt gilt:
        #   1. HELLO MUSS eine gueltige PSK-Signatur tragen (harte Schranke).
        #   2. Ist der Node im lokalen Vault registriert, MUSS zusaetzlich sein
        #      Token stimmen. Nodes ohne eigenen Vault akzeptieren Schritt 1.
        import logging as _logging
        _authlog = _logging.getLogger("ailinux.federation.ws")
        _client_ip = websocket.client.host if websocket.client else None

        if not ("data" in data and isinstance(data.get("data"), dict)):
            _authlog.warning(f"HELLO ohne Signaturhuelle abgewiesen: {peer_id} von {_client_ip}")
            await websocket.close(code=4003, reason="Unsigned HELLO rejected")
            return

        if verify_signed_request(data) is None:
            _authlog.warning(f"HELLO mit ungueltiger Signatur abgewiesen: {peer_id} von {_client_ip}")
            await websocket.close(code=4003, reason="Invalid signature")
            return

        from ..services.federation_vault import get_federation_vault
        vault = get_federation_vault()
        peer_token = data["data"].get("token", "")

        require_token = LOCAL_NODE_ID == "hetzner" or bool(vault.nodes)
        if require_token:
            # Der Hub MUSS immer ueber den Vault authentifizieren. Damit kann
            # eine leere/defekte Vault-Datei nicht still auf PSK-only
            # herunterstufen. Hosts mit vorhandenen Eintraegen verhalten sich
            # ebenfalls fail-closed.
            if vault.get_node(peer_id) is None:
                _authlog.warning(f"Nicht registrierter Node abgewiesen: {peer_id} von {_client_ip}")
                await websocket.close(code=4003, reason="Node not registered")
                return
            if not (peer_token and vault.verify_token(peer_id, peer_token, _federation_auth_client_ip(peer_id, _client_ip))):
                _authlog.warning(f"Token-Pruefung fehlgeschlagen, Peer abgewiesen: {peer_id} von {_client_ip}")
                await websocket.close(code=4003, reason="Invalid or missing node token")
                return
        else:
            # Host ohne eigenen Vault (die Nodes): gueltige PSK-Signatur genuegt.
            _authlog.info(f"Kein lokaler Vault - Peer {peer_id} allein ueber PSK-Signatur zugelassen")
        
        # Store connection
        _peer_connections[peer_id] = websocket
        
        # Send HELLO_ACK
        await websocket.send_json(create_signed_request({
            "type": "hello_ack",
            "node_id": LOCAL_NODE_ID,
            "status": "connected",
            "timestamp": int(__import__("time").time())
        }))
        
        from ..services.federation_websocket import federation_lb
        
        # Log connection
        import logging
        logger = logging.getLogger("ailinux.federation.ws")
        logger.info(f"Federation peer connected: {peer_id}")
        
        # Message loop
        while True:
            raw_msg = await websocket.receive_json()
            logger.info(f"WS Route received from {peer_id}: {str(raw_msg)[:150]}")
            
            # Signatur und Identitaet bleiben nach dem HELLO fuer jede
            # Nachricht verpflichtend. Sonst waere der Handshake sicher,
            # der anschliessende Datenpfad aber wieder offen.
            msg = verify_signed_request(raw_msg)
            if msg is None:
                logger.warning(f"Ungueltige/unsignierte Nachricht von {peer_id} abgewiesen")
                await websocket.close(code=4003, reason="Invalid message signature")
                return
            if msg.get("node_id") != peer_id:
                logger.warning(
                    f"Node-ID-Wechsel auf bestehender Federation-Verbindung abgewiesen: "
                    f"{peer_id} -> {msg.get('node_id')}"
                )
                await websocket.close(code=4003, reason="Node identity mismatch")
                return
            
            msg_type = msg.get("type", "unknown")
            
            # Handle different message types
            if msg_type == "heartbeat":
                await websocket.send_json(create_signed_request({
                    "type": "heartbeat_ack",
                    "node_id": LOCAL_NODE_ID,
                    "timestamp": int(__import__("time").time())}))
            
            elif msg_type == "status_update":
                # Update peer metrics in federation_lb
                if hasattr(federation_lb, 'peers') and peer_id in federation_lb.peers:
                    peer = federation_lb.peers[peer_id]
                    if "metrics" in msg:
                        peer.metrics.cpu_percent = msg["metrics"].get("cpu", 0)
                        peer.metrics.memory_percent = msg["metrics"].get("memory", 0)
                        peer.metrics.active_requests = msg["metrics"].get("active_requests", 0)
            
            elif msg_type == "task_submit":
                # Handle incoming task from peer
                await websocket.send_json(create_signed_request({
                    "type": "task_ack",
                    "node_id": LOCAL_NODE_ID,
                    "task_id": msg.get("task_id"),
                    "status": "received"
                }))
            
            elif msg_type == "task_result":
                # Handle task result from peer
                pass
            
    except WebSocketDisconnect:
        pass
    except Exception as e:
        import logging
        logging.getLogger("ailinux.federation.ws").error(f"Federation WS error: {e}")
    finally:
        # Cleanup
        if peer_id and peer_id in _peer_connections:
            del _peer_connections[peer_id]
            import logging
            logging.getLogger("ailinux.federation.ws").info(f"Federation peer disconnected: {peer_id}")
