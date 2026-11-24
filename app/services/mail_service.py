"""Nova Mail Service — IMAP read + SMTP send via nova@ailinux.me

Provides:
  - IMAP inbox listing, reading, mark-seen
  - SMTP sending via local mailserver (port 587 STARTTLS)
  - MCP tool handlers: mail_inbox, mail_read, mail_send, mail_mark_seen
"""
from __future__ import annotations

import os
import email
import imaplib
import smtplib
import ssl
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.utils.errors import api_error


# ─── IMAP helpers ──────────────────────────────────────────────────────────────

def _imap_connect() -> imaplib.IMAP4_SSL | imaplib.IMAP4:
    s = get_settings()
    host = s.mail_imap_host
    port = s.mail_imap_port or 993
    user = s.mail_imap_user
    password = s.mail_imap_pass

    if not all([host, user, password]):
        raise api_error(
            "IMAP credentials not configured (MAIL_IMAP_HOST/USER/PASS)",
            status_code=503,
            code="imap_unconfigured",
        )

    use_ssl = s.mail_imap_ssl if s.mail_imap_ssl is not None else True
    if use_ssl:
        # FIX: kein Cert-Check fuer internen Mailserver (self-signed / IP)
        import ssl as _ssl
        _ctx = _ssl.create_default_context()
        _ctx.check_hostname = False
        _ctx.verify_mode = _ssl.CERT_NONE
        conn = imaplib.IMAP4_SSL(host, port, ssl_context=_ctx)
    else:
        conn = imaplib.IMAP4(host, port)

    conn.login(user, password)
    return conn


def _decode_header(val: str) -> str:
    """Decode RFC 2047 encoded header value."""
    from email.header import decode_header as _dh
    parts = _dh(val or "")
    result = []
    for part, enc in parts:
        if isinstance(part, bytes):
            result.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            result.append(part)
    return " ".join(result)


