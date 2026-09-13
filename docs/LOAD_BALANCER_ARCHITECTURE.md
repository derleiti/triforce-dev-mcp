# TriForce Load Balancer Architecture

## Übersicht

```
                                    ┌─────────────────────────────────────────┐
                                    │           CLOUDFLARE (DNS/CDN)          │
                                    │         api.ailinux.me → Pool           │
                                    └──────────────────┬──────────────────────┘
                                                       │
                              ┌────────────────────────┼────────────────────────┐
                              │                        │                        │
                              ▼                        ▼                        ▼
                    ┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
                    │   LOAD BALANCER │      │   LOAD BALANCER │      │   LOAD BALANCER │
                    │   (Edge Node 1) │      │   (Edge Node 2) │      │   (Edge Node N) │
                    │   HAProxy/Nginx │      │   HAProxy/Nginx │      │   HAProxy/Nginx │
                    └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
                             │                        │                        │
              ┌──────────────┼──────────────┐         │         ┌──────────────┼──────────────┐
              │              │              │         │         │              │              │
              ▼              ▼              ▼         ▼         ▼              ▼              ▼
     ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
     │ SERVER NODE  │ │ SERVER NODE  │ │ SERVER NODE  │ │ SERVER NODE  │ │ SERVER NODE  │
     │ (Worker 1)   │ │ (Worker 2)   │ │ (Worker 3)   │ │ (Contributor)│ │ (Backup)     │
     │ Ollama Local │ │ Ollama Local │ │ API Proxy    │ │ GPU Compute  │ │ Failover     │
     └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
            │                │                │                │                │
            └────────────────┴────────────────┼────────────────┴────────────────┘
                                              │
                                              ▼
                                    ┌─────────────────────┐
                                    │     SERVER HUB      │
                                    │  (Hetzner Primary)  │
                                    │                     │
                                    │ • Model Registry    │
                                    │ • User Database     │
                                    │ • Memory/Prisma     │
                                    │ • Mesh Coordinator  │
                                    │ • Federation Master │
                                    └─────────────────────┘
```

## Komponenten

### 1. Load Balancer (Edge Layer)
- **Aufgabe**: Request-Verteilung, Health Checks, SSL Termination
- **Algorithmen**: Round Robin, Least Connections, Weighted, IP Hash
- **Health Checks**: HTTP /health endpoint alle 5s

### 2. Server Nodes (Worker Layer)
- **Typen**:
  - **Ollama Workers**: Lokale LLM Inference
  - **API Proxy**: Weiterleitung zu Cloud APIs (Anthropic, OpenAI, Google)
  - **Contributors**: User-gespendete GPU-Ressourcen
  - **Backup**: Hot-Standby für Failover

### 3. Server Hub (Coordination Layer)
- **Aufgaben**:
  - Zentrale Datenbank (Users, Tiers, API Keys)
  - Model Registry (welche Modelle wo verfügbar)
  - Memory/Prisma für Kontext-Speicherung
  - Mesh Brain Koordination
  - Federation Health Monitoring

## Request Flow

```
1. Client Request
   │
   ▼
2. Cloudflare DNS (GeoDNS → nächster Edge)
   │
   ▼
3. Load Balancer entscheidet:
   ├── Lokales Modell? → Ollama Worker
   ├── Cloud API? → API Proxy Node
   ├── GPU-intensiv? → Contributor Node
   └── Überlastet? → Backup Node
   │
   ▼
4. Server Node verarbeitet:
   ├── Auth Check → Hub (JWT Verify)
   ├── Rate Limit → Hub (Tier Check)
   ├── Model Check → Hub (Registry)
   └── Response → Client
   │
   ▼
5. Async an Hub:
   ├── Usage Logging
   ├── Memory Update
   └── Health Report
```

## Implementierung

## HAProxy Konfiguration

