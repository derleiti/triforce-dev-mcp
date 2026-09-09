# app/services/api_vault.py
"""
API Key Vault - Verschlüsselte Speicherung von API Keys
Keys werden nur temporär für Task-Dauer entschlüsselt

Implementierung für TriForce Backend
Stand: 2025-12-13
"""

import os
import json
import base64
from pathlib import Path

from app.paths import VAULT_DIR
from typing import Dict, Optional, List
from dataclasses import dataclass
from datetime import datetime
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import secrets
import logging

logger = logging.getLogger(__name__)

VAULT_PATH = VAULT_DIR
VAULT_FILE = VAULT_PATH / "api_keys.enc"
SALT_FILE = VAULT_PATH / "salt"


@dataclass
class APIKeyEntry:
    """Ein API Key Eintrag"""
    provider: str           # openai, anthropic, google, mistral
    key_id: str            # Identifier (z.B. "main", "backup")
    encrypted_key: bytes   # Verschlüsselter Key
    created_at: str
    last_used: Optional[str] = None
    usage_count: int = 0
    
    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "key_id": self.key_id,
            "encrypted_key": base64.b64encode(self.encrypted_key).decode(),
            "created_at": self.created_at,
            "last_used": self.last_used,
            "usage_count": self.usage_count
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "APIKeyEntry":
        return cls(
            provider=data["provider"],
            key_id=data["key_id"],
            encrypted_key=base64.b64decode(data["encrypted_key"]),
            created_at=data["created_at"],
            last_used=data.get("last_used"),
            usage_count=data.get("usage_count", 0)
        )


