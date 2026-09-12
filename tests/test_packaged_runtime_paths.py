from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_crawler_defaults_follow_state_dir(tmp_path: Path) -> None:
    state = tmp_path / "state"
    env = os.environ.copy()
    env["TRIFORCE_STATE_DIR"] = str(state)
    env.pop("CRAWLER_SPOOL_DIR", None)
    env.pop("CRAWLER_TRAIN_DIR", None)
    code = (
        "from app.config import Settings; "
        "s=Settings(); "
        "print(s.crawler_spool_dir); print(s.crawler_train_dir)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )
    lines = proc.stdout.strip().splitlines()
    assert lines[-2:] == [
        str(state / "data" / "crawler_spool"),
        str(state / "data" / "crawler_spool" / "train"),
    ]


def test_debian_service_uses_fhs_runtime_paths() -> None:
    unit = (ROOT / "packaging" / "systemd" / "triforce.service").read_text()
    assert "TRIFORCE_STATE_DIR=/var/lib/triforce" in unit
    assert "TRIFORCE_DATA_DIR=/var/lib/triforce/data" in unit
    assert "TRIFORCE_LOG_DIR=/var/log/triforce" in unit
    assert "TRIFORCE_VAULT_DIR=/var/lib/triforce/.vault" in unit
    assert "TRIFORCE_CONFIG_FILE=/etc/triforce/triforce.env" in unit
    assert "ExecStart=/opt/triforce/scripts/start-triforce.sh" in unit
    assert "ExecStartPre=/opt/triforce/scripts/setup-venv.sh" not in unit
    assert "--host 0.0.0.0" not in unit


def test_package_helper_creates_writable_state_outside_opt() -> None:
    helper = (ROOT / "packaging" / "triforce-admin-helper.py").read_text()
    assert 'STATE_DIR = Path("/var/lib/triforce")' in helper
    assert 'LOG_DIR = Path("/var/log/triforce")' in helper
    assert 'ETC_DIR = Path("/etc/triforce")' in helper
    assert 'STATE_DIR / "crawler_spool"' in helper
    assert 'STATE_DIR / "data"' in helper
    assert 'STATE_DIR / ".vault"' in helper
    assert '_chown_tree(STATE_DIR, "triforce", "triforce")' in helper
    assert '_chown_tree(LOG_DIR, "triforce", "triforce")' in helper


def test_beta2_packages_use_epoch_and_exact_pairing() -> None:
    builder = (ROOT / "scripts" / "release" / "build-triforce-2.85-beta2.sh").read_text()
    assert 'DEBIAN_VERSION="${DEBIAN_VERSION:-1:${VERSION}-1}"' in builder
    assert 'Version: $DEBIAN_VERSION' in builder
    assert 'Depends: triforce-backend (= $DEBIAN_VERSION)' in builder
    assert '+compat1' not in builder
    assert 'triforce-backend (>= $VERSION)' not in builder


def test_source_bundle_service_is_package_mode_and_not_debhelper_magic() -> None:
    unit = (ROOT / "packaging" / "systemd" / "triforce-source-bundle.service").read_text()
    rules = (ROOT / "debian" / "rules").read_text()
    assert "User=triforce" in unit
    assert "WorkingDirectory=/opt/triforce" in unit
    assert "TRIFORCE_PYTHON=/opt/triforce/.venv/bin/python" in unit
    assert "TRIFORCE_STATE_DIR=/var/lib/triforce" in unit
    assert "ExecStart=/opt/triforce/scripts/start-triforce.sh" in unit
    assert "packaging/systemd/triforce-source-bundle.service" in rules
    assert not (ROOT / "debian" / "triforce-backend.service").exists()


def test_install_service_supports_explicit_source_and_package_modes() -> None:
    script = (ROOT / "scripts" / "install-service.sh").read_text()
    assert '--mode source|package' in script
    assert 'LOCAL_UNIT="/etc/systemd/system/triforce.service"' in script
    assert 'PACKAGE_UNIT="/usr/lib/systemd/system/triforce.service"' in script
    assert 'MODE="source"' in script
    assert 'rm -f "$LOCAL_UNIT"' in script


def test_prerm_does_not_stop_service_on_upgrade() -> None:
    prerm = (ROOT / "debian" / "prerm").read_text()
    assert 'remove|deconfigure' in prerm
    assert 'upgrade|failed-upgrade' in prerm
    upgrade_block = prerm.split('upgrade|failed-upgrade)', 1)[1].split(';;', 1)[0]
    assert 'systemctl stop' not in upgrade_block
    assert 'systemctl disable' not in upgrade_block


def test_admin_helper_uses_system_python_shebang_for_source_bundle() -> None:
    helper = (ROOT / "packaging" / "triforce-admin-helper.py").read_text().splitlines()[0]
    assert helper == "#!/usr/bin/python3"


def test_debian_package_preserves_operator_selected_source_service() -> None:
    preinst = (ROOT / "debian" / "preinst").read_text()
    postinst = (ROOT / "debian" / "postinst").read_text()
    rules = (ROOT / "debian" / "rules").read_text()
    installer = (ROOT / "scripts" / "install-service.sh").read_text()
    assert "/home/zombie/triforce/scripts/start-triforce.sh" in preinst
    assert "source-service.backup" in preinst
    assert "restore-source-service" in preinst
    assert "Restored operator-selected TriForce source service state" in postinst
    assert "systemctl restart triforce.service" not in postinst
    assert 'RUNNING_MARKER="$MIGRATION_DIR/source-service.was-running"' in preinst
    assert 'ENABLED_MARKER="$MIGRATION_DIR/source-service.was-enabled"' in preinst
    assert "systemctl is-active --quiet triforce.service" in preinst
    assert "systemctl is-enabled --quiet triforce.service" in preinst
    assert '[ -f "$RUNNING_MARKER" ] && ! systemctl is-active --quiet triforce.service' in postinst
    assert '[ -f "$ENABLED_MARKER" ]' in postinst
    assert "systemctl start triforce.service" in postinst
    assert "systemctl enable triforce.service" in postinst
    assert postinst.index('install -m 0644 "$BACKUP_UNIT" "$LOCAL_UNIT"') < postinst.index('/usr/lib/triforce/triforce-admin-helper runtime-init')
    assert "Skipping debhelper systemd autoscripts" in rules
    assert "dh_installsystemd --no-start" not in rules
    assert 'MODE_MARKER="$MODE_MARKER_DIR/source-mode.enabled"' in installer
    assert "printf 'source\\n' > \"$MODE_MARKER\"" in installer


def test_package_mode_switch_is_explicit_only() -> None:
    installer = (ROOT / "scripts" / "install-service.sh").read_text()
    assert 'MODE="source"' in installer
    assert 'rm -f "$MODE_MARKER"' in installer
    assert 'rm -f "$LOCAL_UNIT"' in installer
