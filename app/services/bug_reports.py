"""Persistent, privacy-aware crash and self-test reporting for AILinux apps.

Apps submit bounded JSON over HTTPS.  SMTP credentials never leave TriForce.
Reports are stored in SQLite first; e-mail is a notification/archive channel.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import sqlite3
import ssl
import sys
import threading
import traceback
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

_DB_PATH = Path(os.getenv("BUG_REPORT_DB", str(Path.home() / ".local/share/triforce/bug-reports.sqlite3"))).expanduser()
_MAIL_TO = "bugs@ailinux.me"
_MAIL_FROM = "submit@ailinux.me"
_MAX_LOG_LINES = 250
_MAX_LOG_CHARS = 64_000
_MAX_STACK_CHARS = 48_000
_MAX_MESSAGE_CHARS = 8_000
_MAX_METADATA_CHARS = 32_000
_MAIL_DEDUPE_SECONDS = max(60, int(os.getenv("BUG_REPORT_MAIL_DEDUPE_SECONDS", "900")))
_MAX_MAILS_PER_HOUR = max(1, int(os.getenv("BUG_REPORT_MAX_MAILS_PER_HOUR", "120")))
_LOCK = threading.RLock()

_SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|resume[_-]?token|pair[_-]?code)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b[A-F0-9]{4}(?:-[A-F0-9]{4}){5}\b", re.I),
]
_QUERY_SECRET = re.compile(r"(?i)([?&](?:token|key|secret|password|code)=)[^&#\s]+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact_text(value: Any, limit: int) -> str:
    text = str(value or "")[:limit]
    for pattern in _SECRET_PATTERNS:
        replacement = r"\1[REDACTED]" if pattern.groups else "[REDACTED]"
        text = pattern.sub(replacement, text)
    text = _QUERY_SECRET.sub(r"\1[REDACTED]", text)
    return text


_SECRET_KEYS = {
    "authorization", "api_key", "apikey", "token", "access_token", "refresh_token",
    "secret", "password", "passwd", "resume_token", "workspace_token", "pair_code",
}


def _scrub_value(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:200]:
            name = str(key)[:128]
            normalized = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
            out[name] = "[REDACTED]" if normalized in _SECRET_KEYS else _scrub_value(item, depth + 1)
        return out
    if isinstance(value, list):
        return [_scrub_value(item, depth + 1) for item in value[:250]]
    if isinstance(value, tuple):
        return [_scrub_value(item, depth + 1) for item in value[:250]]
    if isinstance(value, str):
        return redact_text(value, 8_000)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(value, 2_000)


def _bounded_json(value: Any, limit: int = _MAX_METADATA_CHARS) -> str:
    try:
        raw = json.dumps(_scrub_value(value if value is not None else {}), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = json.dumps({"value": redact_text(value, 2_000)}, ensure_ascii=False)
    if len(raw) <= limit:
        return raw
    return json.dumps({"truncated": True, "preview": redact_text(raw, max(0, limit - 64))}, ensure_ascii=False)


def _fingerprint(app: str, event_type: str, exception_type: str, stack: str, selftest: Any) -> str:
    stable_stack = "\n".join(
        line.strip() for line in str(stack or "").splitlines()
        if line.strip() and not re.search(r"\bline\s+\d+\b", line, re.I)
    )[:12_000]
    if not stable_stack:
        stable_stack = _bounded_json(selftest, 8_000)
    material = "\0".join([app, event_type, exception_type, stable_stack])
    return hashlib.sha256(material.encode("utf-8", "replace")).hexdigest()[:24]


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bug_reports (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            app TEXT NOT NULL,
            repo TEXT NOT NULL DEFAULT '',
            version TEXT NOT NULL DEFAULT '',
            platform TEXT NOT NULL DEFAULT '',
            os_version TEXT NOT NULL DEFAULT '',
            arch TEXT NOT NULL DEFAULT '',
            channel TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL,
            delivery TEXT NOT NULL DEFAULT 'automatic',
            fingerprint TEXT NOT NULL,
            exception_type TEXT NOT NULL DEFAULT '',
            exception_message TEXT NOT NULL DEFAULT '',
            stack TEXT NOT NULL DEFAULT '',
            logs_json TEXT NOT NULL DEFAULT '[]',
            selftest_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            user_message TEXT NOT NULL DEFAULT '',
            install_id_hash TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'new',
            duplicate_count INTEGER NOT NULL DEFAULT 1,
            mail_status TEXT NOT NULL DEFAULT 'pending',
            mail_error TEXT NOT NULL DEFAULT '',
            mailed_at TEXT NOT NULL DEFAULT '',
            last_seen_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_bug_reports_created ON bug_reports(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_bug_reports_fingerprint ON bug_reports(fingerprint, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_bug_reports_status ON bug_reports(status, created_at DESC);
        """
    )
    return conn


