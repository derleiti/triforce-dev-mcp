from pathlib import Path

from app.legacy_settings import (
    NEVER_IMPORT, PRODUCTION_COMPAT_OVERRIDES, apply_import, build_plan,
)
from app.settings_store import load_snapshot


def test_plan_contains_names_not_values(tmp_path):
    source = tmp_path / "legacy.env"
    secret = "super-secret-test-value"
    source.write_text(
        "OPENROUTER_API_KEY=" + secret + "\n"
        "FEDERATION_SECRET=federation-secret\n"
        "INSTALL_DIR=/home/zombie/triforce\n"
        "WP_HTML_PATH=/home/zombie/triforce/docker/wordpress/html\n"
        "UNKNOWN_OLD_FLAG=yes\n"
    )
    plan = build_plan(source)
    payload = str(plan.to_dict())
    assert "OPENROUTER_API_KEY" in plan.import_keys
    assert "FEDERATION_SECRET" in plan.import_keys
    assert "OPENROUTER_API_KEY" in plan.secret_keys
    assert secret not in payload
    assert "federation-secret" not in payload
    assert "INSTALL_DIR" not in plan.import_keys
    assert "WP_HTML_PATH" not in plan.import_keys


def test_import_preserves_package_values_and_forces_proxy_compat(tmp_path):
    source = tmp_path / "legacy.env"
    target = tmp_path / "new.env"
    source.write_text(
        "OPENROUTER_API_KEY=legacy-key\n"
        "FEDERATION_SECRET=legacy-fed\n"
        "INSTALL_DIR=/home/zombie/triforce\n"
        "TRIFORCE_API_PORT=9999\n"
        "TRIFORCE_BIND_HOST=127.0.0.1\n"
    )
    target.write_text("TRIFORCE_ADMIN_SECRET=new-package-secret\nTRIFORCE_API_PORT=9100\n")
    apply_import(target, source)
    values = load_snapshot(target).values
    assert values["OPENROUTER_API_KEY"] == "legacy-key"
    assert values["FEDERATION_SECRET"] == "legacy-fed"
    assert values["TRIFORCE_ADMIN_SECRET"] == "new-package-secret"
    assert values["TRIFORCE_API_PORT"] == "9000"
    assert values["TRIFORCE_BIND_HOST"] == "0.0.0.0"
    for key in NEVER_IMPORT:
        assert key not in values


def test_production_compat_overrides_are_explicit():
    assert PRODUCTION_COMPAT_OVERRIDES == {
        "TRIFORCE_BIND_HOST": "0.0.0.0",
        "TRIFORCE_API_PORT": "9000",
    }
