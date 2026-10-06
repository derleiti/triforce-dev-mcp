#!/usr/bin/env python3
"""
Nova Flarum Bot — Polling Daemon
Pollt forum.ailinux.me auf neue Posts und antwortet als ailinux-nova-ai.

Trigger:
  - Alle neuen Posts (in konfigurierbaren Tags)
  - Immer wenn @nova erwähnt wird
"""

import os
import sys
import time
import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
import subprocess as _sp
def _flarum_ip():
    try:
        r = _sp.check_output(["docker","inspect","flarum","--format",
            "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
            stderr=_sp.DEVNULL).decode().split()[0]
        return r
    except Exception:
        return "172.19.0.4"
# FIX 2026-07-11: Bot laeuft auf dem HOST, nicht im Docker-Netz -> Container-IP:8888
# ist von hier nicht routbar. Flarum ist auf Host-Port 9080 gemappt (9080:8888).
# ENV FLARUM_API hat Vorrang, Default = Host-Port. _flarum_ip() bleibt als Fallback.
FLARUM_API = os.environ.get("FLARUM_API", "http://127.0.0.1:9080/api")
FLARUM_TOKEN = os.environ.get("FLARUM_TOKEN")
if not FLARUM_TOKEN:
    raise RuntimeError("FLARUM_TOKEN is not set. Use environment/vault; do not hardcode secrets.")
# Bot kann je nach Flarum-Token als Nova-User oder Admin posten.
# Aktuell beobachtet: admin/zombie=user_id 1, nova-ai=user_id 2, 4=Legacy-ID.
# Wichtig: Alle eigenen Bot-/Admin-Identitäten ignorieren, sonst Self-Reply-Loop.
NOVA_USER_IDS   = {int(x) for x in os.environ.get("NOVA_FLARUM_OWN_USER_IDS", "1,2,4").split(",") if x.strip().isdigit()}

TRIFORCE_URL    = "http://127.0.0.1:9000/v1/chat"
TRIFORCE_USER   = "admin@ailinux.me"
TRIFORCE_PASS = os.environ.get("TRIFORCE_PASS")
if not TRIFORCE_PASS:
    raise RuntimeError("TRIFORCE_PASS is not set. Use environment/vault; do not hardcode secrets.")

POLL_INTERVAL   = 30          # Sekunden zwischen Polls
STATE_FILE      = "/var/lib/nova-flarum-bot/state.json"
MAX_POST_AGE    = 300         # Posts älter als 5 Min beim Start ignorieren (kein Spam beim Neustart)

# Tags in denen Nova IMMER antwortet (leer = überall)
ACTIVE_TAGS     = []          # z.B. ["support", "ai"] — leer = alle Tags

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("nova-bot")

# ── State ─────────────────────────────────────────────────────────────────────
def load_state():
    try:
        return json.loads(Path(STATE_FILE).read_text())
    except Exception:
        return {"last_post_id": 0, "processed": []}

def save_state(state):
    Path(STATE_FILE).parent.mkdir(parents=True, exist_ok=True)
    Path(STATE_FILE).write_text(json.dumps(state, indent=2))

# ── Flarum API ────────────────────────────────────────────────────────────────
def flarum_get(path, params=None):
    headers = {"Authorization": f"Token {FLARUM_TOKEN}"}
    r = requests.get(f"{FLARUM_API}{path}", headers=headers, params=params, timeout=15)
    r.raise_for_status()
    return r.json()

def wait_for_flarum_ready(timeout=90, interval=5):
    """Wait for the local Flarum API during boot without logging a false hard error."""
    deadline = time.monotonic() + timeout
    attempts = 0
    while True:
        attempts += 1
        try:
            flarum_get("/posts", params={"page[limit]": 1})
            if attempts > 1:
                log.info(f"Flarum API bereit nach {attempts} Versuchen")
            return True
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status in {401, 403}:
                raise RuntimeError(f"Flarum API authentication failed with HTTP {status}") from exc
            last_error = exc
        except requests.RequestException as exc:
            last_error = exc
        if time.monotonic() >= deadline:
            log.warning(f"Flarum API beim Start noch nicht bereit: {last_error}")
            return False
        if attempts == 1:
            log.info("Flarum API beim Start noch nicht bereit; warte auf Readiness")
        time.sleep(interval)


def flarum_post(discussion_id, content):
    headers = {
        "Authorization": f"Token {FLARUM_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "data": {
            "type": "posts",
            "attributes": {"content": content},
            "relationships": {
                "discussion": {"data": {"type": "discussions", "id": str(discussion_id)}}
            }
        }
    }
    r = requests.post(f"{FLARUM_API}/posts", headers=headers, json=payload, timeout=15)
    r.raise_for_status()
    return r.json()