def _sanitize_report(payload: dict[str, Any]) -> dict[str, Any]:
    app = redact_text(payload.get("app") or "unknown", 128).strip() or "unknown"
    event_type = str(payload.get("event_type") or "crash").lower().strip()
    if event_type not in {"crash", "selftest", "manual", "error"}:
        event_type = "error"
    delivery = str(payload.get("delivery") or "automatic").lower().strip()
    if delivery not in {"automatic", "manual", "selftest"}:
        delivery = "automatic"
    stack = redact_text(payload.get("stack"), _MAX_STACK_CHARS)
    exception_type = redact_text(payload.get("exception_type"), 256)
    raw_logs = payload.get("logs") if isinstance(payload.get("logs"), list) else []
    logs = [redact_text(item, 2_000) for item in raw_logs[-_MAX_LOG_LINES:]]
    selftest = payload.get("selftest") if isinstance(payload.get("selftest"), (dict, list)) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    install_id = str(payload.get("install_id") or "")[:256]
    install_hash = hashlib.sha256(install_id.encode()).hexdigest()[:20] if install_id else ""
    result = {
        "app": app,
        "repo": redact_text(payload.get("repo"), 128),
        "version": redact_text(payload.get("version"), 64),
        "platform": redact_text(payload.get("platform"), 64),
        "os_version": redact_text(payload.get("os_version"), 128),
        "arch": redact_text(payload.get("arch"), 64),
        "channel": redact_text(payload.get("channel"), 64),
        "event_type": event_type,
        "delivery": delivery,
        "exception_type": exception_type,
        "exception_message": redact_text(payload.get("exception_message"), 4_000),
        "stack": stack,
        "logs": logs,
        "selftest": json.loads(_bounded_json(selftest)),
        "metadata": json.loads(_bounded_json(metadata)),
        "user_message": redact_text(payload.get("user_message"), _MAX_MESSAGE_CHARS),
        "install_id_hash": install_hash,
    }
    result["fingerprint"] = _fingerprint(app, event_type, exception_type, stack, result["selftest"])
    return result


def _smtp_send(report: dict[str, Any], report_id: str, duplicate_count: int) -> None:
    host = os.getenv("BUG_SMTP_HOST") or os.getenv("MAIL_SMTP_HOST") or "127.0.0.1"
    port = int(os.getenv("BUG_SMTP_PORT") or os.getenv("MAIL_SMTP_PORT") or "587")
    user = os.getenv("BUG_SMTP_USER", "")
    password = os.getenv("BUG_SMTP_PASS", "")
    starttls = (os.getenv("BUG_SMTP_STARTTLS", "true").lower() not in {"0", "false", "no"})
    subject = f"[AILinux Bug] {report['app']} {report['event_type']} {report['fingerprint']}"
    body = {
        "report_id": report_id,
        "created_at": utc_now(),
        "fingerprint": report["fingerprint"],
        "duplicate_count": duplicate_count,
        **report,
    }
    msg = EmailMessage()
    msg["From"] = f"AILinux Crash Reporter <{_MAIL_FROM}>"
    msg["To"] = _MAIL_TO
    msg["Subject"] = subject
    msg["X-AILinux-Bug-ID"] = report_id
    msg["X-AILinux-Fingerprint"] = report["fingerprint"]
    msg.set_content(json.dumps(body, indent=2, ensure_ascii=False, sort_keys=True))

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.ehlo("api.ailinux.me")
        if starttls:
            smtp.starttls(context=context)
            smtp.ehlo("api.ailinux.me")
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)