```haproxy
# /etc/haproxy/haproxy.cfg

global
    maxconn 50000
    log stdout format raw local0

defaults
    mode http
    timeout connect 10s
    timeout client 300s    # Lange Timeouts für LLM Streaming
    timeout server 300s
    option httplog
    option forwardfor

# Frontend: Eingehende Requests
frontend triforce_frontend
    bind *:443 ssl crt /etc/ssl/triforce.pem
    
    # Health Check Endpoint direkt beantworten
    acl is_health path /health
    use_backend health_backend if is_health
    
    # Model-basiertes Routing
    acl is_ollama hdr(X-Model) -m reg ^(llama|qwen|mistral|phi)
    acl is_claude hdr(X-Model) -m reg ^claude
    acl is_gemini hdr(X-Model) -m reg ^gemini
    acl is_gpt hdr(X-Model) -m reg ^gpt
    
    use_backend ollama_workers if is_ollama
    use_backend api_proxy if is_claude or is_gemini or is_gpt
    default_backend hub_direct

# Backend: Ollama Workers (lokale Modelle)
backend ollama_workers
    balance leastconn
    option httpchk GET /health
    
    server worker1 10.0.1.10:9000 check weight 100
    server worker2 10.0.1.11:9000 check weight 100
    server worker3 10.0.1.12:9000 check weight 50    # Schwächere Hardware
    server contributor1 10.0.2.1:9000 check weight 80 backup
    
# Backend: API Proxy (Cloud APIs)
backend api_proxy
    balance roundrobin
    option httpchk GET /health
    
    server proxy1 10.0.1.20:9000 check
    server proxy2 10.0.1.21:9000 check
    server hub 138.201.50.230:9000 check backup    # Hub als Fallback

# Backend: Direkt zum Hub
backend hub_direct
    server hub 138.201.50.230:9000 check

# Health Check Backend
backend health_backend
    server local 127.0.0.1:8080
```

## Nginx Alternative

```nginx
# /etc/nginx/nginx.conf

upstream ollama_pool {
    least_conn;
    server 10.0.1.10:9000 weight=100;
    server 10.0.1.11:9000 weight=100;
    server 10.0.1.12:9000 weight=50;
    server 10.0.2.1:9000 backup;    # Contributor
}

upstream api_proxy_pool {
    server 10.0.1.20:9000;
    server 10.0.1.21:9000;
    server 138.201.50.230:9000 backup;    # Hub Fallback
}

upstream hub {
    server 138.201.50.230:9000;
}

server {
    listen 443 ssl http2;
    server_name api.ailinux.me;
    
    ssl_certificate /etc/ssl/triforce.pem;
    ssl_certificate_key /etc/ssl/triforce.key;
    
    # Model-basiertes Routing
    location /v1/chat {
        set $backend "hub";
        
        if ($http_x_model ~* "^(llama|qwen|mistral|phi)") {
            set $backend "ollama_pool";
        }
        if ($http_x_model ~* "^(claude|gemini|gpt)") {
            set $backend "api_proxy_pool";
        }
        
        proxy_pass http://$backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # Streaming Support
        proxy_buffering off;
        chunked_transfer_encoding on;
    }
    
    location /health {
        return 200 '{"status":"ok"}';
        add_header Content-Type application/json;
    }
}
```

## Python Server Node Implementation

