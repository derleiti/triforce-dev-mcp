# app/services/user_system/user_manager.py
"""
AILinux User Manager - Verwaltet User-Verzeichnisse und Credentials

Features:
- User-Verzeichnisse unter /users/{user_id}/
- Verschlüsselte Credentials pro User
- Settings-Sync zwischen Client und Server
- Device-Management (mehrere Geräte pro User)
- Quota-Management (Free/Pro/Enterprise)

Stand: 2025-12-14
"""

import os
import json
import secrets
import hashlib
import asyncio
from pathlib import Path

from app.paths import USERS_DIR
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict, field
from enum import Enum
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64
import logging

logger = logging.getLogger("ailinux.user_system")

# Basis-Pfad für User-Daten
USERS_BASE = USERS_DIR
USERS_BASE.mkdir(parents=True, exist_ok=True)


class SubscriptionTier(str, Enum):
    FREE = "free"           # 100 Anfragen/Tag, nur Ollama
    PRO = "pro"             # 1000 Anfragen/Tag, Cloud-Models
    ENTERPRISE = "enterprise"  # Unlimited, Priority


@dataclass
class UserQuota:
    """Nutzungs-Limits pro Tier"""
    tier: SubscriptionTier = SubscriptionTier.FREE
    daily_requests: int = 100
    requests_today: int = 0
    last_reset: str = ""
    
    # Feature-Flags
    allow_cloud_models: bool = False
    allow_code_execution: bool = True
    allow_file_sync: bool = False
    max_devices: int = 2
    max_storage_mb: int = 100
    priority_queue: bool = False
    
    @classmethod
    def for_tier(cls, tier: SubscriptionTier) -> "UserQuota":
        """Erstellt Quota basierend auf Tier"""
        configs = {
            SubscriptionTier.FREE: {
                "daily_requests": 100,
                "allow_cloud_models": False,
                "allow_file_sync": False,
                "max_devices": 2,
                "max_storage_mb": 100,
                "priority_queue": False,
            },
            SubscriptionTier.PRO: {
                "daily_requests": 1000,
                "allow_cloud_models": True,
                "allow_file_sync": True,
                "max_devices": 5,
                "max_storage_mb": 1000,
                "priority_queue": False,
            },
            SubscriptionTier.ENTERPRISE: {
                "daily_requests": 999999,
                "allow_cloud_models": True,
                "allow_file_sync": True,
                "max_devices": 50,
                "max_storage_mb": 10000,
                "priority_queue": True,
            },
        }
        
        config = configs.get(tier, configs[SubscriptionTier.FREE])
        return cls(
            tier=tier,
            last_reset=datetime.now().isoformat(),
            **config
        )


@dataclass 
class UserDevice:
    """Registriertes Gerät eines Users"""
    device_id: str
    device_name: str
    device_type: str  # desktop, mobile, cli_agent
    client_id: str
    client_secret_hash: str  # Nur Hash speichern!
    created_at: str = ""
    last_seen: str = ""
    is_active: bool = True
    
    # Geräte-spezifische Berechtigungen
    allow_bash: bool = True
    allow_file_write: bool = False
    allowed_paths: List[str] = field(default_factory=lambda: ["/home", "/tmp"])


