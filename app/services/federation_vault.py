"""
Federation Vault - Secure Node Authentication
Enables dynamic node registration with vault-stored tokens

Stand: 2026-01-01
"""

import os
import json
import hmac
import hashlib
import secrets
from pathlib import Path

from app.paths import CONFIG_DIR, VAULT_DIR
from typing import Dict, Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime
import logging
import fcntl

logger = logging.getLogger(__name__)

VAULT_PATH = VAULT_DIR
FEDERATION_VAULT_FILE = VAULT_PATH / "federation_nodes.enc"
FEDERATION_TOKENS_FILE = VAULT_PATH / "federation_tokens.json"
FEDERATION_LOCK_FILE = VAULT_PATH / ".federation_tokens.lock"


@dataclass
class FederationNode:
    """A registered federation node"""
    node_id: str
    token_hash: str  # SHA256 hash of the auth token
    role: str  # "hub" or "node"
    allowed_ips: List[str]  # Empty = any IP allowed
    created_at: str
    last_seen: Optional[str] = None
    active: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "FederationNode":
        return cls(**data)


class FederationVault:
    """
    Secure storage for federation node authentication
    
    Auth Flow:
    1. Node sends: HELLO + node_id + token
    2. Vault verifies: token matches stored hash
    3. All messages: signed with shared FEDERATION_SECRET
    """
    
    def __init__(self):
        self.nodes: Dict[str, FederationNode] = {}
        self._shared_secret: Optional[str] = None
        
        VAULT_PATH.mkdir(parents=True, exist_ok=True)
        VAULT_PATH.chmod(0o700)
        
        self._load()
    
    def _load(self):
        """Load registered nodes from file"""
        if FEDERATION_TOKENS_FILE.exists():
            try:
                with open(FEDERATION_TOKENS_FILE, 'r') as f:
                    data = json.load(f)
                    for node_data in data.get("nodes", []):
                        node = FederationNode.from_dict(node_data)
                        self.nodes[node.node_id] = node
                logger.info(f"Loaded {len(self.nodes)} federation nodes from vault")
            except Exception as e:
                logger.error(f"Failed to load federation vault: {e}")
        
        # Load shared secret from env or file
        self._shared_secret = os.getenv("FEDERATION_SECRET")
        if not self._shared_secret:
            secret_file = CONFIG_DIR / "federation_psk.key"
            if secret_file.exists():
                self._shared_secret = secret_file.read_text().strip()
    
    def _save(self, only_node: Optional[str] = None):
        """Merge and atomically persist node state under an inter-process lock."""
        import tempfile

        try:
            # The lock covers the complete read -> merge -> replace sequence.
            # Atomic replace alone protects readers from partial files but does
            # not prevent two writers from overwriting each other.
            with open(FEDERATION_LOCK_FILE, "a+") as lock_file:
                os.chmod(FEDERATION_LOCK_FILE, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

                on_disk: Dict[str, dict] = {}
                if FEDERATION_TOKENS_FILE.exists():
                    with open(FEDERATION_TOKENS_FILE, "r") as f:
                        for nd in json.load(f).get("nodes", []):
                            on_disk[nd["node_id"]] = nd

                mine = self.nodes if only_node is None else {
                    k: v for k, v in self.nodes.items() if k == only_node
                }
                for node_id, node in mine.items():
                    on_disk[node_id] = node.to_dict()

                data = {
                    "nodes": list(on_disk.values()),
                    "updated_at": datetime.utcnow().isoformat(),
                }

                fd, tmp = tempfile.mkstemp(
                    dir=str(VAULT_PATH), prefix=".tokens-", suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, "w") as f:
                        json.dump(data, f, indent=2)
                        f.flush()
                        os.fsync(f.fileno())
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, FEDERATION_TOKENS_FILE)
                except Exception:
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                    raise

                # Align memory with the merged on-disk state so a later full
                # save cannot resurrect stale values from another process.
                for node_id, nd in on_disk.items():
                    self.nodes[node_id] = FederationNode.from_dict(nd)
        except Exception as e:
            logger.error(f"Failed to save federation vault: {e}")

    @property
    def shared_secret(self) -> str:
        """Get shared signing secret"""
        if not self._shared_secret:
            raise ValueError("FEDERATION_SECRET not configured")
        return self._shared_secret
    
    def register_node(self, node_id: str, role: str = "node", 
                      allowed_ips: List[str] = None) -> str:
        """
        Register a new node and return its auth token
        
        Returns: The plain-text token (only shown once!)
        """
        # Generate secure token
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        node = FederationNode(
            node_id=node_id,
            token_hash=token_hash,
            role=role,
            allowed_ips=allowed_ips or [],
            created_at=datetime.utcnow().isoformat(),
            active=True
        )
        
        self.nodes[node_id] = node
        self._save(only_node=node_id)
        
        logger.info(f"Registered federation node: {node_id} ({role})")
        return token
    
    def verify_token(self, node_id: str, token: str, 
                     client_ip: Optional[str] = None) -> bool:
        """Verify a node's auth token"""
        node = self.nodes.get(node_id)
        if not node:
            logger.warning(f"Unknown node attempted auth: {node_id}")
            return False
        
        if not node.active:
            logger.warning(f"Inactive node attempted auth: {node_id}")
            return False
        
        # Check IP restriction
        if node.allowed_ips and client_ip:
            if client_ip not in node.allowed_ips:
                logger.warning(f"Node {node_id} auth from unauthorized IP: {client_ip}")
                return False
        
        # Verify token
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if not hmac.compare_digest(token_hash, node.token_hash):
            logger.warning(f"Invalid token for node: {node_id}")
            return False
        
        # Update last seen
        node.last_seen = datetime.utcnow().isoformat()
        self._save(only_node=node_id)
        
        return True
    
    def revoke_node(self, node_id: str) -> bool:
        """Revoke a node's access"""
        if node_id in self.nodes:
            self.nodes[node_id].active = False
            self._save(only_node=node_id)
            logger.info(f"Revoked federation node: {node_id}")
            return True
        return False
    
    def rotate_token(self, node_id: str) -> Optional[str]:
        """Generate new token for existing node"""
        if node_id not in self.nodes:
            return None
        
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        self.nodes[node_id].token_hash = token_hash
        self._save(only_node=node_id)
        
        logger.info(f"Rotated token for node: {node_id}")
        return token
    
    def list_nodes(self) -> List[dict]:
        """List all registered nodes (without secrets)"""
        return [
            {
                "node_id": n.node_id,
                "role": n.role,
                "active": n.active,
                "last_seen": n.last_seen,
                "allowed_ips": n.allowed_ips
            }
            for n in self.nodes.values()
        ]
    
    def get_node(self, node_id: str) -> Optional[FederationNode]:
        """Get node info"""
        return self.nodes.get(node_id)


