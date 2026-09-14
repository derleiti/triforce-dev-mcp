#!/usr/bin/env python3
"""

AILinux Mesh Guardian v2.0
==========================

Selbstheilendes System für Mesh-Server-Cluster.
Läuft auf allen Servern und synchronisiert sich via Git.

Features:
- Health Monitoring aller Hubs
- Auto-Restart bei Crashes  
- Git Sync zwischen Servern (bidirektional)
- Update Propagation mit Auto-Restart
- Failover Management
- Erweiterbar auf beliebig viele Nodes
"""
import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import socket

# =============================================================================
# Auto-detect paths
# =============================================================================

def find_triforce_dir() -> Path:
    """Find triforce directory automatically"""
    candidates = [
        Path.home() / "triforce",
        Path("/home/zombie/workspace/triforce"),
        Path("/home/zombie/workspace/triforce"),
        Path.cwd(),
    ]
    for p in candidates:
        if (p / "app").exists() or (p / "scripts").exists():
            return p
    return Path.home() / "triforce"

TRIFORCE_DIR = find_triforce_dir()
LOG_FILE = TRIFORCE_DIR / "logs" / "mesh-guardian.log"

# Ensure log directory exists
(TRIFORCE_DIR / "logs").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_FILE), mode="a")
    ]
)
logger = logging.getLogger("guardian")

# =============================================================================
# Configuration
# =============================================================================

@dataclass
class HubConfig:
    hub_id: str
    host: str
    port: int = 44433
    ssh_host: str = ""
    is_local: bool = False
    use_ssl: bool = False
    ws_path: str = ""  # Empty for Hetzner (root), "/mcp" for standalone