class APIVault:
    """
    Verschlüsselter Tresor für API Keys
    
    Master Password → PBKDF2 → Fernet Key → Verschlüsselung
    """
    
    def __init__(self):
        self.keys: Dict[str, APIKeyEntry] = {}
        self._fernet: Optional[Fernet] = None
        self._unlocked = False
        
        # Vault-Verzeichnis erstellen
        VAULT_PATH.mkdir(parents=True, exist_ok=True)
        VAULT_PATH.chmod(0o700)  # Nur Owner
    
    @property
    def is_unlocked(self) -> bool:
        return self._unlocked
    
    @property
    def is_initialized(self) -> bool:
        return VAULT_FILE.exists()
    
    def _derive_key(self, master_password: str, salt: bytes) -> bytes:
        """Leitet Encryption Key aus Master Password ab"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # Hohe Iteration für Sicherheit
        )
        return base64.urlsafe_b64encode(kdf.derive(master_password.encode()))
    
    def initialize(self, master_password: str) -> bool:
        """Vault initialisieren (erstmalig)"""
        if VAULT_FILE.exists():
            logger.warning("Vault already exists, use unlock() instead")
            return False
        
        # Salt generieren
        salt = secrets.token_bytes(32)
        SALT_FILE.write_bytes(salt)
        SALT_FILE.chmod(0o600)
        
        # Fernet Key ableiten
        key = self._derive_key(master_password, salt)
        self._fernet = Fernet(key)
        self._unlocked = True
        
        # Leeren Vault speichern
        self._save()
        
        logger.info("Vault initialized")
        return True
    
    def unlock(self, master_password: str) -> bool:
        """Vault entsperren"""
        if not VAULT_FILE.exists():
            logger.error("Vault not initialized")
            return False
        
        try:
            salt = SALT_FILE.read_bytes()
            key = self._derive_key(master_password, salt)
            self._fernet = Fernet(key)
            
            # Vault laden und entschlüsseln
            encrypted_data = VAULT_FILE.read_bytes()
            decrypted = self._fernet.decrypt(encrypted_data)
            vault_data = json.loads(decrypted.decode())
            
            self.keys = {
                k: APIKeyEntry.from_dict(v) 
                for k, v in vault_data.get("keys", {}).items()
            }
            
            self._unlocked = True
            logger.info(f"Vault unlocked, {len(self.keys)} keys loaded")
            return True
            
        except Exception as e:
            logger.error(f"Failed to unlock vault: {e}")
            self._fernet = None
            self._unlocked = False
            return False
    
    def lock(self):
        """Vault sperren - Keys aus RAM löschen"""
        self.keys.clear()
        self._fernet = None
        self._unlocked = False
        logger.info("Vault locked")
    
    def _save(self):
        """Vault verschlüsselt speichern"""
        if not self._unlocked:
            raise RuntimeError("Vault is locked")
        
        vault_data = {
            "keys": {k: v.to_dict() for k, v in self.keys.items()},
            "updated_at": datetime.now().isoformat()
        }
        
        encrypted = self._fernet.encrypt(json.dumps(vault_data).encode())
        VAULT_FILE.write_bytes(encrypted)
        VAULT_FILE.chmod(0o600)
    
    # =========================================================================
    # Key Management
    # =========================================================================
    
    def add_key(self, provider: str, api_key: str, key_id: str = "main") -> str:
        """API Key hinzufügen"""
        if not self._unlocked:
            raise RuntimeError("Vault is locked")
        
        # Key verschlüsseln
        encrypted = self._fernet.encrypt(api_key.encode())
        
        entry_id = f"{provider}:{key_id}"
        self.keys[entry_id] = APIKeyEntry(
            provider=provider,
            key_id=key_id,
            encrypted_key=encrypted,
            created_at=datetime.now().isoformat()
        )
        
        self._save()
        logger.info(f"Added API key: {entry_id}")
        return entry_id
    
    def get_key(self, provider: str, key_id: str = "main") -> Optional[str]:
        """API Key entschlüsseln und zurückgeben"""
        if not self._unlocked:
            raise RuntimeError("Vault is locked")
        
        entry_id = f"{provider}:{key_id}"
        entry = self.keys.get(entry_id)
        
        if not entry:
            return None
        
        # Entschlüsseln
        decrypted = self._fernet.decrypt(entry.encrypted_key)
        
        # Usage tracken
        entry.last_used = datetime.now().isoformat()
        entry.usage_count += 1
        self._save()
        
        return decrypted.decode()
    
    def remove_key(self, provider: str, key_id: str = "main") -> bool:
        """API Key entfernen"""
        if not self._unlocked:
            raise RuntimeError("Vault is locked")
        
        entry_id = f"{provider}:{key_id}"
        if entry_id in self.keys:
            del self.keys[entry_id]
            self._save()
            logger.info(f"Removed API key: {entry_id}")
            return True
        return False
    
    def list_keys(self) -> List[dict]:
        """Liste aller Keys (ohne die eigentlichen Keys!)"""
        return [
            {
                "provider": e.provider,
                "key_id": e.key_id,
                "created_at": e.created_at,
                "last_used": e.last_used,
                "usage_count": e.usage_count
            }
            for e in self.keys.values()
        ]
    
    def has_key(self, provider: str, key_id: str = "main") -> bool:
        """Prüft ob Key existiert"""
        return f"{provider}:{key_id}" in self.keys
    
    # =========================================================================
    # Temporäre Keys für Tasks
    # =========================================================================
    
    def get_temp_env(self, providers: List[str]) -> Dict[str, str]:
        """
        Gibt temporäre Environment-Variablen für Agent zurück
        Keys sind nur im RAM, nicht auf Disk!
        """
        if not self._unlocked:
            raise RuntimeError("Vault is locked")
        
        env = {}
        env_var_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google": "GOOGLE_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "groq": "GROQ_API_KEY",
            "cerebras": "CEREBRAS_API_KEY",
        }
        
        for provider in providers:
            key = self.get_key(provider)
            if key:
                env_var = env_var_map.get(provider, f"{provider.upper()}_API_KEY")
                env[env_var] = key
        
        return env
    
    def get_status(self) -> dict:
        """Vault-Status für MCP"""
        return {
            "initialized": self.is_initialized,
            "unlocked": self.is_unlocked,
            "key_count": len(self.keys) if self._unlocked else 0,
            "providers": list(set(e.provider for e in self.keys.values())) if self._unlocked else []
        }


# Singleton
api_vault = APIVault()


# =============================================================================
# MCP Tool Handlers
# =============================================================================

async def handle_vault_init(params: dict) -> dict:
    """Vault initialisieren"""
    master_password = params.get("master_password")
    if not master_password:
        return {"error": "master_password required"}
    
    if api_vault.is_initialized:
        return {"error": "Vault already initialized, use vault_unlock"}
    
    success = api_vault.initialize(master_password)
    return {"success": success, "message": "Vault initialized" if success else "Failed"}


async def handle_vault_unlock(params: dict) -> dict:
    """Vault entsperren"""
    master_password = params.get("master_password")
    if not master_password:
        return {"error": "master_password required"}
    
    success = api_vault.unlock(master_password)
    return {
        "success": success,
        "key_count": len(api_vault.keys) if success else 0,
        "message": f"Unlocked with {len(api_vault.keys)} keys" if success else "Wrong password or corrupted vault"
    }


async def handle_vault_lock(params: dict) -> dict:
    """Vault sperren"""
    api_vault.lock()
    return {"success": True, "message": "Vault locked"}


async def handle_vault_add_key(params: dict) -> dict:
    """API Key hinzufügen"""
    provider = params.get("provider")
    api_key = params.get("api_key")
    key_id = params.get("key_id", "main")
    
    if not provider or not api_key:
        return {"error": "provider and api_key required"}
    
    try:
        entry_id = api_vault.add_key(provider, api_key, key_id)
        return {"success": True, "entry_id": entry_id}
    except RuntimeError as e:
        return {"error": str(e)}


async def handle_vault_list_keys(params: dict) -> dict:
    """Keys auflisten (ohne Werte!)"""
    if not api_vault.is_unlocked:
        return {"error": "Vault is locked"}
    
    return {"keys": api_vault.list_keys()}


async def handle_vault_remove_key(params: dict) -> dict:
    """Key entfernen"""
    provider = params.get("provider")
    key_id = params.get("key_id", "main")
    
    if not provider:
        return {"error": "provider required"}
    
    try:
        success = api_vault.remove_key(provider, key_id)
        return {"success": success, "removed": f"{provider}:{key_id}" if success else None}
    except RuntimeError as e:
        return {"error": str(e)}


async def handle_vault_status(params: dict) -> dict:
    """Vault-Status"""
    return api_vault.get_status()


# Tool-Definitionen für MCP
VAULT_TOOLS = [
    {
        "name": "vault_init",
        "description": "API Key Vault initialisieren (einmalig, nur Admin)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "master_password": {"type": "string", "description": "Master-Passwort für Vault"}
            },
            "required": ["master_password"]
        }
    },
    {
        "name": "vault_unlock",
        "description": "Vault entsperren (nach Server-Neustart nötig)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "master_password": {"type": "string", "description": "Master-Passwort"}
            },
            "required": ["master_password"]
        }
    },
    {
        "name": "vault_lock",
        "description": "Vault sperren (Keys aus RAM löschen)",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "vault_add_key",
        "description": "API Key zum Vault hinzufügen (verschlüsselt)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["openai", "anthropic", "google", "gemini", "mistral", "groq", "cerebras"],
                    "description": "API Provider"
                },
                "api_key": {"type": "string", "description": "Der API Key"},
                "key_id": {"type": "string", "default": "main", "description": "Key-Identifier (für mehrere Keys pro Provider)"}
            },
            "required": ["provider", "api_key"]
        }
    },
    {
        "name": "vault_list_keys",
        "description": "Gespeicherte API Keys auflisten (ohne die Keys selbst!)",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "vault_remove_key",
        "description": "API Key aus Vault entfernen",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {"type": "string", "description": "Provider Name"},
                "key_id": {"type": "string", "default": "main"}
            },
            "required": ["provider"]
        }
    },
    {
        "name": "vault_status",
        "description": "Vault-Status prüfen (locked/unlocked, Key-Anzahl)",
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    }
]

VAULT_HANDLERS = {
    "vault_init": handle_vault_init,
    "vault_unlock": handle_vault_unlock,
    "vault_lock": handle_vault_lock,
    "vault_add_key": handle_vault_add_key,
    "vault_list_keys": handle_vault_list_keys,
    "vault_remove_key": handle_vault_remove_key,
    "vault_status": handle_vault_status,
}