```python
# /home/zombie/workspace/triforce/app/services/server_node.py
"""
TriForce Server Node - Worker im Load-Balanced Cluster
Kommuniziert mit Load Balancer (upstream) und Hub (downstream)
"""

import asyncio
import aiohttp
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List
from datetime import datetime
import json

logger = logging.getLogger(__name__)


class NodeRole(str, Enum):
    OLLAMA_WORKER = "ollama_worker"      # Lokale LLM Inference
    API_PROXY = "api_proxy"               # Cloud API Weiterleitung
    CONTRIBUTOR = "contributor"           # User-gespendete Ressourcen
    BACKUP = "backup"                     # Hot-Standby
    HUB = "hub"                          # Zentrale Koordination


class NodeStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    OFFLINE = "offline"
    STARTING = "starting"


@dataclass
class ServerNode:
    """Repräsentiert einen Server Node im Cluster"""
    
    node_id: str
    role: NodeRole
    host: str
    port: int = 9000
    
    # Hub-Verbindung
    hub_url: str = "https://api.ailinux.me"
    hub_token: Optional[str] = None
    
    # Status
    status: NodeStatus = NodeStatus.STARTING
    last_heartbeat: Optional[datetime] = None
    consecutive_failures: int = 0
    max_failures: int = 3
    
    # Capabilities
    models: List[str] = field(default_factory=list)
    max_concurrent: int = 10
    current_load: int = 0
    
    # Metrics
    requests_total: int = 0
    requests_failed: int = 0
    avg_latency_ms: float = 0.0

    async def register_with_hub(self) -> bool:
        """Registriert diesen Node beim Hub"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "node_id": self.node_id,
                    "role": self.role.value,
                    "host": self.host,
                    "port": self.port,
                    "models": self.models,
                    "max_concurrent": self.max_concurrent
                }
                
                async with session.post(
                    f"{self.hub_url}/v1/federation/register",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.hub_token}"}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.hub_token = data.get("node_token", self.hub_token)
                        self.status = NodeStatus.HEALTHY
                        logger.info(f"Node {self.node_id} registered with hub")
                        return True
                    else:
                        logger.error(f"Registration failed: {resp.status}")
                        return False
                        
        except Exception as e:
            logger.error(f"Failed to register with hub: {e}")
            return False

    async def send_heartbeat(self) -> bool:
        """Sendet Heartbeat an Hub"""
        try:
            async with aiohttp.ClientSession() as session:
                payload = {
                    "node_id": self.node_id,
                    "status": self.status.value,
                    "current_load": self.current_load,
                    "metrics": {
                        "requests_total": self.requests_total,
                        "requests_failed": self.requests_failed,
                        "avg_latency_ms": self.avg_latency_ms
                    }
                }
                
                async with session.post(
                    f"{self.hub_url}/v1/federation/heartbeat",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.hub_token}"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        self.last_heartbeat = datetime.now()
                        self.consecutive_failures = 0
                        return True
                    else:
                        self.consecutive_failures += 1
                        return False
                        
        except Exception as e:
            self.consecutive_failures += 1
            logger.warning(f"Heartbeat failed: {e}")
            
            if self.consecutive_failures >= self.max_failures:
                self.status = NodeStatus.DEGRADED
                
            return False

    async def forward_to_hub(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """Leitet Request an Hub weiter (für Auth, Rate Limit, etc.)"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.hub_url}/v1/internal/validate",
                    json=request,
                    headers={"Authorization": f"Bearer {self.hub_token}"}
                ) as resp:
                    return await resp.json()
        except Exception as e:
            logger.error(f"Hub forward failed: {e}")
            return {"error": str(e), "valid": False}

    async def report_completion(self, request_id: str, metrics: Dict[str, Any]):
        """Meldet abgeschlossenen Request an Hub (async)"""
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{self.hub_url}/v1/federation/completion",
                    json={
                        "node_id": self.node_id,
                        "request_id": request_id,
                        "metrics": metrics
                    },
                    headers={"Authorization": f"Bearer {self.hub_token}"}
                )
        except Exception as e:
            logger.warning(f"Completion report failed: {e}")


class LoadBalancerClient:
    """Client für Kommunikation mit vorgeschaltetem Load Balancer"""
    
    def __init__(self, node: ServerNode):
        self.node = node
        self.lb_healthy = True
    
    async def health_check_response(self) -> Dict[str, Any]:
        """Antwort für Load Balancer Health Check"""
        return {
            "status": "ok" if self.node.status == NodeStatus.HEALTHY else "degraded",
            "node_id": self.node.node_id,
            "role": self.node.role.value,
            "load": self.node.current_load,
            "max_load": self.node.max_concurrent,
            "models": self.node.models
        }
    
    def can_accept_request(self) -> bool:
        """Prüft ob Node neue Requests annehmen kann"""
        return (
            self.node.status in [NodeStatus.HEALTHY, NodeStatus.DEGRADED] and
            self.node.current_load < self.node.max_concurrent
        )
```

