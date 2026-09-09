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
