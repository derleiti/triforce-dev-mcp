"""Canonical TriForce configuration file access.

This module deliberately lives next to :mod:`app.config` (rather than in an
``app.config`` package) so the long-standing ``app/config.py`` import cannot be
shadowed.

Resolution order for supported backend settings is:
    process environment > selected config file > Pydantic defaults

The selected file is ``TRIFORCE_CONFIG_FILE`` when set, otherwise
``/etc/triforce/triforce.env`` when present, otherwise the repository-local
``config/triforce.env``. Files are parsed as dotenv *data*; they are never
executed or sourced as shell code.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable, Mapping, get_args, get_origin, Literal

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_CONFIG = Path("/etc/triforce/triforce.env")
PROJECT_CONFIG = PROJECT_ROOT / "config" / "triforce.env"
CONFIG_ENV = "TRIFORCE_CONFIG_FILE"
MASKED_SECRET_VALUE = "••••••••"

# Explicit classification: never infer secrecy from a field name at runtime.
SECRET_ENV_KEYS = frozenset({
    "AILINUX_WEBHOOK_SECRET", "ANTHROPIC_API_KEY", "CHATGPT_PASS",
    "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ZONE_API_TOKEN", "CODESTRAL_API_KEY", "FEDERATION_SECRET",
    "FIREWORKS_API_KEY", "GEMINI_API_KEY", "GITHUB_TOKEN", "GOOGLE_API_KEY",
    "GOOGLE_AI_STUDIO_KEY", "GOOGLE_GEMINI_KEY", "GOOGLE_PASS",
    "GPT_OSS_API_KEY", "GROQ_API_KEY", "HUGGINGFACE_API_KEY", "INTERNAL_API_KEY",
    "JINA_API_KEY", "JWT_SECRET", "LEMONSQUEEZY_WEBHOOK_SECRET",
    "MAIL_IMAP_PASS", "MAIL_SMTP_PASS", "MCP_INTERNAL_PROFILE_VALUE",
    "MCP_OAUTH_PASS", "MISTRAL_API_KEY", "N8N_MCP_TOKEN", "NOVA_AI_INTERNAL_KEY",
    "NOVA_CHATGPT_PASS", "NOVA_CLAUDE_PASS", "NOVA_GOOGLE_PASS", "NOVA_MISTRAL_PASS",
    "OLLAMA_BEARER_TOKEN", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
    "STABLE_DIFFUSION_API_KEY", "STABLE_DIFFUSION_PASSWORD", "TOGETHER_API_KEY",
    "TRIFORCE_ADMIN_SECRET", "TRISTAR_GUI_PASSWORD", "USER_MASTER_KEY",
    "WEBHOOK_SECRET", "WORDPRESS_PASSWORD",
    "DOCKER_WORDPRESS_DB_PASSWORD", "DOCKER_WORDPRESS_DB_ROOT_PASSWORD",
    "DOCKER_FLARUM_DB_PASSWORD", "DOCKER_FLARUM_DB_ROOT_PASSWORD",
    "DOCKER_SEARXNG_SECRET",
})


class ConfigError(RuntimeError):
    """Base error for configuration persistence."""


class ConfigConflict(ConfigError):
    """Raised when a file changed since the editor loaded it."""


@dataclass(frozen=True)
class ConfigSnapshot:
    path: Path
    values: dict[str, str | None]
    digest: str
    mode: int | None


@dataclass(frozen=True)
class SettingMeta:
    name: str
    env_names: tuple[str, ...]
    category: str
    title: str
    description: str
    secret: bool
    default: Any
    value_type: str = "string"
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    storage: str = "config-file"
    unit: str | None = None
    required_restart: bool = True
    live_apply: bool = False
    deprecated: bool = False
    replaced_by: str | None = None


def resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path).expanduser().resolve()
    configured = os.environ.get(CONFIG_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if SYSTEM_CONFIG.is_file():
        return SYSTEM_CONFIG
    return PROJECT_CONFIG


def _read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return b""


def digest_file(path: Path) -> str:
    return sha256(_read_bytes(path)).hexdigest()


def _parse_bytes(raw: bytes) -> dict[str, str | None]:
    if not raw:
        return {}
    parsed = dotenv_values(stream=StringIO(raw.decode("utf-8")))
    return {str(k): (None if v is None else str(v)) for k, v in parsed.items()}


def load_snapshot(path: str | os.PathLike[str] | None = None) -> ConfigSnapshot:
    target = resolve_config_path(path)
    raw = _read_bytes(target)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else None
    return ConfigSnapshot(target, _parse_bytes(raw), sha256(raw).hexdigest(), mode)


def effective_environment(
    path: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return merged values and origin (``file``/``environment``) per key."""
    snap = load_snapshot(path)
    merged = {k: v for k, v in snap.values.items() if v is not None}
    origins = {k: "file" for k in merged}
    source = os.environ if environ is None else environ
    for key, value in source.items():
        merged[str(key)] = str(value)
        origins[str(key)] = "environment"
    return merged, origins


