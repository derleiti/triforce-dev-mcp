"""
Distributed Compute Service - Client Outsourcing
=================================================

Verteilt rechenintensive Tasks an verbundene Clients.
Clients stellen ihre GPU/CPU-Leistung dem Netzwerk zur Verfügung.

Architektur:
1. Server hat Task Queue mit ausstehenden Arbeiten
2. Clients verbinden sich via WebSocket und melden Kapazität
3. Server verteilt Tasks basierend auf Client-Fähigkeiten
4. Ergebnisse werden gesammelt und zusammengeführt

Use Cases:
- Embedding-Berechnung für große Dokumentenmengen
- Batch-Inferenz (Sentiment, Classification)
- Image Processing (CLIP, Feature Extraction)
- Audio Transcription (Whisper)
"""

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ailinux.distributed_compute")


class TaskStatus(Enum):
    """Status eines verteilten Tasks"""
    PENDING = "pending"           # Wartet auf Zuweisung
    ASSIGNED = "assigned"         # An Client zugewiesen
    PROCESSING = "processing"     # Client arbeitet daran
    COMPLETED = "completed"       # Erfolgreich abgeschlossen
    FAILED = "failed"             # Fehlgeschlagen
    TIMEOUT = "timeout"           # Timeout
    CANCELLED = "cancelled"       # Abgebrochen


class TaskPriority(Enum):
    """Task-Priorität"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class ComputeTask:
    """Ein Task zur Verteilung an Clients"""
    task_id: str
    task_type: str                    # embedding, sentiment, whisper, etc.
    input_data: Any                   # Eingabedaten
    model_id: str                     # Zu verwendendes Model
    priority: TaskPriority = TaskPriority.NORMAL

    # Status
    status: TaskStatus = TaskStatus.PENDING
    assigned_to: Optional[str] = None  # Client Session ID
    assigned_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Ergebnis
    result: Optional[Any] = None
    error: Optional[str] = None

    # Timing
    created_at: float = field(default_factory=time.time)
    timeout_seconds: float = 60.0
    retry_count: int = 0
    max_retries: int = 2

    # Callback
    callback_id: Optional[str] = None
    allow_community: bool = False
    owner_id: Optional[str] = None
    result_verified: bool = False
    result_source_trust: str = "unknown"

    def is_expired(self) -> bool:
        """Prüft ob Task Timeout überschritten hat"""
        # Check timeout for both ASSIGNED and PROCESSING states
        # Tasks can get stuck in PROCESSING if worker dies mid-task
        if self.assigned_at and self.status in (TaskStatus.ASSIGNED, TaskStatus.PROCESSING):
            return time.time() - self.assigned_at > self.timeout_seconds
        return False

    def to_client_payload(self) -> Dict[str, Any]:
        """Payload für Client"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "input_data": self.input_data,
            "model_id": self.model_id,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ConnectedClient:
    """Ein verbundener Compute-Client"""
    session_id: str
    websocket: Any                    # WebSocket connection
    capability: str                   # WEBGPU, WEBGL2, WASM, etc.
    gpu_name: str = ""
    estimated_tflops: float = 0.0

    # Status
    is_available: bool = True
    current_task: Optional[str] = None
    connected_at: float = field(default_factory=time.time)
    last_heartbeat: float = field(default_factory=time.time)

    # Statistiken
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_compute_time: float = 0.0
    credits_earned: float = 0.0

    # Supported Models
    supported_models: List[str] = field(default_factory=list)
    user_id: str = ""
    tier: str = "guest"
    trust_level: str = "community"
    credits_pending: float = 0.0
    metadata_audit: Dict[str, Any] = field(default_factory=dict)

    def can_handle(self, task: ComputeTask) -> bool:
        """Prüft ob Client diesen Task handlen kann"""
        if not self.is_available:
            return False
        if self.trust_level == "community" and not task.allow_community:
            return False
        if task.model_id not in self.supported_models:
            return False
        return True

    def get_priority_score(self) -> float:
        """Scoring für Task-Zuweisung (höher = besser)"""
        score = 0.0
        # TFLOPs als Basis
        score += self.estimated_tflops * 10
        # Erfolgsrate
        total = self.tasks_completed + self.tasks_failed
        if total > 0:
            score += (self.tasks_completed / total) * 20
        # Bevorzuge aktive Clients
        idle_time = time.time() - self.last_heartbeat
        if idle_time < 30:
            score += 10
        return score


