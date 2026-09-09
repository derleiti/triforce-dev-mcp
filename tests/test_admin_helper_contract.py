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
    assert "--unit" not in text
    assert "--script" not in text
    assert "--path" not in text

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
    assert "JWT_SECRET=" in text
    assert "TRIFORCE_ADMIN_SECRET=" in text
    assert "MCP_OAUTH_PASS=" in text
    assert "change-this-password" not in text