# Singleton instance
_vault: Optional[FederationVault] = None

def get_federation_vault() -> FederationVault:
    global _vault
    if _vault is None:
        _vault = FederationVault()
    return _vault


# CLI for management
if __name__ == "__main__":
    import sys
    vault = get_federation_vault()
    
    if len(sys.argv) < 2:
        print("Usage: python federation_vault.py <command> [args]")
        print("Commands: list, register <node_id> [role], revoke <node_id>, rotate <node_id>")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "list":
        for node in vault.list_nodes():
            status = "✓" if node["active"] else "✗"
            print(f"  {status} {node['node_id']} ({node['role']}) - last: {node['last_seen'] or 'never'}")
    
    elif cmd == "register":
        if len(sys.argv) < 3:
            print("Usage: register <node_id> [role]")
            sys.exit(1)
        node_id = sys.argv[2]
        role = sys.argv[3] if len(sys.argv) > 3 else "node"
        token = vault.register_node(node_id, role)
        print(f"Registered: {node_id}")
        print(f"Token (save this!): {token}")
        print(f"Add to node's env: FEDERATION_TOKEN={token}")
    
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("Usage: revoke <node_id>")
            sys.exit(1)
        if vault.revoke_node(sys.argv[2]):
            print(f"Revoked: {sys.argv[2]}")
        else:
            print(f"Not found: {sys.argv[2]}")
    
    elif cmd == "rotate":
        if len(sys.argv) < 3:
            print("Usage: rotate <node_id>")
            sys.exit(1)
        token = vault.rotate_token(sys.argv[2])
        if token:
            print(f"New token: {token}")
        else:
            print(f"Not found: {sys.argv[2]}")
