from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_packaged_service_uses_isolated_runtime_and_var_log():
    text = (ROOT / "packaging/systemd/triforce.service").read_text()
    assert "TRIFORCE_PYTHON=/opt/triforce/runtime/bin/python" in text
    assert "TRIFORCE_LOG_DIR=/var/log/triforce" in text
    assert "ExecStart=/opt/triforce/scripts/start-triforce.sh" in text


def test_redis_is_optional_not_recommended_install():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    assert "Suggests: redis-server" in text
    assert "Recommends: redis-server" not in text


def test_no_online_pip_in_postinst_template():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    postinst = text.split('cat > "$pkg/DEBIAN/postinst"', 1)[1].split("EOF", 2)[1]
    assert "pip install" not in postinst

def test_fresh_package_disables_optional_public_mcp_ws_listener():
    helper = (ROOT / "packaging/triforce-admin-helper.py").read_text()
    assert "MCP_WS_ENABLED=false" in helper


def test_control_package_declares_native_qt_platform_dependencies_and_prunes_unused_plugins():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    for package in ("libgl1", "libx11-6", "libxcb-cursor0", "libxkbcommon-x11-0", "libwayland-client0"):
        assert package in text
    assert "plugins/egldeviceintegrations" in text
    assert "plugins/platformthemes" in text
    assert "plugins/printsupport" in text


def test_start_wrapper_treats_administrative_sigterm_as_clean_shutdown():
    text = (ROOT / "scripts/start-triforce.sh").read_text()
    assert "trap on_signal TERM INT" in text
    assert "wait \"$MAIN_PID\"" in text
    assert "exit 0" in text


def test_remove_disables_service_and_only_cleans_generated_program_cache():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    assert "/bin/systemctl disable triforce.service" in text
    assert "find /opt/triforce -type f -name '*.pyc' -delete" in text
    assert "find /opt/triforce -depth -type d -empty -delete" in text
    assert "rm -rf /opt/triforce" not in text


def test_release_builder_normalizes_python_interpreter():
    text = (ROOT / "scripts/release/build-triforce-2.85-beta1.sh").read_text()
    assert 'ln -s /usr/bin/python3.14 "$pkg/opt/triforce/runtime/bin/python3.14"' in text
    assert 'Depends: python3.14, systemd' in text


def test_ci_prepare_script_builds_runtime_and_gui():
    text = (ROOT / "scripts/release/prepare-triforce-2.85-ci-build.sh").read_text()
    assert 'requirements-lock-2.85-beta1.txt' in text
    assert 'nuitka==4.2.1' in text
    assert '--smoke-test' in text
    assert '--mode=standalone' in text


def test_arch_pkgbuild_contract():
    pkg = (ROOT / "packaging/arch/PKGBUILD.in").read_text()
    install = (ROOT / "packaging/arch/triforce.install").read_text()
    render = (ROOT / "scripts/release/render-arch-pkgbuild.sh").read_text()
    assert "pkgname=triforce-bin" in pkg
    assert "python>=3.14" in pkg and "python<3.15" in pkg
    assert "ln -s /usr/bin/python" in pkg
    assert "runtime-init" in install and "config-init" in install
    assert "@SOURCE@" in pkg and "@SHA256@" in pkg
    assert "sha256sum" in render


def test_release_actions_contract():
    text = (ROOT / ".github/workflows/release-2.85-beta.yml").read_text()
    for job in ("test:", "linux:", "arch:", "windows:", "release:"):
        assert job in text
    assert "makepkg --noconfirm --cleanbuild" in text
    assert "DEBIAN_RELEASE_SMOKE_OK" in text
    assert "ARCH_PACKAGE_SMOKE_OK" in text
    assert "--prerelease" in text
    assert "aiwindows-client/.github/workflows/build-windows.yml@70ad034" in text