## Server Node Request Handler

```python
# Fortsetzung server_node.py

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
import time

app = FastAPI(title="TriForce Server Node")

# Global node instance
node: Optional[ServerNode] = None
lb_client: Optional[LoadBalancerClient] = None


@app.on_event("startup")
async def startup():
    global node, lb_client
    
    node = ServerNode(
        node_id=os.getenv("NODE_ID", "worker-1"),
        role=NodeRole(os.getenv("NODE_ROLE", "ollama_worker")),
        host=os.getenv("NODE_HOST", "0.0.0.0"),
        hub_url=os.getenv("HUB_URL", "https://api.ailinux.me"),
        hub_token=os.getenv("HUB_TOKEN")
    )
    
    lb_client = LoadBalancerClient(node)
    
    # Beim Hub registrieren
    await node.register_with_hub()
    
    # Heartbeat-Loop starten
    asyncio.create_task(heartbeat_loop())


async def heartbeat_loop():
    """Periodischer Heartbeat an Hub"""
    while True:
        await node.send_heartbeat()
        await asyncio.sleep(30)


@app.get("/health")
async def health_check():
    """Load Balancer Health Check Endpoint"""
    return await lb_client.health_check_response()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Hauptendpoint für Chat Requests"""
    
    if not lb_client.can_accept_request():
        raise HTTPException(503, "Node at capacity")
    
    node.current_load += 1
    start_time = time.time()
    
    try:
        body = await request.json()
        request_id = body.get("request_id", str(time.time()))
        model = body.get("model", "")
        
        # 1. Auth & Rate Limit beim Hub prüfen
        auth_header = request.headers.get("Authorization", "")
        validation = await node.forward_to_hub({
            "auth": auth_header,
            "model": model,
            "endpoint": "/v1/chat/completions"
        })
        
        if not validation.get("valid"):
            raise HTTPException(401, validation.get("error", "Unauthorized"))
        
        # 2. Request verarbeiten (je nach Node Role)
        if node.role == NodeRole.OLLAMA_WORKER:
            response = await process_ollama(body)
        elif node.role == NodeRole.API_PROXY:
            response = await proxy_to_cloud(body, model)
        else:
            raise HTTPException(500, f"Unknown role: {node.role}")
        
        # 3. Metrics aktualisieren
        latency = (time.time() - start_time) * 1000
        node.requests_total += 1
        node.avg_latency_ms = (node.avg_latency_ms + latency) / 2
        
        # 4. Async Completion Report an Hub
        asyncio.create_task(node.report_completion(request_id, {
            "latency_ms": latency,
            "tokens_in": response.get("usage", {}).get("prompt_tokens", 0),
            "tokens_out": response.get("usage", {}).get("completion_tokens", 0),
            "model": model
        }))
        
        return response
        
    except Exception as e:
        node.requests_failed += 1
        raise HTTPException(500, str(e))
        
    finally:
        node.current_load -= 1


async def process_ollama(body: dict) -> dict:
    """Verarbeitet Request mit lokalem Ollama"""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:11434/api/chat",
            json={
                "model": body.get("model"),
                "messages": body.get("messages"),
                "stream": False
            }
        ) as resp:
            data = await resp.json()
            return {
                "choices": [{
                    "message": {"role": "assistant", "content": data.get("message", {}).get("content", "")}
                }],
                "model": body.get("model"),
                "usage": {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0)
                }
            }


async def proxy_to_cloud(body: dict, model: str) -> dict:
    """Leitet Request an Cloud API weiter"""
    
    # API Key vom Hub holen (cached)
    api_key = await get_api_key_for_model(model)
    
    if "claude" in model:
        return await proxy_anthropic(body, api_key)
    elif "gpt" in model:
        return await proxy_openai(body, api_key)
    elif "gemini" in model:
        return await proxy_google(body, api_key)
    else:
        raise ValueError(f"Unknown cloud model: {model}")
```

