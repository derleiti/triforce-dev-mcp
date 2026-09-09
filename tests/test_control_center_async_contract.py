from pathlib import Path

GUI = Path(__file__).parents[1] / "control_center" / "main.py"


def test_admin_gui_paths_do_not_use_blocking_subprocess_run():
    text = GUI.read_text()
    services = text.split("class ServicesPage", 1)[1].split("class SetupPage", 1)[0]
    settings = text.split("class SettingsPage", 1)[1].split("class ServicesPage", 1)[0]
    assert "subprocess.run(" not in services
    assert "subprocess.run(" not in settings
    assert "QProcess" in services
    assert "QProcess" in settings


def test_setup_gui_reconnects_to_persistent_job_status():
    text = GUI.read_text()
    setup = text.split("class SetupPage", 1)[1].split("class LogsPage", 1)[0]
    assert "read_setup_job()" in setup
    assert '"setup-start", task_id' in setup
    assert "job_timer" in setup


def test_readonly_diagnostics_and_service_status_use_workers():
    text = GUI.read_text()
    logs = text.split("class LogsPage", 1)[1].split("class MainWindow", 1)[0]
    services = text.split("class ServicesPage", 1)[1].split("class SetupPage", 1)[0]
    assert "self._start(recent_logs" in logs
    assert "Worker(service_info)" in services
    assert "subprocess.run(" not in logs


def test_protected_settings_use_redacted_policykit_read_and_raw_update():
    text = GUI.read_text()
    settings = text.split("class SettingsPage", 1)[1].split("class ServicesPage", 1)[0]
    assert '"config-read"' in settings
    assert '"config-raw-update"' in settings
    assert "Rohmodus" in settings
    assert "restore_masked_secrets" in settings


def test_setup_cancel_uses_async_qprocess_and_fixed_helper_action():
    text = GUI.read_text()
    assert 'proc.setArguments([str(ADMIN_HELPER), "setup-cancel"])' in text
    assert 'def cancel_job(self):' in text


def test_setup_page_has_explicit_docker_install_button():
    text = GUI.read_text()
    assert 'task.task_id == "docker-install"' in text
    assert 'QPushButton("Docker installieren")' in text
    assert '"setup-start", task_id' in text