def _aliases(field: Any) -> tuple[str, ...]:
    alias = field.validation_alias
    if alias is None:
        return ()
    choices = getattr(alias, "choices", None)
    if choices:
        return tuple(str(x) for x in choices if isinstance(x, str))
    return (str(alias),)


def _category(name: str, env_names: Iterable[str]) -> str:
    joined = " ".join((name, *env_names)).upper()
    for marker, category in (
        ("MEMORY", "Memory"), ("EPISODIC", "Memory"),
        ("DOCKER_", "Docker"),
        ("MCP_", "MCP & Sicherheit"),
        ("AGENT", "Agenten"), ("CODEX", "Agenten"), ("OPENCODE", "Agenten"),
        ("NOVA_CLAUDE", "Agenten"), ("TRISTAR", "Agenten"),
        ("OLLAMA", "Provider & Modelle"),
        ("GEMINI", "Provider & Modelle"), ("OPENAI", "Provider & Modelle"),
        ("OPENROUTER", "Provider & Modelle"), ("ANTHROPIC", "Provider & Modelle"),
        ("MISTRAL", "Provider & Modelle"), ("GROQ", "Provider & Modelle"),
        ("CEREBRAS", "Provider & Modelle"), ("NVIDIA", "Provider & Modelle"),
        ("CLOUDFLARE", "Provider & Modelle"), ("REDIS", "Server & Integrationen"),
        ("WORDPRESS", "Server & Integrationen"), ("MAIL_", "Server & Integrationen"),
        ("CRAWLER", "Server & Integrationen"), ("FEDERATION", "Server & Integrationen"),
        ("TELEGRAM", "Server & Integrationen"), ("N8N", "Server & Integrationen"),
        ("SEARX", "Server & Integrationen"), ("SEARCH", "Server & Integrationen"),
        ("TRIFORCE_DEPLOYMENT", "Server"), ("TRIFORCE_BIND", "Server"),
        ("TRIFORCE_API_PORT", "Server"), ("CORS", "Server & Sicherheit"),
    ):
        if marker in joined:
            return category
    return "Erweitert"



def _field_type_name(annotation: Any) -> str:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return "choice"
    if origin is not None:
        if origin in (list, tuple, set):
            return "list"
        if origin is dict:
            return "json"
        non_none = [arg for arg in args if arg is not type(None)]
        if len(non_none) == 1:
            return _field_type_name(non_none[0])
    if annotation is bool: return "bool"
    if annotation is int: return "integer"
    if annotation is float: return "number"
    if annotation is str: return "string"
    name = getattr(annotation, "__name__", "")
    if name in {"AnyHttpUrl", "HttpUrl"}: return "url"
    try:
        from enum import Enum
        if isinstance(annotation, type) and issubclass(annotation, Enum): return "choice"
    except TypeError:
        pass
    return name or str(annotation).replace("typing.", "")