## Hub Federation Endpoints

```python
# /home/zombie/workspace/triforce/app/api/federation.py
"""
Hub-seitige Endpoints für Federation Management
"""

from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, List
from datetime import datetime, timedelta
import asyncio

router = APIRouter(prefix="/v1/federation", tags=["federation"])

# In-Memory Node Registry (in Production: Redis/DB)
registered_nodes: Dict[str, dict] = {}
node_health: Dict[str, datetime] = {}


@router.post("/register")
async def register_node(payload: dict):
    """Node registriert sich beim Hub"""
    node_id = payload.get("node_id")
    
    if not node_id:
        raise HTTPException(400, "node_id required")
    
    # Node Token generieren
    node_token = generate_node_token(node_id)
    
    registered_nodes[node_id] = {
        "role": payload.get("role"),
        "host": payload.get("host"),
        "port": payload.get("port"),
        "models": payload.get("models", []),
        "max_concurrent": payload.get("max_concurrent", 10),
        "registered_at": datetime.now().isoformat(),
        "status": "healthy"
    }
    
    node_health[node_id] = datetime.now()
    
    logger.info(f"Node registered: {node_id} ({payload.get('role')})")
    
    return {
        "status": "registered",
        "node_token": node_token,
        "hub_config": {
            "heartbeat_interval": 30,
            "max_failures": 3
        }
    }


@router.post("/heartbeat")
async def node_heartbeat(payload: dict):
    """Node sendet Heartbeat"""
    node_id = payload.get("node_id")
    
    if node_id not in registered_nodes:
        raise HTTPException(404, "Node not registered")
    
    # Update health timestamp
    node_health[node_id] = datetime.now()
    
    # Update node status & metrics
    registered_nodes[node_id].update({
        "status": payload.get("status", "healthy"),
        "current_load": payload.get("current_load", 0),
        "last_heartbeat": datetime.now().isoformat(),
        "metrics": payload.get("metrics", {})
    })
    
    return {"status": "ok", "next_heartbeat": 30}


@router.post("/completion")
async def report_completion(payload: dict):
    """Node meldet abgeschlossenen Request"""
    node_id = payload.get("node_id")
    metrics = payload.get("metrics", {})
    
    # Usage logging (async an DB)
    asyncio.create_task(log_usage(
        node_id=node_id,
        request_id=payload.get("request_id"),
        **metrics
    ))
    
    return {"status": "logged"}


@router.get("/nodes")
async def list_nodes():
    """Listet alle registrierten Nodes"""
    now = datetime.now()
    
    nodes = []
    for node_id, info in registered_nodes.items():
        last_seen = node_health.get(node_id, datetime.min)
        age = (now - last_seen).total_seconds()
        
        # Status basierend auf Heartbeat
        if age > 90:
            status = "offline"
        elif age > 60:
            status = "degraded"
        else:
            status = info.get("status", "unknown")
        
        nodes.append({
            "node_id": node_id,
            "status": status,
            "last_seen_seconds": int(age),
            **info
        })
    
    return {"nodes": nodes, "total": len(nodes)}


@router.get("/route/{model}")
async def get_route_for_model(model: str):
    """Gibt beste Node(s) für ein Model zurück (für dynamisches LB)"""
    
    candidates = []
    
    for node_id, info in registered_nodes.items():
        # Prüfe ob Node das Model hat
        if model in info.get("models", []) or info.get("role") == "api_proxy":
            last_seen = node_health.get(node_id, datetime.min)
            age = (datetime.now() - last_seen).total_seconds()
            
            if age < 60:  # Nur healthy nodes
                load_percent = info.get("current_load", 0) / info.get("max_concurrent", 10)
                candidates.append({
                    "node_id": node_id,
                    "host": info.get("host"),
                    "port": info.get("port"),
                    "load_percent": load_percent,
                    "score": 1.0 - load_percent  # Höher = besser
                })
    
    # Sortiere nach Score (niedrigste Last zuerst)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    return {
        "model": model,
        "candidates": candidates[:3],  # Top 3
        "recommended": candidates[0] if candidates else None
    }
```

