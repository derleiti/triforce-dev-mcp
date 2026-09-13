#!/usr/bin/python3
"""Minimal independent recovery webhook for TriForce source-mode hosts.

This process intentionally does not import TriForce. It stays available when the
backend or its Python environment is broken. It accepts exactly one authenticated
operation: POST /recover -> start triforce-source-recovery.service.
"""
from __future__ import annotations

import hmac
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TOKEN_FILE = Path(os.environ.get("TRIFORCE_RECOVERY_TOKEN_FILE", "/etc/triforce/recovery-token"))
def _listen_host() -> str:
    explicit = os.environ.get("TRIFORCE_RECOVERY_LISTEN", "").strip()
    if explicit:
        if explicit == "0.0.0.0":
            raise RuntimeError("wildcard recovery bind is forbidden")
        return explicit
    proc = subprocess.run(
        ["/usr/sbin/ip", "-4", "-o", "addr", "show", "dev", "wg0"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("wg0 has no IPv4 address")
    cidr = proc.stdout.split()[3]
    host = cidr.split("/", 1)[0]
    if not host.startswith("10.10.0."):
        raise RuntimeError("refusing unexpected recovery bind address")
    return host


PORT = int(os.environ.get("TRIFORCE_RECOVERY_PORT", "9191"))
MAX_BODY = 4096


def _token() -> str:
    value = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(value) < 32:
        raise RuntimeError("recovery token is missing or too short")
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = "TriForceRecovery/1"

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log request headers, query strings or request bodies.
        print("[triforce-recovery-http] " + fmt % args, flush=True)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "triforce-recovery"})
        else:
            self._json(404, {"ok": False})

    def do_POST(self) -> None:
        if self.path != "/recover":
            self._json(404, {"ok": False})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length < 0 or length > MAX_BODY:
            self._json(413, {"ok": False, "error": "request_too_large"})
            return
        if length:
            self.rfile.read(length)
        auth = self.headers.get("Authorization", "")
        provided = auth[7:] if auth.startswith("Bearer ") else ""
        try:
            expected = _token()
        except Exception:
            self._json(503, {"ok": False, "error": "recovery_not_configured"})
            return
        if not provided or not hmac.compare_digest(provided, expected):
            self._json(401, {"ok": False, "error": "unauthorized"})
            return
        proc = subprocess.run(
            ["/usr/bin/systemctl", "start", "triforce-source-recovery.service"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
        )
        if proc.returncode != 0:
            self._json(500, {"ok": False, "error": "recovery_failed"})
            return
        self._json(202, {"ok": True, "status": "recovery_started"})


if __name__ == "__main__":
    HTTPServer((_listen_host(), PORT), Handler).serve_forever()