def _field_choices(annotation: Any) -> tuple[str, ...]:
    origin = get_origin(annotation)
    args = get_args(annotation)
    if origin is Literal:
        return tuple(str(x) for x in args)
    non_none = [arg for arg in args if arg is not type(None)] if origin is not None else []
    if len(non_none) == 1:
        return _field_choices(non_none[0])
    try:
        from enum import Enum
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return tuple(str(member.value) for member in annotation)
    except TypeError:
        pass
    return ()


def _field_limits(field: Any) -> tuple[int | float | None, int | float | None]:
    minimum = maximum = None
    for item in field.metadata:
        for attr in ("ge", "gt"):
            value = getattr(item, attr, None)
            if value is not None: minimum = value
        for attr in ("le", "lt"):
            value = getattr(item, attr, None)
            if value is not None: maximum = value
        min_length = getattr(item, "min_length", None)
        max_length = getattr(item, "max_length", None)
        if minimum is None and min_length is not None: minimum = min_length
        if maximum is None and max_length is not None: maximum = max_length
    return minimum, maximum

def settings_inventory() -> list[SettingMeta]:
    # Lazy import avoids a cycle: app.config imports effective_environment.
    from .config import Settings

    result: list[SettingMeta] = []
    for name, field in Settings.model_fields.items():
        env_names = _aliases(field)
        title = name.replace("_", " ").strip().title()
        minimum, maximum = _field_limits(field)
        result.append(SettingMeta(
            name=name,
            env_names=env_names,
            category=_category(name, env_names),
            title=title,
            description=field.description or "",
            secret=any(alias in SECRET_ENV_KEYS for alias in env_names),
            default=None if field.is_required() else field.default,
            value_type=_field_type_name(field.annotation),
            minimum=minimum,
            maximum=maximum,
            choices=_field_choices(field.annotation),
            deprecated=bool(getattr(field, "deprecated", False)),
        ))
    return result


def validate_supported_values(
    file_values: Mapping[str, str | None],
    environ: Mapping[str, str] | None = None,
) -> Any:
    """Validate a prospective configuration with the canonical Pydantic model."""
    from .config import Settings

    merged = {k: v for k, v in file_values.items() if v is not None}
    source = os.environ if environ is None else environ
    # Process environment wins by design, but ignore the config selector itself.
    merged.update({str(k): str(v) for k, v in source.items() if k != CONFIG_ENV})
    return Settings.model_validate(merged, by_alias=True, by_name=True)


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if text == "":
        return '""'
    if any(ch.isspace() for ch in text) or any(ch in text for ch in "#'\"$`\\"):
        return json.dumps(text, ensure_ascii=False)
    return text


def _replace_lines(original: str, updates: Mapping[str, Any]) -> str:
    pending = dict(updates)
    update_keys = set(pending)
    written: set[str] = set()
    out: list[str] = []
    for line in original.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in update_keys:
                # Canonical dotenv files must not retain duplicate active keys.
                # python-dotenv uses the last value, so leaving a later legacy
                # duplicate behind can silently undo a successful settings update.
                if key in written:
                    continue
                newline = "\n" if line.endswith("\n") else ""
                out.append(f"{key}={_format_value(pending.pop(key))}{newline}")
                written.add(key)
                continue
        out.append(line)
    if pending:
        if out and not out[-1].endswith("\n"):
            out[-1] += "\n"
        for key, value in pending.items():
            out.append(f"{key}={_format_value(value)}\n")
    return "".join(out)


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


