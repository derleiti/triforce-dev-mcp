"""Safe migration of legacy TriForce dotenv settings into the packaged config.

Legacy files are treated strictly as dotenv data. Only settings understood by
TriForce 2.85 or still explicitly consumed by the runtime are imported. Docker
compose/site data remains in the legacy tree and is intentionally not copied.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from io import StringIO
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from .settings_store import SECRET_ENV_KEYS, load_snapshot, save_updates, settings_inventory

LEGACY_CANDIDATES = (
    Path("/home/zombie/triforce-legacy/config/triforce.env"),
    Path("/home/zombie/triforce-legacy/.env"),
    Path("/home/zombie/triforce/config/triforce.env"),
    Path("/home/zombie/triforce/.env"),
)

# These are not yet represented by the canonical Pydantic settings model, but
# current 2.85 runtime modules still read them directly from the environment.
LEGACY_RUNTIME_KEYS = frozenset({
    "ADMIN_EMAIL", "ADMIN_PASSWORD", "ALLOW_AUTO_REGISTER", "AUTO_BOOTSTRAP_AGENTS",
    "CLOUDFLARE_AI_WORKERS_API", "FEDERATION_NODE_ID", "FEDERATION_SECRET",
    "FEDERATION_TOKEN", "FLARUM_API", "GOOGLE_CLIENT_ID", "INTERNAL_API_KEY",
    "JWT_SECRET", "KIMI_API_KEY", "LEMONSQUEEZY_PRODUCT_ENTITLEMENTS",
    "LEMONSQUEEZY_WEBHOOK_SECRET", "MAIL_HOSTNAME", "N8N_MCP_TOKEN", "N8N_MCP_URL",
    "NOVA_AI_INTERNAL_KEY", "NVIDIA_DISABLED_MODELS", "REDIS_HOST", "REDIS_PORT",
    "REPLICATE_API_KEY", "USER_MASTER_KEY", "WEBHOOK_SECRET", "WORDPRESS_APP_PASSWORD",
    "WORDPRESS_APP_USER",
})

# Never import these old installation/layout variables even if future code starts
# referencing them. Package ownership and writable state paths are fixed by the
# Debian layout and must not drift back into the checkout.
NEVER_IMPORT = frozenset({
    "INSTALL_DIR", "DATA_DIR", "BACKUP_DIR", "MCP_CONFIG_DIR", "TRIFORCE_AUTH_DIR",
    "WP_HTML_PATH", "REPO_DATA_PATH", "N8N_DATA_DIR",
})

# Compatibility with the existing api.ailinux.me Apache reverse proxy during the
# first package migration. These values are explicit and visible in the preview.
PRODUCTION_COMPAT_OVERRIDES = {
    "TRIFORCE_BIND_HOST": "0.0.0.0",
    "TRIFORCE_API_PORT": "9000",
}


@dataclass(frozen=True)
class LegacyImportPlan:
    source: str | None
    source_keys: int
    import_keys: tuple[str, ...]
    secret_keys: tuple[str, ...]
    skipped_keys: int
    overrides: Mapping[str, str]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def find_legacy_source() -> Path | None:
    for path in LEGACY_CANDIDATES:
        if path.is_file():
            return path
    return None


def _parse(path: Path) -> dict[str, str]:
    parsed = dotenv_values(stream=StringIO(path.read_text(encoding="utf-8")))
    return {str(k): str(v) for k, v in parsed.items() if k and v is not None}


def supported_import_keys() -> frozenset[str]:
    structured = {alias for row in settings_inventory() for alias in row.env_names}
    return frozenset((structured | LEGACY_RUNTIME_KEYS) - NEVER_IMPORT)


def build_plan(source: Path | None = None, *, production_compat: bool = True) -> LegacyImportPlan:
    path = source or find_legacy_source()
    if path is None:
        return LegacyImportPlan(None, 0, (), (), 0, PRODUCTION_COMPAT_OVERRIDES if production_compat else {})
    values = _parse(path)
    allowed = supported_import_keys()
    import_keys = tuple(sorted(k for k in values if k in allowed))
    secrets = tuple(sorted(k for k in import_keys if k in SECRET_ENV_KEYS))
    overrides = dict(PRODUCTION_COMPAT_OVERRIDES if production_compat else {})
    return LegacyImportPlan(str(path), len(values), import_keys, secrets, len(values) - len(import_keys), overrides)


def apply_import(target: Path, source: Path | None = None, *, production_compat: bool = True) -> LegacyImportPlan:
    plan = build_plan(source, production_compat=production_compat)
    if plan.source is None:
        raise FileNotFoundError("no supported legacy TriForce config found")
    legacy = _parse(Path(plan.source))
    updates = {key: legacy[key] for key in plan.import_keys}
    updates.update(plan.overrides)
    current = load_snapshot(target)
    save_updates(updates, path=target, expected_digest=current.digest, environ={})
    return plan