def _extract_body(msg: email.message.Message) -> str:
    """Extract plain-text body from MIME message."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not part.get("Content-Disposition"):
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


# ─── Public API ────────────────────────────────────────────────────────────────

def mail_inbox(limit: int = 20, folder: str = "INBOX") -> List[Dict[str, Any]]:
    """List recent messages from the inbox. Returns list of message summaries.
    Uses UID-based IMAP operations for stable message references across sessions.
    """
    conn = _imap_connect()
    try:
        conn.select(folder, readonly=True)
        _, validity = conn.response("UIDVALIDITY")
        uidvalidity = (validity[0] or b"").decode() if validity else ""
        # B-01 FIX: use uid('SEARCH') to get stable UIDs (not MSNs which shift on expunge)
        _, data = conn.uid("SEARCH", None, "ALL")
        uids = data[0].split()
        recent = uids[-limit:] if len(uids) > limit else uids
        recent.reverse()  # newest first

        messages = []
        for uid in recent:
            _, raw = conn.uid("FETCH", uid, "(BODY.PEEK[HEADER] FLAGS)")
            if not raw or not raw[0]:
                continue
            if not isinstance(raw[0], tuple):
                continue
            meta_str = raw[0][0]
            header_data = raw[0][1]
            msg = email.message_from_bytes(header_data)
            seen = b"\\Seen" in meta_str

            messages.append({
                "uid": uid.decode(),
                "uidvalidity": uidvalidity,
                "subject": _decode_header(msg.get("Subject", "(kein Betreff)")),
                "from": _decode_header(msg.get("From", "")),
                "date": msg.get("Date", ""),
                "seen": seen,
            })
        return messages
    finally:
        conn.logout()


def mail_read(uid: str, folder: str = "INBOX", *, mark_seen: bool = True) -> Dict[str, Any]:
    """Fetch full message by UID. Returns headers + body."""
    conn = _imap_connect()
    try:
        conn.select(folder, readonly=not mark_seen)
        # B-01 FIX: use uid('FETCH') with stable UIDs
        status, raw = conn.uid("FETCH", uid.encode(), "(RFC822)" if mark_seen else "(BODY.PEEK[])")
        if status != "OK" or not raw or not isinstance(raw[0], tuple):
            raise api_error(f"Message UID {uid} not found", status_code=404, code="mail_not_found")

        msg_bytes = raw[0][1] if isinstance(raw[0], tuple) else raw[0]
        msg = email.message_from_bytes(msg_bytes)
        body = _extract_body(msg)

        return {
            "uid": uid,
            "subject": _decode_header(msg.get("Subject", "")),
            "from": _decode_header(msg.get("From", "")),
            "to": _decode_header(msg.get("To", "")),
            "reply_to": _decode_header(msg.get("Reply-To", "")),
            "message_id": (msg.get("Message-ID", "") or "").strip(),
            "references": (msg.get("References", "") or "").strip(),
            "date": msg.get("Date", ""),
            "body": body[:4000],  # Limit to 4000 chars for MCP safety
            "truncated": len(body) > 4000,
        }
    finally:
        conn.logout()


def mail_mark_seen(uid: str, folder: str = "INBOX") -> Dict[str, Any]:
    """Mark a message as read."""
    conn = _imap_connect()
    try:
        conn.select(folder)
        # B-01 FIX: use uid('STORE') with stable UIDs
        conn.uid("STORE", uid.encode(), "+FLAGS", "\\Seen")
        return {"ok": True, "uid": uid, "action": "marked_seen"}
    finally:
        conn.logout()


def mail_send(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    reply_to: Optional[str] = None,
    in_reply_to: Optional[str] = None,
    references: Optional[str] = None,
) -> Dict[str, Any]:
    """Send an email via SMTP. Uses nova@ailinux.me as sender."""
    s = get_settings()
    host = s.mail_smtp_host
    port = s.mail_smtp_port or 587
    user = s.mail_smtp_user
    password = s.mail_smtp_pass
    from_name = s.mail_from_name or "Nova AI"
    from_addr = s.mail_from_addr or "nova@ailinux.me"

    # FIX S16-SMTP-RELAY: Port 25 trusted relay braucht nur host, kein user/pass
    # user/pass nur nötig bei AUTH (Port 587) — docker-mailserver hat smtpd_sasl_auth_enable=no
    if not host:
        raise api_error(
            "SMTP not configured (MAIL_SMTP_HOST missing)",
            status_code=503,
            code="smtp_unconfigured",
        )

    import uuid as _uuid
    from email.utils import formatdate as _fmtdate
    msg = EmailMessage()
    msg["Message-ID"] = f"<{_uuid.uuid4()}@ailinux.me>"
    msg["Date"] = _fmtdate(localtime=True)
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_addr}>"
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references
    msg.set_content(body)

    use_starttls = s.mail_smtp_starttls if s.mail_smtp_starttls is not None else True
    # FIX S16-SSL: Localhost mailserver hat kein gültiges Zertifikat für 127.0.0.1
    # → check_hostname + verify_mode deaktivieren für interne SMTP-Verbindung
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    ehlo_name = getattr(s, "mail_hostname", None) or os.environ.get("MAIL_HOSTNAME", "mail.ailinux.me")
    with smtplib.SMTP(host, port, timeout=8) as server:
        server.ehlo(ehlo_name)
        if use_starttls:
            server.starttls(context=context)
            server.ehlo(ehlo_name)
        if user and password:
            try:
                server.login(user, password)
            except smtplib.SMTPNotSupportedError:
                # Local trusted relay may not advertise AUTH.
                # Continue without login when SMTP AUTH is unavailable.
                pass
        server.send_message(msg)

    return {"ok": True, "to": to, "subject": subject, "from": from_addr}


# ─── Singleton ────────────────────────────────────────────────────────────────

class MailService:
    """Thin wrapper exposing mail functions for dependency injection / MCP tools."""

    def inbox(self, limit: int = 20, folder: str = "INBOX") -> List[Dict]:
        return mail_inbox(limit=limit, folder=folder)

    def read(self, uid: str, folder: str = "INBOX") -> Dict:
        return mail_read(uid=uid, folder=folder)

    def mark_seen(self, uid: str, folder: str = "INBOX") -> Dict:
        return mail_mark_seen(uid=uid, folder=folder)

    def send(self, to: str, subject: str, body: str, **kwargs) -> Dict:
        return mail_send(to=to, subject=subject, body=body, **kwargs)


mail_service = MailService()