@dataclass
class UserSettings:
    """Synchronisierbare Client-Einstellungen"""
    # UI Preferences
    theme: str = "dark"
    language: str = "de"
    font_size: int = 14
    
    # Model Preferences
    preferred_model: str = "auto"
    fallback_model: str = "ollama/llama3.2"
    temperature: float = 0.7
    
    # Behavior
    auto_save: bool = True
    confirm_destructive: bool = True
    show_token_count: bool = False
    
    # Codebase Settings
    default_project_path: str = ""
    ignored_patterns: List[str] = field(default_factory=lambda: [
        "__pycache__", ".git", "node_modules", ".venv", "*.pyc"
    ])
    
    # Notifications
    notify_on_complete: bool = True
    notify_on_error: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class User:
    """Vollständiges User-Objekt"""
    user_id: str
    email: str
    username: str
    created_at: str = ""
    
    # Subscription
    quota: UserQuota = field(default_factory=UserQuota)
    subscription_expires: str = ""
    
    # Settings & Devices
    settings: UserSettings = field(default_factory=UserSettings)
    devices: List[UserDevice] = field(default_factory=list)
    
    # Status
    is_active: bool = True
    is_verified: bool = False
    last_login: str = ""
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data["quota"] = asdict(self.quota)
        data["settings"] = self.settings.to_dict()
        data["devices"] = [asdict(d) for d in self.devices]
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> "User":
        quota = UserQuota(**data.pop("quota", {}))
        settings = UserSettings.from_dict(data.pop("settings", {}))
        devices = [UserDevice(**d) for d in data.pop("devices", [])]
        return cls(quota=quota, settings=settings, devices=devices, **data)


