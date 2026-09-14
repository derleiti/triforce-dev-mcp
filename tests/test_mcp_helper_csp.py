from pathlib import Path


def test_api_csp_allows_only_pinned_browser_python_runtime_requirements():
    root = Path(__file__).resolve().parents[1]
    conf = (root / "docker/wordpress/apache/vhosts/vhost-api.ailinux.me.conf").read_text()
    csp = next(line for line in conf.splitlines() if "Header always set Content-Security-Policy" in line)
    assert "script-src 'self' 'unsafe-inline' 'wasm-unsafe-eval' https://cdn.jsdelivr.net" in csp
    assert "worker-src 'self' blob:" in csp
    assert "connect-src 'self' wss://api.ailinux.me https://cdn.jsdelivr.net" in csp
    assert "'unsafe-eval'" not in csp.replace("'wasm-unsafe-eval'", "")
