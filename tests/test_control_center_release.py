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