def get_recent_posts(since_id=0):
    """Holt neue Posts seit since_id."""
    try:
        data = flarum_get("/posts", params={"filter[type]": "comment", "sort": "-createdAt", "page[limit]": 20})
        posts = data.get("data", [])
        new_posts = []
        for p in posts:
            pid = int(p["id"])
            if pid <= since_id:
                continue
            attrs = p.get("attributes", {})
            # Eigene Posts ignorieren — aber last_post_id trotzdem hochsetzen!
            author_id = p.get("relationships", {}).get("user", {}).get("data", {}).get("id")
            if str(author_id).isdigit() and int(author_id) in NOVA_USER_IDS:
                # BUG FIX: Ohne dieses Update sieht der Bot eigene Posts endlos wieder
                # (get_recent_posts gibt pid > since_id zurück, since_id wird nie erhöht)
                new_posts.append({
                    "id": pid,
                    "content": "",
                    "content_raw": "",
                    "discussion_id": p.get("relationships", {}).get("discussion", {}).get("data", {}).get("id"),
                    "created_at": "",
                    "author_id": author_id,
                    "_skip": True  # Flag: don't respond, just advance pointer
                })
                continue
            new_posts.append({
                "id": pid,
                "content": attrs.get("contentHtml", attrs.get("content", "")),
                "content_raw": attrs.get("content", ""),
                "discussion_id": p.get("relationships", {}).get("discussion", {}).get("data", {}).get("id"),
                "created_at": attrs.get("createdAt", ""),
                "author_id": author_id,
            })
        return sorted(new_posts, key=lambda x: x["id"])
    except Exception as e:
        log.error(f"Flarum GET error: {e}")
        return []

def should_respond(post, state):
    """Entscheidet ob Nova antworten soll."""
    # _skip = eigener Post, nur Pointer vorwärtsbewegen
    if post.get("_skip"):
        return False, "own_post"
    content = post["content_raw"].lower()
    post_id = post["id"]

    # Bereits verarbeitet?
    if post_id in state.get("processed", []):
        return False, "already_processed"

    # @nova Erwähnung → immer antworten
    if "@nova" in content or "@ailinux-nova-ai" in content:
        return True, "mention"

    # Sonst: alle neuen Posts (in aktiven Tags wenn konfiguriert)
    return True, "new_post"

# ── TriForce Chat ─────────────────────────────────────────────────────────────
def get_triforce_token():
    r = requests.post(
        "http://127.0.0.1:9000/v1/auth/login",
        json={"email": TRIFORCE_USER, "password": TRIFORCE_PASS},
        timeout=10
    )
    r.raise_for_status()
    data = r.json(); return data.get("access_token") or data.get("token")

