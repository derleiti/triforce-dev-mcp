"""Installer regression tests, isolated from production services and secrets."""
from pathlib import Path
import subprocess

REPO = Path(__file__).resolve().parents[1]
INSTALLER = REPO / 'install.sh'


def invoke(tmp_path, body):
    script = f'''set -Eeuo pipefail
export TRIFORCE_INSTALL_LIB_ONLY=1
source "{INSTALLER}"
run_sudo() {{ "$@"; }}
ROOT_DIR="{tmp_path}"
ENV_FILE="$ROOT_DIR/.env"
ENV_EXAMPLE_FILE="$ROOT_DIR/.env.example"
TRIFORCE_ENV_TEMPLATE="$ROOT_DIR/triforce.env.template"
TRIFORCE_ENV_FILE="$ROOT_DIR/triforce.env"
{body}
'''
    p = subprocess.run(['bash', '-c', script], cwd=tmp_path, capture_output=True, text=True, timeout=12)
    assert p.returncode == 0, p.stderr + p.stdout


def test_rerun_preserves_existing_config_and_permissions(tmp_path):
    (tmp_path / '.env.example').write_text('LOG_LEVEL=INFO\n')
    (tmp_path / 'triforce.env.template').write_text('SERVER_HOST=127.0.0.1\n')
    cfg = tmp_path / 'triforce.env'
    cfg.write_text('FEDERATION_SECRET=not-a-real-secret\nFEDERATION_TOKEN=fake\nNODE_ID=zombie-pc\nSERVER_HOST=10.10.0.2\nSERVER_PORT=9100\nDOMAIN=existing.example\n')
    invoke(tmp_path, '''ensure_env_files; ensure_env_files
[[ "$API_HOST" == 10.10.0.2 ]]
[[ "$API_PORT" == 9100 ]]
[[ "$(stat -c %a "$TRIFORCE_ENV_FILE")" == 600 ]]
''')
    s=cfg.read_text()
    assert s.count('FEDERATION_SECRET=') == 1
    assert 'FEDERATION_TOKEN=fake' in s
    assert 'DOMAIN=existing.example' in s
    assert 'SERVER_HOST=10.10.0.2' in s


def test_explicit_port_update_keeps_credentials(tmp_path):
    (tmp_path / '.env.example').write_text('LOG_LEVEL=INFO\n')
    cfg=tmp_path/'triforce.env'
    cfg.write_text('FEDERATION_SECRET=not-a-real-secret\nSERVER_PORT=9100\n')
    invoke(tmp_path, '''PORT_EXPLICIT=true; API_PORT=9911; ensure_env_files''')
    assert 'SERVER_PORT=9911' in cfg.read_text()
    assert 'FEDERATION_SECRET=not-a-real-secret' in cfg.read_text()


def test_renders_requested_workspace(tmp_path):
    invoke(tmp_path, f'''RUN_USER=zombie
render_unit "{REPO / 'scripts/systemd/triforce.service'}" "$ROOT_DIR/rendered.service"
grep -F "WorkingDirectory=$ROOT_DIR" "$ROOT_DIR/rendered.service"
grep -F "ExecStart=$ROOT_DIR/scripts/start-triforce.sh" "$ROOT_DIR/rendered.service"
''')


def test_cli_validation():
    for args in (['--port'], ['--health-timeout'], ['--port','invalid'], ['--enable-federation-service']):
        p=subprocess.run(['bash',str(INSTALLER),*args,'--non-interactive'],capture_output=True,text=True,timeout=8)
        assert p.returncode != 0, args
