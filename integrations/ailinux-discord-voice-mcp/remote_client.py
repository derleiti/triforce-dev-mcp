"""Narrow Discord webhook to voice socket RPC. JSON request via STDIN, result via STDOUT."""
from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

SOCKET = Path.home() / ".local/state/ailinux-nova-voice/control.sock"


def main() -> None:
    raw = sys.stdin.buffer.read(4096)
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("request must be JSON object")
    operation = request.get("op")
    if operation not in {"status", "join_user", "say", "leave", "listen"}:
        raise ValueError("invalid operation")
    if operation == "join_user":
        uid = request.get("user_id", "")
        if not isinstance(uid, str) or not uid.isdigit() or not 17 <= len(uid) <= 20:
            raise ValueError("invalid user_id")
        request = {"op": "join_user", "user_id": uid}
    elif operation == "say":
        text = request.get("text", "")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 400:
            raise ValueError("invalid text length")
        request = {"op": "say", "text": text.strip()}
    elif operation == "listen":
        enabled = request.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("invalid listening state")
        request = {"op": "listen", "enabled": enabled}
    else:
        request = {"op": operation}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(50)
        connection.connect(str(SOCKET))
        connection.sendall((json.dumps(request, ensure_ascii=False) + "\n").encode("utf8"))
        data = bytearray()
        while b"\n" not in data:
            block = connection.recv(8192)
            if not block:
                break
            data += block
            if len(data) > 8192:
                raise ValueError("voice socket response oversized")
    if not data:
        raise RuntimeError("voice socket returned no data")
    result = json.loads(data.split(b"\n", 1)[0])
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        sys.stdout.write(json.dumps({"ok": False, "error": str(error)[:190]}) + "\n")
        sys.exit(1)