@dataclass 
class GuardianConfig:
    node_id: str = field(default_factory=socket.gethostname)
    triforce_dir: Path = field(default_factory=lambda: TRIFORCE_DIR)
    check_interval: int = 30
    git_remote: str = "origin"
    git_branch: str = "master"
    max_failures_before_restart: int = 3
    hubs: List[HubConfig] = field(default_factory=list)
    
    def __post_init__(self):
        self._detect_hubs()
    
    def _detect_hubs(self):
        """Auto-detect local and remote hubs"""
        local_ip = self._get_local_ip()
        hostname = socket.gethostname()
        
        # Known hubs in the mesh
        all_hubs = [
            HubConfig("hetzner", "10.10.0.1", 44433, "10.10.0.1", use_ssl=True, ws_path=""),
            HubConfig("backup", "10.10.0.3", 44433, "backup", use_ssl=False, ws_path="/mcp"),
        ]
        
        for hub in all_hubs:
            # Check if this is the local hub
            if hub.host == local_ip or hub.hub_id in hostname.lower():
                hub.is_local = True
                hub.host = "127.0.0.1"
            self.hubs.append(hub)
        
        logger.info(f"Detected {len(self.hubs)} hubs, local: {[h.hub_id for h in self.hubs if h.is_local]}")
    
    def _get_local_ip(self) -> str:
        """Get local IP address"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.10.0.1", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

# =============================================================================
# Health Checker
# =============================================================================

class HealthChecker:
    def __init__(self, config: GuardianConfig):
        self.config = config
        self.hub_status: Dict[str, dict] = {}
    
    async def check_hub(self, hub: HubConfig) -> dict:
        """Check single hub health via WebSocket"""
        try:
            import websockets
            import ssl
        except ImportError:
            logger.error("websockets not installed!")
            return {"hub_id": hub.hub_id, "healthy": False, "error": "websockets not installed"}
        
        status = {
            "hub_id": hub.hub_id,
            "healthy": False,
            "latency_ms": 0,
            "nodes": 0,
            "tools": 0,
            "error": None,
            "checked_at": datetime.now().isoformat()
        }
        
        try:
            protocol = "wss" if hub.use_ssl else "ws"
            url = f"{protocol}://{hub.host}:{hub.port}{hub.ws_path}"
            
            ssl_ctx = None
            if hub.use_ssl:
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False
                ssl_ctx.verify_mode = ssl.CERT_NONE
            
            start = datetime.now()
            async with websockets.connect(url, ssl=ssl_ctx, open_timeout=10, close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "jsonrpc": "2.0",
                    "method": "mesh/stats",
                    "id": 1
                }))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                
                latency = (datetime.now() - start).total_seconds() * 1000
                result = resp.get("result", {})
                
                status["healthy"] = True
                status["latency_ms"] = round(latency, 2)
                status["nodes"] = result.get("active_nodes", 0)
                status["tools"] = result.get("active_tools", 0)
                status["connections"] = result.get("total_connections", 0)
        
        except Exception as e:
            status["error"] = str(e)[:100]
            if hub.is_local:
                logger.warning(f"Local hub {hub.hub_id} unhealthy: {e}")
        
        self.hub_status[hub.hub_id] = status
        return status
    
    async def check_all(self) -> Dict[str, dict]:
        tasks = [self.check_hub(hub) for hub in self.config.hubs]
        await asyncio.gather(*tasks, return_exceptions=True)
        return self.hub_status

# =============================================================================
# Service Manager  
# =============================================================================

class ServiceManager:
    def __init__(self, config: GuardianConfig):
        self.config = config
    
    def is_hub_running(self) -> bool:
        result = subprocess.run(
            ["pgrep", "-f", "mesh_hub|mcp_ws_server|uvicorn.*app.main"],
            capture_output=True
        )
        return result.returncode == 0
    
    def start_hub(self) -> bool:
        logger.info("Starting hub...")
        
        venv = self.config.triforce_dir / ".venv/bin/python"
        if not venv.exists():
            logger.error(f"Venv not found: {venv}")
            return False
        
        # Check if full backend exists (main server) or standalone hub
        main_py = self.config.triforce_dir / "app/main.py"
        hub_py = self.config.triforce_dir / "app/mcp/mesh_hub.py"
        
        if main_py.exists() and self._has_full_deps():
            return self._start_backend(venv)
        elif hub_py.exists():
            return self._start_standalone_hub(venv, hub_py)
        else:
            logger.error("No hub script found!")
            return False
    
    def _has_full_deps(self) -> bool:
        """Check if full backend dependencies are installed"""
        venv = self.config.triforce_dir / ".venv/bin/python"
        result = subprocess.run(
            [str(venv), "-c", "import redis, uvicorn, fastapi"],
            capture_output=True
        )
        return result.returncode == 0
    
    def _start_backend(self, venv: Path) -> bool:
        log_file = self.config.triforce_dir / "logs/backend.log"
        try:
            with open(log_file, "a") as log:
                subprocess.Popen(
                    [str(venv), "-m", "uvicorn", "app.main:app",
                     "--host", "0.0.0.0", "--port", "9000",
                     "--timeout-keep-alive", "75"],
                    cwd=str(self.config.triforce_dir),
                    stdout=log, stderr=log,
                    start_new_session=True
                )
            logger.info("Full backend started")
            return True
        except Exception as e:
            logger.error(f"Backend start failed: {e}")
            return False
    
    def _start_standalone_hub(self, venv: Path, hub_py: Path) -> bool:
        log_file = self.config.triforce_dir / "logs/mesh-hub.log"
        try:
            with open(log_file, "a") as log:
                subprocess.Popen(
                    [str(venv), str(hub_py), "--port", "44433"],
                    cwd=str(self.config.triforce_dir),
                    stdout=log, stderr=log,
                    start_new_session=True
                )
            logger.info("Standalone hub started")
            return True
        except Exception as e:
            logger.error(f"Hub start failed: {e}")
            return False
    
    def stop_hub(self):
        subprocess.run(["pkill", "-f", "mesh_hub"], capture_output=True)
        subprocess.run(["pkill", "-f", "uvicorn.*app.main"], capture_output=True)
    
    def restart_hub(self) -> bool:
        logger.info("Restarting hub...")
        self.stop_hub()
        import time
        time.sleep(3)
        return self.start_hub()

# =============================================================================
# Git Sync Manager
# =============================================================================

class GitSyncManager:
    def __init__(self, config: GuardianConfig):
        self.config = config
        self._last_check = None
        self._check_interval = 60  # Check git every 60 seconds
    
    def _run_git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + list(args),
            cwd=str(self.config.triforce_dir),
            capture_output=True,
            text=True
        )
    
    def get_current_commit(self) -> str:
        result = self._run_git("rev-parse", "HEAD")
        return result.stdout.strip() if result.returncode == 0 else ""
    
    def fetch_and_check_updates(self) -> bool:
        """Fetch and check if updates available"""
        # Rate limit git checks
        now = datetime.now()
        if self._last_check and (now - self._last_check).seconds < self._check_interval:
            return False
        self._last_check = now
        
        # Fetch
        self._run_git("fetch", self.config.git_remote, self.config.git_branch)
        
        # Compare
        local = self.get_current_commit()
        result = self._run_git("rev-parse", f"{self.config.git_remote}/{self.config.git_branch}")
        remote = result.stdout.strip()
        
        has_updates = local != remote and remote != ""
        if has_updates:
            logger.info(f"Updates available: {local[:8]} → {remote[:8]}")
        return has_updates
    
    def pull_updates(self) -> bool:
        logger.info("Pulling updates from Git...")
        result = self._run_git("pull", "--ff-only", self.config.git_remote, self.config.git_branch)
        
        if result.returncode == 0:
            logger.info(f"Pull successful")
            return True
        else:
            logger.error(f"Pull failed: {result.stderr}")
            # Try reset to remote
            self._run_git("reset", "--hard", f"{self.config.git_remote}/{self.config.git_branch}")
            return True
    
    def push_local_changes(self) -> bool:
        """Push any local changes (for bidirectional sync)"""
        # Check for changes
        status = self._run_git("status", "--porcelain")
        if not status.stdout.strip():
            return True
        
        # Add and commit
        self._run_git("add", "-A")
        self._run_git("commit", "-m", f"Auto-sync from {self.config.node_id} at {datetime.now().isoformat()}")
        
        # Push
        result = self._run_git("push", self.config.git_remote, self.config.git_branch)
        if result.returncode == 0:
            logger.info("Pushed local changes")
            return True
        else:
            logger.warning(f"Push failed: {result.stderr}")
            return False

# =============================================================================
# Main Guardian
# =============================================================================

class MeshGuardian:
    def __init__(self, config: GuardianConfig = None):
        self.config = config or GuardianConfig()
        self.health = HealthChecker(self.config)
        self.service = ServiceManager(self.config)
        self.git = GitSyncManager(self.config)
        self._running = False
        self.failures: Dict[str, int] = {}
    
    async def run(self):
        logger.info(f"═══ Mesh Guardian v2.0 starting on {self.config.node_id} ═══")
        logger.info(f"Triforce dir: {self.config.triforce_dir}")
        logger.info(f"Check interval: {self.config.check_interval}s")
        
        self._running = True
        
        while self._running:
            try:
                await self._check_cycle()
            except Exception as e:
                logger.error(f"Check cycle error: {e}")
            
            await asyncio.sleep(self.config.check_interval)
    
    async def _check_cycle(self):
        # 1. Health check all hubs
        status = await self.health.check_all()
        
        healthy = [h for h, s in status.items() if s.get("healthy")]
        unhealthy = [h for h, s in status.items() if not s.get("healthy")]
        
        logger.info(f"Health: {len(healthy)}/{len(status)} hubs OK | Healthy: {healthy}")
        
        # 2. Handle local hub failures
        for hub in self.config.hubs:
            if not hub.is_local:
                continue
            
            hub_status = status.get(hub.hub_id, {})
            
            if not hub_status.get("healthy"):
                self.failures[hub.hub_id] = self.failures.get(hub.hub_id, 0) + 1
                logger.warning(f"Local hub unhealthy ({self.failures[hub.hub_id]}/{self.config.max_failures_before_restart})")
                
                if self.failures[hub.hub_id] >= self.config.max_failures_before_restart:
                    logger.warning("Max failures reached, restarting...")
                    if self.service.restart_hub():
                        self.failures[hub.hub_id] = 0
                        await asyncio.sleep(5)  # Give it time to start
            else:
                self.failures[hub.hub_id] = 0
        
        # 3. Git sync
        if self.git.fetch_and_check_updates():
            logger.info("Updates available, pulling...")
            if self.git.pull_updates():
                logger.info("Restarting after update...")
                self.service.restart_hub()
                await asyncio.sleep(5)
    
    def stop(self):
        self._running = False

# =============================================================================
# Entry Point
# =============================================================================

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="AILinux Mesh Guardian v2.0")
    parser.add_argument("--interval", type=int, default=30, help="Check interval in seconds")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()
    
    config = GuardianConfig()
    config.check_interval = args.interval
    
    guardian = MeshGuardian(config)
    
    if args.once:
        await guardian._check_cycle()
    else:
        try:
            await guardian.run()
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            guardian.stop()

if __name__ == "__main__":
    asyncio.run(main())