def submit_report(payload: dict[str, Any]) -> dict[str, Any]:
    report = _sanitize_report(payload)
    now = utc_now()
    report_id = uuid.uuid4().hex
    should_mail = True
    duplicate_count = 1
    deduped = False
    mail_status = "pending"

    with _LOCK, _connect() as conn:
        recent = conn.execute(
            "SELECT id, created_at, duplicate_count, mailed_at FROM bug_reports WHERE fingerprint=? ORDER BY created_at DESC LIMIT 1",
            (report["fingerprint"],),
        ).fetchone()
        if recent and report["delivery"] != "manual":
            try:
                age = time.time() - datetime.fromisoformat(recent["created_at"].replace("Z", "+00:00")).timestamp()
            except Exception:
                age = _MAIL_DEDUPE_SECONDS + 1
            if age < _MAIL_DEDUPE_SECONDS:
                should_mail = False
                deduped = True
                mail_status = "deduped"
                duplicate_count = int(recent["duplicate_count"] or 1) + 1
        if should_mail:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
            sent_last_hour = int(conn.execute(
                "SELECT COUNT(*) FROM bug_reports WHERE mail_status='sent' AND mailed_at>=?", (cutoff,)
            ).fetchone()[0])
            if sent_last_hour >= _MAX_MAILS_PER_HOUR:
                should_mail = False
                mail_status = "rate_limited"
        conn.execute(
            """INSERT INTO bug_reports
            (id, created_at, app, repo, version, platform, os_version, arch, channel, event_type, delivery,
             fingerprint, exception_type, exception_message, stack, logs_json, selftest_json, metadata_json,
             user_message, install_id_hash, status, duplicate_count, mail_status, last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                report_id, now, report["app"], report["repo"], report["version"], report["platform"],
                report["os_version"], report["arch"], report["channel"], report["event_type"], report["delivery"],
                report["fingerprint"], report["exception_type"], report["exception_message"], report["stack"],
                json.dumps(report["logs"], ensure_ascii=False), json.dumps(report["selftest"], ensure_ascii=False),
                json.dumps(report["metadata"], ensure_ascii=False), report["user_message"], report["install_id_hash"],
                "new", duplicate_count, mail_status, now,
            ),
        )
        if recent and deduped:
            conn.execute("UPDATE bug_reports SET duplicate_count=?, last_seen_at=? WHERE id=?", (duplicate_count, now, recent["id"]))

    if should_mail:
        try:
            _smtp_send(report, report_id, duplicate_count)
            with _LOCK, _connect() as conn:
                conn.execute("UPDATE bug_reports SET mail_status='sent', mailed_at=?, mail_error='' WHERE id=?", (utc_now(), report_id))
        except Exception as exc:
            error = redact_text(exc, 500)
            with _LOCK, _connect() as conn:
                conn.execute("UPDATE bug_reports SET mail_status='failed', mail_error=? WHERE id=?", (error, report_id))

    return {"ok": True, "report_id": report_id, "fingerprint": report["fingerprint"], "mail_queued": should_mail}


_PROCESS_REPORTING_INSTALLED = False
_ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook
_ORIGINAL_THREAD_EXCEPTHOOK = getattr(threading, "excepthook", None)


def startup_selftest() -> dict[str, Any]:
    checks: dict[str, Any] = {"sqlite": False, "mail_sender": _MAIL_FROM, "mail_recipient": _MAIL_TO}
    try:
        with _LOCK, _connect() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["sqlite"] = True
        return {"ok": True, "checks": checks}
    except Exception as exc:
        checks["sqlite_error"] = redact_text(exc, 500)
        # The database is the normal source of truth. If it is unavailable,
        # make one best-effort direct mail notification rather than recursing
        # through submit_report(), which would hit the same broken DB again.
        report = _sanitize_report({
            "app": "TriForce Bug Reporter",
            "repo": "triforce",
            "version": os.getenv("TRIFORCE_VERSION", "unknown"),
            "platform": sys.platform,
            "event_type": "selftest",
            "delivery": "selftest",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "selftest": checks,
        })
        try:
            _smtp_send(report, "startup-selftest-" + uuid.uuid4().hex[:12], 1)
        except Exception:
            pass
        return {"ok": False, "checks": checks}


def _report_uncaught(error: BaseException, origin: str) -> None:
    try:
        submit_report({
            "app": "TriForce Backend",
            "repo": "triforce",
            "version": os.getenv("TRIFORCE_VERSION", "unknown"),
            "platform": sys.platform,
            "event_type": "crash",
            "delivery": "automatic",
            "exception_type": type(error).__name__,
            "exception_message": str(error),
            "stack": "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            "metadata": {"origin": origin},
        })
    except Exception:
        pass


def install_process_reporting() -> dict[str, Any]:
    global _PROCESS_REPORTING_INSTALLED
    if not _PROCESS_REPORTING_INSTALLED:
        def _sys_hook(exc_type, exc, tb):
            if exc is not None:
                _report_uncaught(exc, "sys.excepthook")
            _ORIGINAL_SYS_EXCEPTHOOK(exc_type, exc, tb)
        sys.excepthook = _sys_hook

        if _ORIGINAL_THREAD_EXCEPTHOOK is not None:
            def _thread_hook(args):
                if args.exc_value is not None:
                    _report_uncaught(args.exc_value, f"thread:{getattr(args.thread, 'name', '')}")
                _ORIGINAL_THREAD_EXCEPTHOOK(args)
            threading.excepthook = _thread_hook
        _PROCESS_REPORTING_INSTALLED = True
    return startup_selftest()

def list_reports(*, limit: int = 50, status: str = "", app: str = "", fingerprint: str = "") -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 200))
    clauses, values = [], []
    if status:
        clauses.append("status=?"); values.append(status)
    if app:
        clauses.append("app LIKE ?"); values.append(f"%{app[:128]}%")
    if fingerprint:
        clauses.append("fingerprint=?"); values.append(fingerprint[:64])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            "SELECT id,created_at,app,repo,version,platform,event_type,delivery,fingerprint,exception_type,exception_message,user_message,status,duplicate_count,mail_status,last_seen_at FROM bug_reports"
            + where + " ORDER BY created_at DESC LIMIT ?", (*values, limit)
        ).fetchall()
    return [dict(row) for row in rows]


def get_report(report_id: str) -> dict[str, Any] | None:
    with _LOCK, _connect() as conn:
        row = conn.execute("SELECT * FROM bug_reports WHERE id=?", (report_id,)).fetchone()
    if not row:
        return None
    out = dict(row)
    for key in ("logs_json", "selftest_json", "metadata_json"):
        try:
            out[key[:-5]] = json.loads(out.pop(key))
        except Exception:
            out[key[:-5]] = None
    return out


def update_status(report_id: str, status: str) -> dict[str, Any]:
    if status not in {"new", "triaged", "resolved", "ignored"}:
        raise ValueError("status must be new, triaged, resolved or ignored")
    with _LOCK, _connect() as conn:
        cur = conn.execute("UPDATE bug_reports SET status=? WHERE id=?", (status, report_id))
    return {"ok": bool(cur.rowcount), "report_id": report_id, "status": status}


def stats() -> dict[str, Any]:
    with _LOCK, _connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM bug_reports").fetchone()[0]
        by_status = {r[0]: r[1] for r in conn.execute("SELECT status,COUNT(*) FROM bug_reports GROUP BY status")}
        by_app = {r[0]: r[1] for r in conn.execute("SELECT app,COUNT(*) FROM bug_reports GROUP BY app ORDER BY COUNT(*) DESC LIMIT 30")}
        top = [dict(r) for r in conn.execute("SELECT fingerprint,app,event_type,COUNT(*) AS reports,MAX(last_seen_at) AS last_seen FROM bug_reports GROUP BY fingerprint,app,event_type ORDER BY reports DESC LIMIT 20")]
    return {"total": total, "by_status": by_status, "by_app": by_app, "top_fingerprints": top, "db": str(_DB_PATH)}
