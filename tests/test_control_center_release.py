from pathlib import Path

SCRIPT = Path('scripts/release/build-control-center-2.86.sh')


def test_control_center_release_targets_286_and_current_backend_floor():
    text = SCRIPT.read_text()
    assert 'CONTROL_REVISION="${CONTROL_REVISION:-2}"' in text
    assert 'BACKEND_MIN="${BACKEND_MIN:-1:${VERSION}-9}"' in text
    assert 'triforce-backend (>= $BACKEND_MIN)' in text
    assert 'triforce-backend (<< 1:3.0)' in text


def test_control_center_release_builds_and_smokes_nuitka_bundle():
    text = SCRIPT.read_text()
    assert "--mode=standalone" in text
    assert "--enable-plugin=pyqt6" in text
    assert 'QT_QPA_PLATFORM=offscreen "$GUI/triforce-control-center" --smoke-test' in text


def test_control_center_release_is_package_hygiene_aware():
    text = SCRIPT.read_text()
    assert "potential secret material" in text
    assert 'touch -h -d "@$SOURCE_DATE_EPOCH"' in text
    assert "dpkg-deb --root-owner-group --build" in text
    assert "dir-or-file-in-opt" in text


def test_control_center_release_scrubs_host_config_environment():
    text = SCRIPT.read_text()
    assert text.count('env -u TRIFORCE_CONFIG_FILE -u TRIFORCE_PROJECT_ROOT') >= 3
    assert 'triforce-control-center: embedded-library' in text
    assert 'triforce-control-center: unstripped-binary-or-object' in text


def test_gui_import_graph_ships_no_absolute_developer_paths():
    """Nuitka bakes string constants in, so a hardcoded home leaks into the DEB.

    Scrubbing the build environment cannot help here: the path was a literal in
    the source, not an environment value.
    """
    import re

    modules = (
        'app/config.py', 'app/paths.py', 'app/settings_store.py', 'app/setup_tasks.py',
        'app/setup_job.py', 'app/legacy_settings.py', 'control_center/main.py',
    )
    offenders = [
        module for module in modules
        if re.search(r'/home/[A-Za-z][A-Za-z0-9._-]*/', Path(module).read_text(encoding='utf-8'))
    ]
    assert offenders == []


def test_control_center_builder_exports_reproducible_timestamp():
    text = SCRIPT.read_text()
    assert 'export SOURCE_DATE_EPOCH' in text


def test_control_center_build_does_not_follow_backend_entrypoint():
    """app/__init__.py lazily imports app.main, and Nuitka follows it statically.

    Without this flag the GUI binary compiles the whole backend graph (routes,
    MCP, services) even though the Control Center only needs settings, setup
    tasks and the backend HTTP/systemd surface.
    """
    text = SCRIPT.read_text()
    assert '--nofollow-import-to=app.main' in text