## TLA+ Spezifikation für Load Balancer

```tla
---------------------------- MODULE LoadBalancer ----------------------------
(* TLA+ Specification for TriForce Load Balancer *)

EXTENDS Naturals, FiniteSets, Sequences

CONSTANTS 
    Nodes,              \* Set of backend nodes
    MaxLoad,            \* Max concurrent requests per node
    MaxQueueSize        \* Max requests in LB queue

VARIABLES
    nodeLoad,           \* Function: Node -> current load
    nodeStatus,         \* Function: Node -> {healthy, degraded, offline}
    requestQueue,       \* Sequence of pending requests
    routedTo            \* Last routing decision

vars == <<nodeLoad, nodeStatus, requestQueue, routedTo>>

Status == {"healthy", "degraded", "offline"}

TypeInvariant ==
    /\ nodeLoad \in [Nodes -> 0..MaxLoad]
    /\ nodeStatus \in [Nodes -> Status]
    /\ Len(requestQueue) <= MaxQueueSize

Init ==
    /\ nodeLoad = [n \in Nodes |-> 0]
    /\ nodeStatus = [n \in Nodes |-> "healthy"]
    /\ requestQueue = <<>>
    /\ routedTo = CHOOSE n \in Nodes : TRUE

\* Find node with least connections (that's healthy)
LeastLoadedNode ==
    LET healthy == {n \in Nodes : nodeStatus[n] = "healthy" /\ nodeLoad[n] < MaxLoad}
    IN IF healthy # {}
       THEN CHOOSE n \in healthy : \A m \in healthy : nodeLoad[n] <= nodeLoad[m]
       ELSE CHOOSE n \in Nodes : TRUE  \* Fallback

\* New request arrives
RequestArrives ==
    /\ Len(requestQueue) < MaxQueueSize
    /\ requestQueue' = Append(requestQueue, "request")
    /\ UNCHANGED <<nodeLoad, nodeStatus, routedTo>>

\* Route request to best node
RouteRequest ==
    /\ Len(requestQueue) > 0
    /\ LET target == LeastLoadedNode
       IN /\ nodeStatus[target] \in {"healthy", "degraded"}
          /\ nodeLoad[target] < MaxLoad
          /\ nodeLoad' = [nodeLoad EXCEPT ![target] = @ + 1]
          /\ requestQueue' = Tail(requestQueue)
          /\ routedTo' = target
          /\ UNCHANGED nodeStatus

\* Request completes on a node
RequestCompletes(node) ==
    /\ node \in Nodes
    /\ nodeLoad[node] > 0
    /\ nodeLoad' = [nodeLoad EXCEPT ![node] = @ - 1]
    /\ UNCHANGED <<nodeStatus, requestQueue, routedTo>>

\* Node becomes unhealthy
NodeFails(node) ==
    /\ node \in Nodes
    /\ nodeStatus[node] = "healthy"
    /\ nodeStatus' = [nodeStatus EXCEPT ![node] = "degraded"]
    /\ UNCHANGED <<nodeLoad, requestQueue, routedTo>>

\* Node recovers
NodeRecovers(node) ==
    /\ node \in Nodes
    /\ nodeStatus[node] \in {"degraded", "offline"}
    /\ nodeStatus' = [nodeStatus EXCEPT ![node] = "healthy"]
    /\ UNCHANGED <<nodeLoad, requestQueue, routedTo>>

Next ==
    \/ RequestArrives
    \/ RouteRequest
    \/ \E n \in Nodes : RequestCompletes(n)
    \/ \E n \in Nodes : NodeFails(n)
    \/ \E n \in Nodes : NodeRecovers(n)

Spec == Init /\ [][Next]_vars

\* SAFETY: No node is overloaded
NoOverload == \A n \in Nodes : nodeLoad[n] <= MaxLoad

\* SAFETY: Requests only routed to available nodes
OnlyRouteToAvailable ==
    nodeStatus[routedTo] \in {"healthy", "degraded"}

\* LIVENESS: Eventually all requests are processed (with fairness)
EventuallyProcessed == 
    <>(Len(requestQueue) = 0)

=============================================================================
```

