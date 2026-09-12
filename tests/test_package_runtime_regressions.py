import asyncio
import importlib.util
from pathlib import Path

from app.services.tristar_mcp import tristar_mcp
from app.settings_store import load_snapshot


def _load_admin_helper():
    path = Path(__file__).parents[1] / "packaging" / "triforce-admin-helper.py"
    spec = importlib.util.spec_from_file_location("triforce_admin_helper_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_config_set_uses_canonical_dotenv_and_masks_secrets(tmp_path, monkeypatch):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("OLLAMA_TIMEOUT_MS=120000\nOPENROUTER_API_KEY=old-secret\n")
    monkeypatch.setenv("TRIFORCE_CONFIG_FILE", str(cfg))

    result = asyncio.run(tristar_mcp.set_setting("OLLAMA_TIMEOUT_MS", "90000"))
    assert result["status"] == "saved"
    assert result["storage"] == "canonical-env"
    assert result["path"] == str(cfg)
    assert load_snapshot(cfg).values["OLLAMA_TIMEOUT_MS"] == "90000"
    assert not (tmp_path / "config.json").exists()

    secret = asyncio.run(tristar_mcp.set_setting("OPENROUTER_API_KEY", "new-secret"))
    assert secret["status"] == "saved"
    assert secret["value"] == "********"
    assert load_snapshot(cfg).values["OPENROUTER_API_KEY"] == "new-secret"


def test_config_set_rejects_unknown_legacy_key(tmp_path, monkeypatch):
    cfg = tmp_path / "triforce.env"
    cfg.write_text("OLLAMA_TIMEOUT_MS=120000\n")
    monkeypatch.setenv("TRIFORCE_CONFIG_FILE", str(cfg))
    before = cfg.read_text()

    result = asyncio.run(tristar_mcp.set_setting("ollama.timeout", "10"))
    assert result["status"] == "rejected"
    assert cfg.read_text() == before


def test_tristar_migration_moves_existing_directory_without_data_loss(tmp_path):
    helper = _load_admin_helper()
    legacy = tmp_path / "legacy"
    target = tmp_path / "state" / "tristar"
    (legacy / "agents").mkdir(parents=True)
    (legacy / "agents" / "agents.json").write_text('{"ok": true}')
    (legacy / "memory").mkdir()
    (legacy / "memory" / "one.json").write_text('{}')

    helper.migrate_legacy_tristar(legacy, target)

    assert legacy.is_symlink()
    assert legacy.resolve() == target.resolve()
    assert (target / "agents" / "agents.json").read_text() == '{"ok": true}'
    assert (target / "memory" / "one.json").is_file()


def test_tristar_migration_is_idempotent_for_correct_symlink(tmp_path):
    helper = _load_admin_helper()
    target = tmp_path / "state" / "tristar"
    target.mkdir(parents=True)
    legacy = tmp_path / "legacy"
    legacy.symlink_to(target, target_is_directory=True)
    helper.migrate_legacy_tristar(legacy, target)
    assert legacy.resolve() == target.resolve()


def test_tristar_migration_refuses_conflict(tmp_path):
    helper = _load_admin_helper()
    legacy = tmp_path / "legacy"
    target = tmp_path / "state" / "tristar"
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    (legacy / "agents").mkdir()
    (target / "agents").mkdir()

    try:
        helper.migrate_legacy_tristar(legacy, target)
    except SystemExit as exc:
        assert "conflict" in str(exc).lower()
    else:
        raise AssertionError("conflicting state must not be overwritten")


def test_mcp_well_known_uses_product_version_and_current_protocol():
    text = (Path(__file__).parents[1] / "app" / "routes" / "mcp_remote.py").read_text()
    assert '"version": VERSION' in text
    assert '"description": f"AILinux AI Backend {VERSION}' in text
    assert '"mcp_version": "2025-11-25"' in text
    # Legacy protocol support remains present for older clients.
    assert '"2024-11-05"' in text


def test_openrouter_default_is_current_chat_capable_fallback():
    from app.config import Settings
    assert Settings.model_fields["openrouter_default_model"].default == "nvidia/nemotron-3-ultra-550b-a55b:free"


def test_package_ssh_identity_migration_is_safe(tmp_path, monkeypatch):
    helper = _load_admin_helper()
    legacy = tmp_path / "legacy-ssh"
    target = tmp_path / "state-ssh"
    legacy.mkdir()
    (legacy / "id_ed25519").write_text("private-test-key")
    (legacy / "id_ed25519.pub").write_text("public-test-key")

    monkeypatch.setattr(helper.shutil, "chown", lambda *args, **kwargs: None)
    helper.migrate_legacy_ssh_identity(legacy, target)

    assert (target / "id_ed25519").read_text() == "private-test-key"
    assert (target / "id_ed25519.pub").read_text() == "public-test-key"
    assert (target / "id_ed25519").stat().st_mode & 0o777 == 0o600
    assert (target / "id_ed25519.pub").stat().st_mode & 0o777 == 0o644

    (legacy / "id_ed25519").write_text("replacement-must-not-win")
    helper.migrate_legacy_ssh_identity(legacy, target)
    assert (target / "id_ed25519").read_text() == "private-test-key"


def test_packaged_service_uses_state_owned_ssh_identity():
    root = Path(__file__).parents[1]
    unit = (root / "packaging/systemd/triforce.service").read_text()
    admin = (root / "app/mcp/structured_admin.py").read_text()
    assert "Environment=TRIFORCE_SSH_KEY=/var/lib/triforce/.ssh/id_ed25519" in unit
    assert 'SSH_KEY_PATH = Path(os.environ.get("TRIFORCE_SSH_KEY"' in admin
    assert 'key_path = str(SSH_KEY_PATH)' in admin
    assert '["-i", str(SSH_KEY_PATH)]' in admin
    assert '/home/zombie/.ssh/id_ed25519' not in admin


def test_beta2_upgrade_stops_running_service_before_unpack_and_restores_it():
    root = Path(__file__).parents[1]
    builder = (root / "scripts/release/build-triforce-2.85-beta2.sh").read_text()
    assert 'DEBIAN_VERSION="${DEBIAN_VERSION:-1:${VERSION}-1}"' in builder
    assert 'DEB_FILE_VERSION="${DEBIAN_VERSION#*:}"' in builder
    assert 'cat > "$pkg/DEBIAN/preinst"' in builder
    assert 'systemctl is-active --quiet triforce.service' in builder
    assert 'systemctl stop triforce.service' in builder
    assert 'MARKER=/run/triforce-package-was-active' in builder
    assert 'systemctl restart triforce.service' in builder
    assert 'Fresh installs remain an explicit operator choice' in builder