def ask_nova(post_content, discussion_id, reason):
    """Schickt Post-Inhalt an TriForce, bekommt Nova-Antwort zurück."""
    try:
        token = get_triforce_token()
        
        if reason == "mention":
            system = (
                "Du bist Nova, KI-Assistent von AILinux (ailinux.me). "
                "Du wurdest im AILinux Community Forum direkt angesprochen. "
                "Antworte hilfreich, präzise und in der Sprache des Users. "
                "Halte Antworten kompakt — das ist ein Forum, kein Aufsatz. "
                "Kein Smalltalk, keine übertriebenen Floskeln."
            )
        else:
            system = (
                "Du bist Nova, KI-Assistent von AILinux (ailinux.me). "
                "Du beobachtest das AILinux Community Forum und kommentierst neue Beiträge. "
                "Antworte nur wenn du echten Mehrwert bietest — technische Hilfe, "
                "Ergänzungen, oder relevante Infos zu AILinux. "
                "Kein Kommentar wenn der Post keine sinnvolle Antwort erfordert — "
                "in dem Fall antworte nur mit dem Wort: SKIP "
                "Antworte in der Sprache des Users."
            )

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "openrouter/anthropic/claude-sonnet-4",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": f"Forum Post:\n\n{post_content}"}
            ],
            "max_tokens": 500,
            "stream": False
        }
        r = requests.post(TRIFORCE_URL, headers=headers, json=payload, timeout=60)
        r.raise_for_status()
        d = r.json()
        # /v1/chat gibt {"text": "..."} zurück (kein OpenAI-Format)
        if "text" in d:
            answer = d["text"].strip()
        else:
            answer = d.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        return answer
    except Exception as e:
        log.error(f"TriForce error: {e}")
        return None

# ── Main Loop ─────────────────────────────────────────────────────────────────
def main():
    log.info("Nova Flarum Bot gestartet")
    log.info(f"Ignoriere eigene Flarum user_ids: {sorted(NOVA_USER_IDS)}")
    wait_for_flarum_ready()
    state = load_state()

    # Beim ersten Start: aktuellen höchsten Post-ID als Baseline setzen (kein Spam)
    if state["last_post_id"] == 0:
        try:
            posts = get_recent_posts(0)
            if posts:
                state["last_post_id"] = posts[-1]["id"]
                save_state(state)
                log.info(f"Baseline gesetzt: last_post_id={state['last_post_id']}")
        except Exception as e:
            log.error(f"Baseline error: {e}")

    log.info(f"Polling alle {POLL_INTERVAL}s | Baseline post_id={state['last_post_id']}")

    while True:
        try:
            posts = get_recent_posts(state["last_post_id"])

            for post in posts:
                pid = post["id"]
                respond, reason = should_respond(post, state)

                if not respond:
                    if reason == "own_post":
                        log.debug(f"Post {pid} skip: eigener Post (Nova)")
                    else:
                        log.debug(f"Post {pid} skip: {reason}")
                    state["last_post_id"] = max(state["last_post_id"], pid)
                    save_state(state)  # BUG FIX: state auf Disk sichern auch bei skips
                    continue

                log.info(f"Post {pid} → verarbeite (reason={reason})")
                content = post["content_raw"] or post["content"]

                answer = ask_nova(content, post["discussion_id"], reason)

                if not answer or answer.strip().upper() == "SKIP":
                    log.info(f"Post {pid} → Nova sagt SKIP")
                else:
                    try:
                        created = flarum_post(post["discussion_id"], answer)
                        created_id = 0
                        try:
                            created_id = int(created.get("data", {}).get("id") or 0)
                        except Exception:
                            created_id = 0

                        if created_id:
                            # Defensive Loop-Sicherung: direkt nach eigener Antwort den Pointer
                            # auf die erzeugte Reply-ID ziehen. Damit wird die eigene Antwort
                            # nicht im nächsten Poll erneut verarbeitet, selbst wenn Author-Mapping
                            # im Forum später anders aussieht.
                            state["last_post_id"] = max(state["last_post_id"], created_id)
                            processed = state.get("processed", [])
                            processed.append(created_id)
                            state["processed"] = processed[-500:]
                            save_state(state)
                            log.info(f"Post {pid} → Nova geantwortet in Discussion {post['discussion_id']} | reply_post_id={created_id}")
                        else:
                            log.info(f"Post {pid} → Nova geantwortet in Discussion {post['discussion_id']} | reply_post_id=unknown")
                    except Exception as e:
                        log.error(f"Post {pid} → Flarum post error: {e}")

                # State updaten
                state["last_post_id"] = max(state["last_post_id"], pid)
                processed = state.get("processed", [])
                processed.append(pid)
                state["processed"] = processed[-500:]  # Max 500 IDs merken
                save_state(state)

                time.sleep(2)  # Kurz warten zwischen Posts

        except Exception as e:
            log.error(f"Poll error: {e}")

        time.sleep(POLL_INTERVAL)

if __name__ == "__main__":
    main()