def save_updates(
    updates: Mapping[str, Any],
    *,
    path: str | os.PathLike[str] | None = None,
    expected_digest: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConfigSnapshot:
    """Validate and atomically persist updates while preserving unknown entries.

    A single ``.last-good`` file is maintained; it is replaced rather than
    accumulated. The backup inherits the protected mode of the source file.
    """
    target = resolve_config_path(path)
    before = load_snapshot(target)
    if expected_digest is not None and before.digest != expected_digest:
        raise ConfigConflict(f"configuration changed on disk: {target}")

    old_text = _read_bytes(target).decode("utf-8") if target.exists() else ""
    candidate_text = _replace_lines(old_text, updates)
    candidate_values = _parse_bytes(candidate_text.encode("utf-8"))
    validate_supported_values(candidate_values, environ=environ)

    mode = before.mode if before.mode is not None else 0o600
    if target.exists():
        backup = target.with_name(target.name + ".last-good")
        _atomic_write(backup, _read_bytes(target), mode)
    _atomic_write(target, candidate_text.encode("utf-8"), mode)
    return load_snapshot(target)


def restore_last_good(
    *, path: str | os.PathLike[str] | None = None,
    expected_digest: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConfigSnapshot:
    target = resolve_config_path(path)
    current = load_snapshot(target)
    if expected_digest is not None and current.digest != expected_digest:
        raise ConfigConflict(f"configuration changed on disk: {target}")
    backup = target.with_name(target.name + ".last-good")
    if not backup.is_file():
        raise ConfigError("no last-good configuration available")
    values = _parse_bytes(backup.read_bytes())
    validate_supported_values(values, environ=environ)
    mode = current.mode or stat.S_IMODE(backup.stat().st_mode)
    _atomic_write(target, backup.read_bytes(), mode)
    return load_snapshot(target)



def redact_dotenv_text(text: str) -> str:
    """Return dotenv text with explicitly-classified secret values masked."""
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in SECRET_ENV_KEYS:
                newline = "\n" if line.endswith("\n") else ""
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}{key}={MASKED_SECRET_VALUE}{newline}")
                continue
        out.append(line)
    return "".join(out)


def restore_masked_secrets(edited_text: str, original_text: str) -> str:
    """Restore unchanged masked secret lines from the protected original text."""
    originals: dict[str, str] = {}
    for line in original_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in SECRET_ENV_KEYS:
                originals[key] = line
    out: list[str] = []
    for line in edited_text.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            key = key.strip()
            if key in SECRET_ENV_KEYS and value.strip() == MASKED_SECRET_VALUE and key in originals:
                original = originals[key]
                if line.endswith("\n") and not original.endswith("\n"):
                    original += "\n"
                elif not line.endswith("\n") and original.endswith("\n"):
                    original = original[:-1]
                out.append(original)
                continue
        out.append(line)
    return "".join(out)


def save_raw_text(
    text: str,
    *,
    path: str | os.PathLike[str] | None = None,
    expected_digest: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ConfigSnapshot:
    """Validate and atomically save complete dotenv text, preserving its layout."""
    target = resolve_config_path(path)
    before = load_snapshot(target)
    if expected_digest is not None and before.digest != expected_digest:
        raise ConfigConflict(f"configuration changed on disk: {target}")
    try:
        candidate_values = dict(dotenv_values(stream=StringIO(text)))
    except Exception as exc:
        raise ConfigError(f"invalid dotenv data: {exc}") from exc
    validate_supported_values(candidate_values, environ=environ)
    mode = before.mode if before.mode is not None else 0o600
    if target.exists():
        backup = target.with_name(target.name + ".last-good")
        _atomic_write(backup, _read_bytes(target), mode)
    _atomic_write(target, text.encode("utf-8"), mode)
    return load_snapshot(target)

def redact(values: Mapping[str, Any]) -> dict[str, Any]:
    return {k: ("***" if k in SECRET_ENV_KEYS and v not in (None, "") else v) for k, v in values.items()}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Read canonical TriForce config values")
    parser.add_argument("--config")
    parser.add_argument("--get", dest="key")
    args = parser.parse_args()
    merged, _ = effective_environment(args.config)
    if args.key:
        value = merged.get(args.key)
        if value is not None:
            print(value)