class CredentialStore:
    """Verschlüsselte Credential-Speicherung pro User"""
    
    def __init__(self, user_path: Path, master_key: str):
        self.cred_file = user_path / ".credentials.enc"
        self.key = self._derive_key(master_key, user_path.name)
        self.fernet = Fernet(self.key)
    
    def _derive_key(self, master: str, salt: str) -> bytes:
        """Leitet User-spezifischen Key ab"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt.encode(),
            iterations=100000,
        )
        return base64.urlsafe_b64encode(kdf.derive(master.encode()))
    
    def store(self, credentials: Dict[str, str]) -> None:
        """Speichert Credentials verschlüsselt"""
        data = json.dumps(credentials).encode()
        encrypted = self.fernet.encrypt(data)
        self.cred_file.write_bytes(encrypted)
        os.chmod(self.cred_file, 0o600)
    
    def load(self) -> Dict[str, str]:
        """Lädt Credentials"""
        if not self.cred_file.exists():
            return {}
        encrypted = self.cred_file.read_bytes()
        data = self.fernet.decrypt(encrypted)
        return json.loads(data)
    
    def get(self, provider: str) -> Optional[str]:
        """Holt einzelnen API Key"""
        creds = self.load()
        return creds.get(provider)
    
    def set(self, provider: str, api_key: str) -> None:
        """Setzt einzelnen API Key"""
        creds = self.load()
        creds[provider] = api_key
        self.store(creds)
    
    def remove(self, provider: str) -> bool:
        """Entfernt API Key"""
        creds = self.load()
        if provider in creds:
            del creds[provider]
            self.store(creds)
            return True
        return False
    
    def list_providers(self) -> List[str]:
        """Listet Provider mit gespeicherten Keys"""
        return list(self.load().keys())


class UserManager:
    """Zentrale User-Verwaltung"""
    
    def __init__(self, master_key: str = None):
        self.base_path = USERS_BASE
        self.master_key = master_key or os.getenv("USER_MASTER_KEY", "ailinux-default-key-change-me")
        self._users_cache: Dict[str, User] = {}
    
    def _user_path(self, user_id: str) -> Path:
        """Pfad zum User-Verzeichnis"""
        return self.base_path / user_id
    
    def _load_user(self, user_id: str) -> Optional[User]:
        """Lädt User aus Dateisystem"""
        user_path = self._user_path(user_id)
        user_file = user_path / "user.json"
        
        if not user_file.exists():
            return None
        
        data = json.loads(user_file.read_text())
        return User.from_dict(data)
    
    def _save_user(self, user: User) -> None:
        """Speichert User ins Dateisystem"""
        user_path = self._user_path(user.user_id)
        user_path.mkdir(parents=True, exist_ok=True)
        
        user_file = user_path / "user.json"
        user_file.write_text(json.dumps(user.to_dict(), indent=2))
        os.chmod(user_file, 0o600)
        
        # Cache aktualisieren
        self._users_cache[user.user_id] = user
    
    async def create_user(
        self,
        email: str,
        username: str,
        tier: SubscriptionTier = SubscriptionTier.FREE,
        wordpress_user_id: str = None
    ) -> Dict[str, Any]:
        """
        Erstellt neuen User (wird von WordPress Plugin aufgerufen)
        
        Returns:
            {user_id, client_id, client_secret, ...}
        """
        # User-ID generieren
        user_id = wordpress_user_id or f"u_{secrets.token_hex(8)}"
        
        # Prüfen ob schon existiert
        if self._user_path(user_id).exists():
            raise ValueError(f"User already exists: {user_id}")
        
        # User erstellen
        user = User(
            user_id=user_id,
            email=email,
            username=username,
            created_at=datetime.now().isoformat(),
            quota=UserQuota.for_tier(tier),
        )
        
        # Verzeichnis-Struktur erstellen
        user_path = self._user_path(user_id)
        (user_path / "devices").mkdir(parents=True, exist_ok=True)
        (user_path / "data").mkdir(parents=True, exist_ok=True)
        (user_path / "memory").mkdir(parents=True, exist_ok=True)
        
        # Erstes Device (Web-Client) registrieren
        client_id = f"web_{user_id}"
        client_secret = secrets.token_urlsafe(32)
        
        device = UserDevice(
            device_id=f"dev_{secrets.token_hex(4)}",
            device_name="Web Client",
            device_type="web",
            client_id=client_id,
            client_secret_hash=hashlib.sha256(client_secret.encode()).hexdigest(),
            created_at=datetime.now().isoformat(),
        )
        user.devices.append(device)
        
        # Speichern
        self._save_user(user)
        
        logger.info(f"User created: {user_id} ({email})")
        
        return {
            "user_id": user_id,
            "client_id": client_id,
            "client_secret": client_secret,  # Nur einmal zurückgeben!
            "tier": tier.value,
            "quota": asdict(user.quota),
        }
    
    async def get_user(self, user_id: str) -> Optional[User]:
        """Holt User (mit Cache)"""
        if user_id in self._users_cache:
            return self._users_cache[user_id]
        
        user = self._load_user(user_id)
        if user:
            self._users_cache[user_id] = user
        return user
    
    async def authenticate(self, client_id: str, client_secret: str) -> Optional[User]:
        """Authentifiziert Client und gibt User zurück"""
        # Client-ID Format: {type}_{user_id}
        parts = client_id.split("_", 1)
        if len(parts) != 2:
            return None
        
        user_id = parts[1]
        user = await self.get_user(user_id)
        
        if not user or not user.is_active:
            return None
        
        # Device finden und Secret prüfen
        secret_hash = hashlib.sha256(client_secret.encode()).hexdigest()
        
        for device in user.devices:
            if device.client_id == client_id and device.client_secret_hash == secret_hash:
                if device.is_active:
                    # Last seen aktualisieren
                    device.last_seen = datetime.now().isoformat()
                    self._save_user(user)
                    return user
        
        return None
    
    async def register_device(
        self,
        user_id: str,
        device_name: str,
        device_type: str = "desktop"
    ) -> Dict[str, str]:
        """Registriert neues Gerät für User"""
        user = await self.get_user(user_id)
        if not user:
            raise ValueError(f"User not found: {user_id}")
        
        # Quota prüfen
        if len(user.devices) >= user.quota.max_devices:
            raise ValueError(f"Max devices reached ({user.quota.max_devices})")
        
        # Neues Device erstellen
        client_id = f"{device_type}_{user_id}_{secrets.token_hex(4)}"
        client_secret = secrets.token_urlsafe(32)
        
        device = UserDevice(
            device_id=f"dev_{secrets.token_hex(4)}",
            device_name=device_name,
            device_type=device_type,
            client_id=client_id,
            client_secret_hash=hashlib.sha256(client_secret.encode()).hexdigest(),
            created_at=datetime.now().isoformat(),
        )
        
        user.devices.append(device)
        self._save_user(user)
        
        logger.info(f"Device registered: {client_id} for {user_id}")
        
        return {
            "device_id": device.device_id,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    
    async def sync_settings(
        self,
        user_id: str,
        settings: Dict[str, Any],
        merge: bool = True
    ) -> UserSettings:
        """Synchronisiert Client-Settings mit Server"""
        user = await self.get_user(user_id)
        if not user:
            raise ValueError(f"User not found: {user_id}")
        
        if merge:
            # Nur übergebene Felder aktualisieren
            current = user.settings.to_dict()
            current.update(settings)
            user.settings = UserSettings.from_dict(current)
        else:
            # Komplett ersetzen
            user.settings = UserSettings.from_dict(settings)
        
        self._save_user(user)
        return user.settings
    
    async def get_settings(self, user_id: str) -> Optional[UserSettings]:
        """Holt aktuelle Settings"""
        user = await self.get_user(user_id)
        return user.settings if user else None
    
    def get_credential_store(self, user_id: str) -> CredentialStore:
        """Gibt CredentialStore für User zurück"""
        user_path = self._user_path(user_id)
        return CredentialStore(user_path, self.master_key)
    
    async def check_quota(self, user_id: str) -> Dict[str, Any]:
        """Prüft Quota und gibt Status zurück"""
        user = await self.get_user(user_id)
        if not user:
            return {"error": "User not found"}
        
        quota = user.quota
        
        # Daily reset prüfen
        today = datetime.now().date().isoformat()
        if quota.last_reset[:10] != today:
            quota.requests_today = 0
            quota.last_reset = datetime.now().isoformat()
            self._save_user(user)
        
        remaining = quota.daily_requests - quota.requests_today
        
        # tier kann Enum oder String sein (nach JSON Deserialisierung)
        tier_value = quota.tier.value if hasattr(quota.tier, 'value') else quota.tier

        return {
            "tier": tier_value,
            "daily_limit": quota.daily_requests,
            "used_today": quota.requests_today,
            "remaining": max(0, remaining),
            "allow_cloud_models": quota.allow_cloud_models,
            "reset_at": (datetime.now().replace(hour=0, minute=0, second=0) + timedelta(days=1)).isoformat(),
        }
    
    async def increment_usage(self, user_id: str, count: int = 1) -> bool:
        """Erhöht Nutzungszähler, gibt False zurück wenn Limit erreicht"""
        user = await self.get_user(user_id)
        if not user:
            return False
        
        quota = user.quota
        
        # Daily reset prüfen
        today = datetime.now().date().isoformat()
        if quota.last_reset[:10] != today:
            quota.requests_today = 0
            quota.last_reset = datetime.now().isoformat()
        
        if quota.requests_today + count > quota.daily_requests:
            return False
        
        quota.requests_today += count
        self._save_user(user)
        return True
    
    async def upgrade_tier(
        self,
        user_id: str,
        new_tier: SubscriptionTier,
        expires: datetime = None
    ) -> bool:
        """Upgraded User auf neuen Tier (von WordPress Payment Webhook)"""
        user = await self.get_user(user_id)
        if not user:
            return False
        
        user.quota = UserQuota.for_tier(new_tier)
        user.subscription_expires = expires.isoformat() if expires else ""
        
        self._save_user(user)
        logger.info(f"User upgraded: {user_id} -> {new_tier.value}")
        return True
    
    async def list_users(self, active_only: bool = True) -> List[str]:
        """Listet alle User-IDs"""
        users = []
        for path in self.base_path.iterdir():
            if path.is_dir() and not path.name.startswith("."):
                if active_only:
                    user = await self.get_user(path.name)
                    if user and user.is_active:
                        users.append(path.name)
                else:
                    users.append(path.name)
        return users


# Singleton-Instanz
user_manager = UserManager()
