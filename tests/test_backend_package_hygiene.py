from pathlib import Path


def test_debian_rules_does_not_copy_live_config_tree():
    rules = Path('debian/rules').read_text()
    assert 'cp -a config ' not in rules
    assert 'config/triforce.env.example' in rules
    assert 'verify-backend-staging.py' in rules


def test_package_unit_uses_fhs_config_dir():
    unit = Path('packaging/systemd/triforce-source-bundle.service').read_text()
    assert 'TRIFORCE_CONFIG_FILE=/etc/triforce/triforce.env' in unit
    assert 'TRIFORCE_CONFIG_DIR=/etc/triforce' in unit
    assert 'TRIFORCE_CONFIG_DIR=/opt/triforce/config' not in unit


def test_staging_guard_blocks_sensitive_and_host_local_artifacts():
    guard = Path('packaging/verify-backend-staging.py').read_text()
    for token in ('federation_psk.key','users.json','config/agents','.bun','.cache','/home/','/root/','/etc/systemd/system/triforce.service'):
        assert token in guard


def test_debhelper_service_actions_are_non_intrusive():
    rules = Path('debian/rules').read_text()
    assert 'Skipping debhelper systemd autoscripts' in rules
    assert 'dh_installsystemd --no-start' not in rules


def test_only_runtime_script_is_packaged():
    rules = Path('debian/rules').read_text()
    assert 'cp -a scripts ' not in rules
    assert 'scripts/start-triforce.sh' in rules


def test_runtime_init_preserves_operator_source_trees():
    helper = Path('packaging/triforce-admin-helper.py').read_text()
    marker = 'if source_mode:'
    assert marker in helper
    source_branch = helper.split(marker, 1)[1].split('for path, mode in', 1)[0]
    assert '_chown_tree' not in source_branch
    assert 'package-runtime' in source_branch
    assert 'return' in source_branch


def test_package_metadata_isolated_from_shared_source_state():
    postinst = Path('debian/postinst').read_text()
    preinst = Path('debian/preinst').read_text()
    assert 'PACKAGE_STATE="$STATE/package-runtime"' in postinst
    assert 'REQ_HASH="$PACKAGE_STATE/.requirements.sha256"' in postinst
    assert 'MIGRATION_DIR="$PACKAGE_STATE/migration"' in postinst
    assert 'MIGRATION_DIR=/var/lib/triforce/package-runtime/migration' in preinst
    assert 'install -d -m 0750 /etc/triforce' not in postinst


def test_config_init_does_not_create_package_config_in_source_mode():
    helper = Path('packaging/triforce-admin-helper.py').read_text()
    section = helper.split('def config_init()', 1)[1].split('def service_install()', 1)[0]
    assert '/etc/systemd/system/triforce.service' in section
    assert 'Package installation is file-only' in section


def test_admin_helper_service_repair_uses_packaged_reference_unit():
    rules = Path("debian/rules").read_text()
    helper = Path("packaging/triforce-admin-helper.py").read_text()
    assert "/usr/share/triforce/systemd/triforce.service" in helper
    assert "usr/share/triforce/systemd/triforce.service" in rules
    assert "usr/lib/systemd/system/triforce.service" in rules


def test_backend_version_metadata_matches_debian_upstream_version():
    import re
    version = Path("VERSION").read_text().strip()
    config = Path("app/config.py").read_text()
    changelog = Path("debian/changelog").read_text().splitlines()[0]
    match = re.search(r"\((?:\d+:)?([^-]+)-[^)]+\)", changelog)
    assert match, changelog
    assert version == match.group(1)
    assert f'VERSION = "{version}"' in config


def test_package_ships_tmpfiles_and_logrotate_policy():
    tmpfiles = Path("debian/triforce-backend.tmpfiles").read_text()
    logrotate = Path("debian/triforce-backend.logrotate").read_text()
    assert "/var/lib/triforce" in tmpfiles
    assert "/var/lib/triforce/package-runtime" in tmpfiles
    assert "/var/log/triforce" in tmpfiles
    assert "/run/triforce" in tmpfiles
    assert "/var/log/triforce/*.log" in logrotate
    assert "rotate 14" in logrotate
    assert "copytruncate" in logrotate


def test_package_does_not_ship_active_env_conffiles():
    rules = Path("debian/rules").read_text()
    assert "/etc/triforce/triforce.env" not in rules
    assert "/etc/triforce/node.env" not in rules
    assert not Path("debian/triforce-backend.conffiles").exists()


def test_prerm_preserves_source_override_on_remove():
    prerm = Path("debian/prerm").read_text()
    assert "LOCAL_UNIT=/etc/systemd/system/triforce.service" in prerm
    assert 'if [ ! -e "$LOCAL_UNIT" ] && [ ! -L "$LOCAL_UNIT" ]; then' in prerm
    assert "deb-systemd-invoke stop triforce.service" in prerm
    assert "deb-systemd-helper disable triforce.service" in prerm
    assert "systemctl stop" not in prerm
    assert "systemctl disable" not in prerm


def test_package_normalizes_python_modes_and_drops_historical_repair_copy():
    rules = Path("debian/rules").read_text()
    guard = Path("packaging/verify-backend-staging.py").read_text()
    assert "Normalize Python source modes in the package only" in rules
    assert "chmod 0644" in rules
    assert "chmod 0755" in rules
    assert "model_registry.py_20260410_fix" in rules
    assert "forbidden historical repair copy" in guard


def test_legacy_upgrade_state_restore_is_strictly_marker_gated():
    preinst = Path("debian/preinst").read_text()
    postinst = Path("debian/postinst").read_text()
    assert "source-service.was-running" in preinst
    assert "source-service.was-enabled" in preinst
    assert '[ -f "$ENABLED_MARKER" ] || [ "$LEGACY_PRERM_BROKE_STATE" -eq 1 ]' in postinst
    assert '[ -f "$RUNNING_MARKER" ] || [ "$LEGACY_PRERM_BROKE_STATE" -eq 1 ]' in postinst
    assert '! systemctl is-active --quiet triforce.service' in postinst
    assert postinst.count("systemctl start triforce.service") == 1
    assert postinst.count("systemctl enable triforce.service") == 1
    assert "systemctl restart triforce.service" not in postinst


def test_only_known_broken_legacy_versions_force_source_state_restore():
    postinst = Path("debian/postinst").read_text()
    assert '2.86.0-1|1:2.86.0-1) LEGACY_PRERM_BROKE_STATE=1' in postinst
    assert 'LEGACY_PRERM_BROKE_STATE=0' in postinst
    assert '[ -f "$ENABLED_MARKER" ] || [ "$LEGACY_PRERM_BROKE_STATE" -eq 1 ]' in postinst
    assert '[ -f "$RUNNING_MARKER" ] || [ "$LEGACY_PRERM_BROKE_STATE" -eq 1 ]' in postinst
    assert "2.85.0~beta2" not in postinst
