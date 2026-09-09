from pathlib import Path

HELPER = Path(__file__).parents[1] / "packaging" / "triforce-admin-helper.py"


def test_helper_has_fixed_service_name_and_no_shell_true():
    text = HELPER.read_text()
    assert 'SERVICE = "triforce.service"' in text
    assert "shell=True" not in text
    assert "subprocess.run(list(args)" in text


def test_helper_config_input_uses_stdin_not_secret_arguments():
    text = HELPER.read_text()
    assert "json.load(sys.stdin)" in text
    assert '"config-update"' in text


def test_helper_rejects_free_unit_or_path_arguments_by_parser_shape():
    text = HELPER.read_text()
    assert 'parser.add_argument("action", choices=[' in text
    assert 'parser.add_argument("--unit"' not in text
    assert 'parser.add_argument("--script"' not in text
    assert 'parser.add_argument("--path"' not in text
    assert '"--unit=triforce-setup-job"' in text

def test_helper_uses_packaged_isolated_runtime():
    assert HELPER.read_text().startswith("#!/opt/triforce/runtime/bin/python\n")

def test_config_directory_is_group_readable_by_service_user():
    text = HELPER.read_text()
    assert 'shutil.chown(ETC_DIR, user="root", group="triforce")' in text

def test_packaged_writable_crawler_paths_live_under_var_lib():
    text = HELPER.read_text()
    assert "CRAWLER_SPOOL_DIR=/var/lib/triforce/crawler_spool" in text
    assert "CRAWLER_TRAIN_DIR=/var/lib/triforce/crawler_spool/train" in text

def test_new_install_maps_legacy_tristar_into_var_lib_without_overwrite():
    text = HELPER.read_text()
    assert 'legacy_tristar = Path("/var/tristar")' in text
    assert 'legacy_tristar.exists()' in text
    assert 'legacy_tristar.symlink_to(STATE_DIR / "tristar"' in text

def test_fresh_config_sanitizes_template_secrets_and_generates_local_auth():
    text = HELPER.read_text()
    assert "if key in SECRET_ENV_KEYS" in text
    assert 'sanitized.append(f"{key}=")' in text
    assert "secrets.token_urlsafe(32)" in text
    assert '"JWT_SECRET": secrets.token_urlsafe(48)' in text
    assert '"TRIFORCE_ADMIN_SECRET": secrets.token_urlsafe(32)' in text
    assert '"MCP_OAUTH_PASS": secrets.token_urlsafe(32)' in text
    assert "seen_generated" in text
    assert "change-this-password" not in text


def test_setup_start_is_fixed_systemd_job_not_arbitrary_command():
    text = HELPER.read_text()
    assert 'SETUP_TASKS = frozenset({"runtime-init", "config-init", "service-install", "docker-install"})' in text
    assert '"/usr/bin/systemd-run", "--unit=triforce-setup-job"' in text
    assert 'str(SETUP_RUNNER), task' in text
    assert 'parser.add_argument("task", nargs="?", choices=sorted(SETUP_TASKS))' in text


def test_config_read_is_redacted_and_raw_update_restores_masked_secrets():
    text = HELPER.read_text()
    assert '"config-read"' in text
    assert '"config-raw-update"' in text
    assert "redact_dotenv_text" in text
    assert "restore_masked_secrets" in text
    assert "CONFIG.read_text" in text


def test_setup_cancel_targets_only_fixed_transient_unit():
    text = HELPER.read_text()
    assert '"setup-cancel"' in text
    assert 'run_fixed("/bin/systemctl", "stop", "triforce-setup-job.service")' in text
    assert 'parser.add_argument("--unit"' not in text


def test_docker_install_uses_only_fixed_distro_packages_and_no_group_mutation():
    text = HELPER.read_text()
    assert 'def docker_install()' in text
    assert 'packages.append("docker.io")' in text
    assert 'packages.append("docker-compose-v2")' in text
    assert '["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *packages]' in text
    assert 'run_fixed("/bin/systemctl", "enable", "--now", "docker.service")' in text
    assert 'usermod' not in text
    assert 'gpasswd' not in text
    assert 'groupadd' not in text
    assert 'docker compose up' not in text