## Deployment mit Docker Compose

```yaml
# docker-compose.lb.yml
version: '3.8'

services:
  # HAProxy Load Balancer
  loadbalancer:
    image: haproxy:2.8
    ports:
      - "443:443"
      - "80:80"
      - "8404:8404"  # Stats
    volumes:
      - ./haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro
      - ./certs:/etc/ssl/certs:ro
    depends_on:
      - worker1
      - worker2
      - api-proxy
    networks:
      - triforce-net
    restart: unless-stopped

  # Ollama Worker 1
  worker1:
    build: ./server-node
    environment:
      - NODE_ID=worker-1
      - NODE_ROLE=ollama_worker
      - HUB_URL=https://api.ailinux.me
      - HUB_TOKEN=${HUB_TOKEN}
      - OLLAMA_HOST=ollama1:11434
    depends_on:
      - ollama1
    networks:
      - triforce-net
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  ollama1:
    image: ollama/ollama:latest
    volumes:
      - ollama1-data:/root/.ollama
    networks:
      - triforce-net
    deploy:
      resources:
        reservations:
          devices:
            - capabilities: [gpu]

  # Ollama Worker 2
  worker2:
    build: ./server-node
    environment:
      - NODE_ID=worker-2
      - NODE_ROLE=ollama_worker
      - HUB_URL=https://api.ailinux.me
      - HUB_TOKEN=${HUB_TOKEN}
      - OLLAMA_HOST=ollama2:11434
    depends_on:
      - ollama2
    networks:
      - triforce-net

  ollama2:
    image: ollama/ollama:latest
    volumes:
      - ollama2-data:/root/.ollama
    networks:
      - triforce-net

  # API Proxy (für Cloud APIs)
  api-proxy:
    build: ./server-node
    environment:
      - NODE_ID=api-proxy-1
      - NODE_ROLE=api_proxy
      - HUB_URL=https://api.ailinux.me
      - HUB_TOKEN=${HUB_TOKEN}
    networks:
      - triforce-net

networks:
  triforce-net:
    driver: bridge

volumes:
  ollama1-data:
  ollama2-data:
```

## Zusammenfassung: Request Flow

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           COMPLETE REQUEST FLOW                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. CLIENT                                                               │
│     │                                                                    │
│     │ POST /v1/chat/completions                                         │
│     │ Authorization: Bearer <user_jwt>                                  │
│     │ X-Model: llama3.2                                                 │
│     ▼                                                                    │
│  2. CLOUDFLARE DNS                                                       │
│     │                                                                    │
│     │ GeoDNS → nächster Edge                                            │
│     ▼                                                                    │
│  3. LOAD BALANCER (HAProxy)                                             │
│     │                                                                    │
│     │ X-Model: llama3.2 → Backend: ollama_workers                       │
│     │ Health Check: worker1 ✓, worker2 ✓, worker3 ✗                     │
│     │ Algorithm: least_conn → worker1 (load: 2/10)                      │
│     ▼                                                                    │
│  4. SERVER NODE (worker1)                                                │
│     │                                                                    │
│     ├─► HUB: Validate JWT + Check Tier + Rate Limit                     │
│     │   └─► Response: {valid: true, tier: "pro", remaining: 249999}     │
│     │                                                                    │
│     ├─► OLLAMA: Generate Response                                        │
│     │   └─► Response: {content: "...", tokens: 150}                     │
│     │                                                                    │
│     ├─► HUB (async): Report Completion                                   │
│     │   └─► {node_id, latency_ms, tokens, model}                        │
│     │                                                                    │
│     ▼                                                                    │
│  5. RESPONSE → CLIENT                                                    │
│     │                                                                    │
│     │ {choices: [{message: {...}}], usage: {...}}                       │
│     ▼                                                                    │
│  ✓ DONE                                                                  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```