class DistributedComputeManager:
    """
    Verwaltet die Verteilung von Tasks an Clients.

    Features:
    - Task Queue mit Prioritäten
    - Client Pool Management
    - Automatische Task-Zuweisung
    - Timeout Handling
    - Credit/Incentive System
    """

    # Credit-Belohnungen pro Task-Typ (Compute-Einheiten)
    TASK_CREDITS = {
        "embedding": 1.0,
        "embedding_batch": 5.0,
        "sentiment": 0.5,
        "classification": 0.5,
        "summarization": 3.0,
        "whisper_tiny": 10.0,
        "whisper_small": 20.0,
        "clip_embed": 2.0,
        "image_classification": 2.0,
    }

    def __init__(self):
        self._task_queue: Dict[str, ComputeTask] = {}  # task_id -> Task
        self._clients: Dict[str, ConnectedClient] = {}  # session_id -> Client
        self._callbacks: Dict[str, Callable] = {}       # callback_id -> callback

        # Statistiken
        self._stats = {
            "total_tasks_created": 0,
            "total_tasks_completed": 0,
            "total_tasks_failed": 0,
            "total_compute_time": 0.0,
            "total_credits_distributed": 0.0,
        }

        # Background Task für Queue-Processing
        self._running = False
        self._process_task: Optional[asyncio.Task] = None

    async def start(self):
        """Startet den Queue-Processor"""
        if self._running:
            return
        self._running = True
        self._process_task = asyncio.create_task(self._process_queue())
        logger.info("Distributed Compute Manager started")

    async def stop(self):
        """Stoppt den Queue-Processor"""
        self._running = False
        if self._process_task:
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
        logger.info("Distributed Compute Manager stopped")

    # === Client Management ===

    async def register_client(
        self,
        websocket: Any,
        session_id: str,
        capability: str,
        gpu_name: str = "",
        estimated_tflops: float = 0.0,
        supported_models: List[str] = None,
        user_id: str = "",
        tier: str = "guest",
        trust_level: str = "community",
        metadata_audit: Optional[Dict[str, Any]] = None,
    ) -> ConnectedClient:
        """Registriert einen neuen Compute-Client"""
        client = ConnectedClient(
            session_id=session_id,
            websocket=websocket,
            capability=capability,
            gpu_name=gpu_name,
            estimated_tflops=estimated_tflops,
            supported_models=supported_models or [],
            user_id=user_id,
            tier=tier,
            trust_level=trust_level,
            metadata_audit=metadata_audit or {},
        )
        self._clients[session_id] = client

        logger.info(f"Client registered: {session_id} ({capability}, {gpu_name}, {estimated_tflops} TFLOPS)")

        # Sofort verfügbare Tasks zuweisen
        await self._try_assign_tasks()

        return client

    async def unregister_client(self, session_id: str):
        """Entfernt einen Client"""
        if session_id not in self._clients:
            return

        client = self._clients[session_id]

        # Aktiven Task zurück in Queue
        if client.current_task:
            task = self._task_queue.get(client.current_task)
            if task and task.status == TaskStatus.ASSIGNED:
                task.status = TaskStatus.PENDING
                task.assigned_to = None
                task.assigned_at = None
                task.retry_count += 1

        del self._clients[session_id]
        logger.info(f"Client unregistered: {session_id}")

    async def client_heartbeat(self, session_id: str):
        """Heartbeat von Client"""
        if session_id in self._clients:
            self._clients[session_id].last_heartbeat = time.time()

    # === Task Management ===

    async def submit_task(
        self,
        task_type: str,
        input_data: Any,
        model_id: str,
        priority: TaskPriority = TaskPriority.NORMAL,
        timeout_seconds: float = 60.0,
        callback: Optional[Callable] = None,
        allow_community: bool = False,
        owner_id: Optional[str] = None,
    ) -> str:
        """Reicht einen neuen Task zur Verteilung ein"""
        task_id = secrets.token_urlsafe(16)
        callback_id = None

        if callback:
            callback_id = secrets.token_urlsafe(8)
            self._callbacks[callback_id] = callback

        task = ComputeTask(
            task_id=task_id,
            task_type=task_type,
            input_data=input_data,
            model_id=model_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            callback_id=callback_id,
            allow_community=allow_community,
            owner_id=owner_id,
        )

        self._task_queue[task_id] = task
        self._stats["total_tasks_created"] += 1

        logger.info(f"Task submitted: {task_id} ({task_type}, {model_id})")

        # Sofort zuweisen versuchen
        await self._try_assign_tasks()

        return task_id

    async def submit_batch_task(
        self,
        task_type: str,
        input_items: List[Any],
        model_id: str,
        batch_size: int = 10,
        priority: TaskPriority = TaskPriority.NORMAL,
        allow_community: bool = False,
        owner_id: Optional[str] = None,
    ) -> List[str]:
        """Reicht mehrere Items als Batch-Tasks ein"""
        task_ids = []

        # In Batches aufteilen
        for i in range(0, len(input_items), batch_size):
            batch = input_items[i:i + batch_size]
            task_id = await self.submit_task(
                task_type=f"{task_type}_batch",
                input_data=batch,
                model_id=model_id,
                priority=priority,
                allow_community=allow_community,
                owner_id=owner_id,
            )
            task_ids.append(task_id)

        return task_ids

    async def get_task_status(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Gibt Status eines Tasks zurück"""
        task = self._task_queue.get(task_id)
        if not task:
            return None

        return {
            "task_id": task.task_id,
            "status": task.status.value,
            "assigned_to": task.assigned_to,
            "result": task.result,
            "error": task.error,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "allow_community": task.allow_community,
            "result_verified": task.result_verified,
            "result_source_trust": task.result_source_trust,
        }

    async def get_task_result(self, task_id: str, wait: bool = True, timeout: float = 30.0) -> Optional[Any]:
        """Wartet auf und gibt Task-Ergebnis zurück"""
        start = time.time()

        while True:
            task = self._task_queue.get(task_id)
            if not task:
                return None

            if task.status == TaskStatus.COMPLETED:
                return task.result

            if task.status in (TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED):
                raise Exception(f"Task failed: {task.error}")

            if not wait:
                return None

            if time.time() - start > timeout:
                raise TimeoutError(f"Task {task_id} timed out waiting for result")

            await asyncio.sleep(0.1)

    async def cancel_task(self, task_id: str) -> bool:
        """Bricht einen Task ab"""
        task = self._task_queue.get(task_id)
        if not task:
            return False

        if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
            return False

        task.status = TaskStatus.CANCELLED

        # Client benachrichtigen
        if task.assigned_to and task.assigned_to in self._clients:
            client = self._clients[task.assigned_to]
            await self._send_to_client(client, {
                "type": "cancel_task",
                "task_id": task_id,
            })
            client.is_available = True
            client.current_task = None

        return True

    # === Task Processing ===

    async def _process_queue(self):
        """Background-Task für Queue-Verarbeitung"""
        while self._running:
            try:
                # Expired Tasks behandeln
                await self._handle_expired_tasks()

                # Tasks zuweisen
                await self._try_assign_tasks()

                # Stale Clients entfernen
                await self._cleanup_stale_clients()

            except Exception as e:
                logger.error(f"Queue processing error: {e}")

            await asyncio.sleep(1.0)

    async def _try_assign_tasks(self):
        """Versucht wartende Tasks an verfügbare Clients zuzuweisen"""
        # Pending Tasks nach Priorität sortieren
        pending_tasks = [
            t for t in self._task_queue.values()
            if t.status == TaskStatus.PENDING
        ]
        pending_tasks.sort(key=lambda t: (-t.priority.value, t.created_at))

        # Verfügbare Clients nach Score sortieren
        available_clients = [
            c for c in self._clients.values()
            if c.is_available
        ]
        available_clients.sort(key=lambda c: -c.get_priority_score())

        for task in pending_tasks:
            for client in available_clients:
                if client.can_handle(task):
                    await self._assign_task(task, client)
                    available_clients.remove(client)
                    break

    async def _assign_task(self, task: ComputeTask, client: ConnectedClient):
        """Weist Task einem Client zu"""
        task.status = TaskStatus.ASSIGNED
        task.assigned_to = client.session_id
        task.assigned_at = time.time()

        client.is_available = False
        client.current_task = task.task_id

        # Task an Client senden
        await self._send_to_client(client, {
            "type": "task_assignment",
            "task": task.to_client_payload(),
        })

        logger.info(f"Task {task.task_id} assigned to {client.session_id}")

    async def _handle_expired_tasks(self):
        """Behandelt Tasks mit Timeout"""
        now = time.time()

        for task in list(self._task_queue.values()):
            if task.is_expired():
                logger.warning(f"Task {task.task_id} expired")

                # Client freigeben
                if task.assigned_to and task.assigned_to in self._clients:
                    client = self._clients[task.assigned_to]
                    client.is_available = True
                    client.current_task = None
                    client.tasks_failed += 1

                # Retry oder Fail
                if task.retry_count < task.max_retries:
                    task.status = TaskStatus.PENDING
                    task.assigned_to = None
                    task.assigned_at = None
                    task.retry_count += 1
                else:
                    task.status = TaskStatus.TIMEOUT
                    task.error = "Max retries exceeded"
                    self._stats["total_tasks_failed"] += 1

    async def _cleanup_stale_clients(self):
        """Entfernt inaktive Clients"""
        now = time.time()
        stale_timeout = 120  # 2 Minuten

        for session_id in list(self._clients.keys()):
            client = self._clients[session_id]
            if now - client.last_heartbeat > stale_timeout:
                logger.info(f"Removing stale client: {session_id}")
                await self.unregister_client(session_id)

    # === Result Handling ===

    async def report_task_result(
        self,
        session_id: str,
        task_id: str,
        success: bool,
        result: Any = None,
        error: str = None,
        compute_time: float = 0.0,
    ):
        """Client meldet Task-Ergebnis"""
        task = self._task_queue.get(task_id)
        client = self._clients.get(session_id)

        if not task or not client:
            logger.warning(f"Result for unknown task/client: {task_id}/{session_id}")
            return

        if task.assigned_to != session_id:
            logger.warning(f"Result from wrong client: {task_id}")
            return

        # Task aktualisieren
        task.completed_at = time.time()

        if success:
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.result_source_trust = client.trust_level
            task.result_verified = client.trust_level != "community"
            self._stats["total_tasks_completed"] += 1

            # Community results are untrusted until a separate verification step.
            # Keep credits pending so a malicious worker cannot earn final credit
            # merely by returning arbitrary output.
            credits = self.TASK_CREDITS.get(task.task_type, 1.0)
            if client.trust_level == "community":
                client.credits_pending += credits
            else:
                client.credits_earned += credits
                self._stats["total_credits_distributed"] += credits

            client.tasks_completed += 1
        else:
            task.status = TaskStatus.FAILED
            task.error = error
            self._stats["total_tasks_failed"] += 1
            client.tasks_failed += 1

        # Statistiken
        client.total_compute_time += compute_time
        self._stats["total_compute_time"] += compute_time

        # Client freigeben
        client.is_available = True
        client.current_task = None

        # Callback ausführen
        if task.callback_id and task.callback_id in self._callbacks:
            try:
                callback = self._callbacks[task.callback_id]
                await callback(task)
            except Exception as e:
                logger.error(f"Callback error: {e}")
            finally:
                del self._callbacks[task.callback_id]

        logger.info(f"Task {task_id} {'completed' if success else 'failed'}")

        # Nächsten Task zuweisen
        await self._try_assign_tasks()

    # === WebSocket Communication ===

    async def _send_to_client(self, client: ConnectedClient, message: Dict[str, Any]):
        """Sendet Nachricht an Client"""
        try:
            await client.websocket.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send to client {client.session_id}: {e}")
            await self.unregister_client(client.session_id)

    async def broadcast_to_clients(self, message: Dict[str, Any], filter_capability: str = None):
        """Broadcast an alle/gefilterte Clients"""
        for client in self._clients.values():
            if filter_capability and client.capability != filter_capability:
                continue
            await self._send_to_client(client, message)

    # === Statistics ===

    def get_stats(self) -> Dict[str, Any]:
        """Gibt Statistiken zurück"""
        pending = sum(1 for t in self._task_queue.values() if t.status == TaskStatus.PENDING)
        processing = sum(1 for t in self._task_queue.values() if t.status in (TaskStatus.ASSIGNED, TaskStatus.PROCESSING))

        return {
            "queue": {
                "pending": pending,
                "processing": processing,
                "total_in_queue": len(self._task_queue),
            },
            "clients": {
                "connected": len(self._clients),
                "available": sum(1 for c in self._clients.values() if c.is_available),
                "total_tflops": sum(c.estimated_tflops for c in self._clients.values()),
            },
            "totals": self._stats,
            "top_contributors": self._get_top_contributors(5),
        }

    def _get_top_contributors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Top Clients nach Credits"""
        clients = sorted(
            self._clients.values(),
            key=lambda c: c.credits_earned,
            reverse=True
        )[:limit]

        return [
            {
                "session_id": c.session_id[:8] + "...",
                "capability": c.capability,
                "credits": round(c.credits_earned, 2),
                "credits_pending": round(c.credits_pending, 2),
                "tasks_completed": c.tasks_completed,
            }
            for c in clients
        ]

    def get_client_credits(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Gibt Credits eines Clients zurück"""
        client = self._clients.get(session_id)
        if not client:
            return None

        return {
            "credits_earned": round(client.credits_earned, 2),
            "credits_pending": round(client.credits_pending, 2),
            "tasks_completed": client.tasks_completed,
            "tasks_failed": client.tasks_failed,
            "total_compute_time": round(client.total_compute_time, 2),
            "connected_since": client.connected_at,
        }


# === Singleton ===
distributed_compute = DistributedComputeManager()


def get_distributed_compute() -> DistributedComputeManager:
    return distributed_compute
