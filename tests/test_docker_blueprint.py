from __future__ import annotations

from pathlib import Path
import asyncio
import pytest

from app.docker_stack import ACTIONS, PROFILES, command
from app.settings_store import SECRET_ENV_KEYS, settings_inventory

ROOT = Path(__file__).parents[1]
BLUEPRINT = ROOT / "docker" / "blueprint"
COMPOSE = BLUEPRINT / "docker-compose.yml"
STACK = BLUEPRINT / "maintenance" / "stack.sh"
BUILDER = ROOT / "scripts" / "release" / "build-triforce-2.85-beta1.sh"
HELPER = ROOT / "packaging" / "triforce-admin-helper.py"
ADMIN = ROOT / "app" / "mcp" / "structured_admin.py"


def test_blueprint_is_lightweight_and_excludes_production_payloads():
    assert COMPOSE.is_file()
    assert not (BLUEPRINT / "wordpress" / "html").exists()
    assert not (BLUEPRINT / "repository" / "repo" / "mirror").exists()
    assert not any("{" in part or "}" in part for path in BLUEPRINT.rglob("*") for part in path.parts)
    files = [p for p in BLUEPRINT.rglob("*") if p.is_file()]
    assert sum(p.stat().st_size for p in files) < 1_000_000
    assert max((p.stat().st_size for p in files), default=0) < 250_000


def test_maintenance_script_is_fixed_and_never_sources_config_as_shell():
    text = STACK.read_text()
    assert "source " not in text
    assert "eval " not in text
    assert 'case "$ACTION" in validate|status|up|down|restart|pull|logs)' in text
    assert 'case "$PROFILE" in all|redis|wordpress|flarum|searxng|n8n|repository|mailserver)' in text
    assert '--env-file "$ENV_FILE" -f "$COMPOSE"' in text


def test_docker_stack_command_rejects_arbitrary_action_profile_or_path():
    assert set(ACTIONS) == {"validate", "status", "up", "down", "restart", "pull", "logs"}
    assert set(PROFILES) == {"all", "redis", "wordpress", "flarum", "searxng", "n8n", "repository", "mailserver"}
    cmd = command("status", "redis")
    assert cmd[-2:] == ["status", "redis"]
    assert cmd[0].endswith("docker/blueprint/maintenance/stack.sh")
    with pytest.raises(ValueError):
        command("shell", "redis")
    with pytest.raises(ValueError):
        command("status", "../../tmp")


def test_docker_settings_are_structured_and_secrets_are_classified():
    inventory = settings_inventory()
    docker = [item for item in inventory if item.category == "Docker"]
    names = {item.env_names[0] for item in docker if item.env_names}
    assert "DOCKER_PROJECT_NAME" in names
    assert "DOCKER_WORDPRESS_PORT" in names
    assert "DOCKER_REPOSITORY_DATA_PATH" in names
    assert "DOCKER_MAIL_IMAPS_PORT" in names
    for key in (
        "DOCKER_WORDPRESS_DB_PASSWORD",
        "DOCKER_WORDPRESS_DB_ROOT_PASSWORD",
        "DOCKER_FLARUM_DB_PASSWORD",
        "DOCKER_FLARUM_DB_ROOT_PASSWORD",
        "DOCKER_SEARXNG_SECRET",
    ):
        assert key in SECRET_ENV_KEYS
        meta = next(x for x in inventory if key in x.env_names)
        assert meta.secret


def test_fresh_install_generates_docker_secrets_without_static_values():
    text = HELPER.read_text()
    for key in (
        "DOCKER_WORDPRESS_DB_PASSWORD",
        "DOCKER_WORDPRESS_DB_ROOT_PASSWORD",
        "DOCKER_FLARUM_DB_PASSWORD",
        "DOCKER_FLARUM_DB_ROOT_PASSWORD",
        "DOCKER_SEARXNG_SECRET",
    ):
        assert f'"{key}": secrets.token_urlsafe(32)' in text


def test_package_builder_copies_only_blueprint_not_historical_docker_tree():
    text = BUILDER.read_text()
    assert 'cp -a "$ROOT/docker/blueprint" "$pkg/opt/triforce/docker/blueprint"' in text
    assert 'cp -a "$ROOT/docker"' not in text
    assert 'docker/wordpress/html' not in text
    assert 'docker/repository/repo' not in text


def test_mcp_exposes_one_semantic_fixed_docker_stack_tool():
    text = ADMIN.read_text()
    assert '"name":"docker_stack"' in text
    assert '"validate","status","up","down","restart","pull","logs"' in text
    assert '"all","redis","wordpress","flarum","searxng","n8n","repository","mailserver"' in text
    assert "run_docker_stack(action, profile)" in text


def test_docker_stack_is_canonical_admin_inventory_tool():
    from app.mcp.tool_registry_unified import get_unified_tools, filter_tools_for_profile
    tools = get_unified_tools()
    by_name = {tool["name"]: tool for tool in tools}
    assert "docker_stack" in by_name
    assert by_name["docker_stack"]["x_inventory"] == "admin"
    admin_names = {tool["name"] for tool in filter_tools_for_profile(tools, "admin")}
    assert "docker_stack" in admin_names
