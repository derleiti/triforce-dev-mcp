from pathlib import Path


def test_api_csp_has_strict_privileged_mcp_override_without_third_party_origins():
    root = Path(__file__).resolve().parents[1]
    conf = (root / "docker/wordpress/apache/vhosts/vhost-api.ailinux.me.conf").read_text()
    assert "cdn.jsdelivr.net" not in conf
    assert "static.cloudflareinsights.com" not in conf
    marker = '<LocationMatch "^/v1/mcp(?:/|$)">'
    assert marker in conf
    scoped = conf.split(marker, 1)[1].split("</LocationMatch>", 1)[0]
    csp = next(line for line in scoped.splitlines() if "Header always set Content-Security-Policy" in line)
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp
    assert "worker-src 'self'" in csp
    assert "style-src 'self'" in csp
    assert "connect-src 'self' wss://api.ailinux.me" in csp
    assert "'unsafe-inline'" not in csp
    assert "blob:" not in csp
    assert "'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")
