"""
TriForce Task Scheduler v2.0
=============================
Autonome Content-Engine + System-Tasks.

Task-Typen:
  agent_call   : Sendet Prompt an CLI-Agent
  mcp_tool     : Ruft MCP-Tool direkt auf
  notification : Erstellt Notification

Content-Engine:
  - Alle 1h WordPress-Post (Themen rotieren)
  - Alle 2h Flarum-Post
  - Mail-Check alle 10min
  - Forum-Check alle 5min
  - Research-Trigger alle 6h
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ailinux.task_scheduler")

# =============================================================================
# Content-Themen Rotation
# =============================================================================

WP_TOPICS = [
    "AILinux: Die Linux-Distribution mit eingebauter KI — was steckt dahinter?",
    "Linux News: Die wichtigsten Updates dieser Woche",
    "Tech-Gadgets: Neue Releases und was sich lohnt",
    "Gaming auf Linux: Aktueller Stand und Best Practices",
    "Open-Source-Tools die deinen Workflow verbessern",
    "KI-Tools 2026: Was ist neu, was ist nützlich?",
    "Homelab-Guide: Eigener Server mit Linux und KI",
    "Python-Entwicklung: Aktuelle Best Practices",
    "Hardware-Roundup: Neue Releases für Entwickler",
    "App-Updates: Was ist neu auf Windows, Mac und Linux?",
    "Docker und Container: Tipps für Einsteiger und Profis",
    "Raspberry Pi und Single-Board-Computer: Neue Projekte",
    "Cybersecurity: Was Entwickler wissen müssen",
    "Terminal-Tools: Die besten CLI-Werkzeuge",
    "TriForce Backend: Wie KI-Orchestrierung funktioniert",
]

FLARUM_TOPICS = [
    "Was nutzt ihr für KI auf eurem Linux-System?",
    "Neue Tech-Releases diese Woche — was interessiert euch?",
    "Gaming auf Linux: Eure Erfahrungen?",
    "Welche Open-Source-Tools könnt ihr empfehlen?",
    "KI-Tools im Alltag — was hat sich bewährt?",
    "Homelab-Projekte: Was baut ihr gerade?",
    "Python vs andere Sprachen — eure Meinung?",
    "Neue Hardware — was habt ihr kürzlich gekauft?",
    "Eure liebsten Terminal-Tools?",
    "Was wünscht ihr euch von AILinux?",
]

# =============================================================================
# Task-Konfigurationen
# =============================================================================

TASK_CONFIGS: List[Dict] = [
    {
        "id": "forum-check",
        "name": "Forum Activity Check",
        "interval_seconds": 300,
        "type": "mcp_tool",
        "tool": "flarum_discussions",
        "args": {"limit": 5, "sort": "-lastPostedAt"},
        "enabled": True,
        "description": "Neue Forum-Posts → Support-Agent spawnen",
        "on_result": "notify_if_new_forum",
    },
    {
        "id": "notify-process",
        "name": "Notification Queue Processor",
        "interval_seconds": 300,
        "type": "mcp_tool",
        "tool": "notify_list",
        "args": {"unread_only": True, "priority": "high"},
        "enabled": True,
        "description": "Offene Notifications — AgentSpawner-Watcher übernimmt",
        "on_result": "log_only",
    },
    {
        "id": "mail-check",
        "name": "Nova Inbox Check",
        "interval_seconds": 600,
        "type": "mcp_tool",
        "tool": "mail_inbox",
        "args": {"limit": 10},
        "enabled": True,
        "description": "nova@ailinux.me prüfen — Mails als Notifications weiterleiten",
        "on_result": "process_mail",
    },
    {
        "id": "system-health",
        "name": "System Health Check",
        "interval_seconds": 600,
        "type": "mcp_tool",
        "tool": "safe_probe",
        "args": {"action": "overview"},
        "enabled": True,
        "description": "System-Status — nur bei echten Crashes spawnen",
        "on_result": "log_only",
    },
    {
        "id": "wp-content",
        "name": "WordPress Content Engine",
        "interval_seconds": 3600,
        "type": "agent_call",
        "agent": "gemini-mcp",
        "prompt_template": "content_wp",
        "enabled": True,
        "description": "Stündlicher WordPress-Post via content_agent",
        "on_result": "log_only",
    },
    {
        "id": "flarum-content",
        "name": "Flarum Content Engine",
        "interval_seconds": 7200,
        "type": "agent_call",
        "agent": "gemini-mcp",
        "prompt_template": "content_flarum",
        "enabled": True,
        "description": "2-stündlicher Flarum-Discussion-Post via content_agent",
        "on_result": "log_only",
    },
    {
        "id": "research-scan",
        "name": "Codebase Research Scan",
        "interval_seconds": 21600,
        "type": "agent_call",
        "agent": "codex-mcp",
        "prompt_template": "research_scan",
        "enabled": True,
        "description": "6-stündlicher Code-Scan via research_agent → Mail an nova@",
        "on_result": "log_only",
    },
    {
        "id": "production-snapshot",
        "name": "Production Snapshot → Backup",
        "interval_seconds": 21600,
        "type": "mcp_tool",
        "tool": "notify_send",
        "args": {"title": "⏰ Snapshot fällig", "body": "deploy_pipeline_tick", "source": "system", "priority": "low", "auto_resolve": True, "tags": ["snapshot", "scheduler"]},
        "enabled": True,
        "description": "Alle 6h Snapshot Production → Backup",
        "on_result": "log_only",
    },
    {
        "id": "production-health",
        "name": "Production Health Monitor",
        "interval_seconds": 1800,
        "type": "mcp_tool",
        "tool": "notify_send",
        "args": {"title": "🔍 Health-Check", "body": "deploy_pipeline_health", "source": "system", "priority": "low", "auto_resolve": True, "tags": ["health", "scheduler"]},
        "enabled": True,
        "description": "Alle 30min Production-Health prüfen",
        "on_result": "log_only",
    },
    {
        "id": "wp-changelog",
        "name": "Daily Changelog Post",
        "interval_seconds": 86400,
        "type": "agent_call",
        "agent": "claude-mcp",
        "prompt": (
            "Schau dir die letzten Git-Commits von heute an (git_ops action=log lines=10) "
            "und erstelle einen WordPress-Post der die wichtigsten Änderungen zusammenfasst. "
            "Titel: 'TriForce Updates - {date}'. Nutze wp_publish_post."
        ),
        "enabled": False,
        "description": "Täglicher Changelog — manuell aktivieren",
        "on_result": "log_only",
    },
]

# =============================================================================
# ScheduledTask
# =============================================================================

class ScheduledTask:
    def __init__(self, config: Dict):
        self.id          = config["id"]
        self.name        = config["name"]
        self.interval    = config["interval_seconds"]
        self.type        = config["type"]
        self.enabled     = config.get("enabled", True)
        self.description = config.get("description", "")
        self.on_result   = config.get("on_result", "log_only")
        self.config      = config
        self.last_run    = 0.0
        self.run_count   = 0
        self.last_error: Optional[str] = None

    @property
    def due(self) -> bool:
        return self.enabled and (time.time() - self.last_run) >= self.interval

    def to_dict(self) -> Dict:
        return {
            "id":          self.id,
            "name":        self.name,
            "interval_s":  self.interval,
            "enabled":     self.enabled,
            "last_run":    self.last_run,
            "run_count":   self.run_count,
            "due_in_s":    max(0, int(self.interval - (time.time() - self.last_run))),
            "last_error":  self.last_error,
        }

# =============================================================================
# TaskScheduler
# =============================================================================

_topic_idx_wp:     int = 0
_topic_idx_flarum: int = 0

# Cooldown für Scheduler-Spawns
_scheduler_spawn_cooldown: Dict[str, float] = {}
SCHEDULER_SPAWN_COOLDOWN_S = 600

class TaskScheduler:
    def __init__(self):
        self._tasks:       Dict[str, ScheduledTask] = {}
        self._loop_task:   Optional[asyncio.Task] = None
        self._forum_seen:  set = set()
        self._mail_seen:   set = set()
        self._init_tasks()

    def _init_tasks(self):
        for cfg in TASK_CONFIGS:
            self._tasks[cfg["id"]] = ScheduledTask(cfg)

    def start(self):
        self._loop_task = asyncio.create_task(self._acquire_lock_and_run())
        active = sum(1 for t in self._tasks.values() if t.enabled)
        logger.info(f"Task Scheduler initialisiert — {active} aktive Tasks")

    async def stop(self):
        if self._loop_task:
            self._loop_task.cancel()

    async def _acquire_lock_and_run(self):
        """Nur ein Worker laeuft (Redis-Lock, wie Federation Manager)."""
        import redis.asyncio as aioredis
        redis_client = None
        lock_key = "triforce:scheduler:primary_lock"
        try:
            redis_client = aioredis.from_url("redis://localhost:6379", decode_responses=True)
            while True:
                acquired = await redis_client.set(lock_key, "1", nx=True, ex=60)
                if acquired:
                    logger.info("Task Scheduler: Primary Worker -- starte Loop")
                    break
                await asyncio.sleep(30)
        except Exception as e:
            logger.warning(f"Task Scheduler Redis-Lock fehlgeschlagen, starte ohne Lock: {e}")
        await self._scheduler_loop(redis_client, lock_key)

    async def _scheduler_loop(self, redis_client=None, lock_key=None):
        await asyncio.sleep(10)  # kurzer Startup-Delay
        while True:
            try:
                if redis_client and lock_key:
                    await redis_client.expire(lock_key, 60)
                for task in self._tasks.values():
                    if task.due:
                        asyncio.create_task(self._run_task(task))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Scheduler-Loop Fehler: {e}")
            await asyncio.sleep(30)

    async def _run_task(self, task: ScheduledTask):
        task.last_run  = time.time()
        task.run_count += 1
        logger.debug(f"Task {task.id} läuft (#{task.run_count})")

        try:
            if task.type == "mcp_tool":
                result = await self._run_mcp_tool(task)
            elif task.type == "agent_call":
                result = await self._run_agent_call(task)
            else:
                result = {}

            await self._handle_result(task, result)

        except Exception as e:
            task.last_error = str(e)[:200]
            logger.debug(f"Task {task.id} Fehler: {e}")

    async def _run_mcp_tool(self, task: ScheduledTask) -> Any:
        try:
            from app.routes.mcp import MCP_HANDLERS
            tool_name = task.config.get("tool", "")
            handler   = MCP_HANDLERS.get(tool_name)
            if not handler:
                return {}
            args = task.config.get("args", {})
            return await asyncio.wait_for(handler(args), timeout=30)
        except Exception as e:
            return {"error": str(e)}

    async def _run_agent_call(self, task: ScheduledTask) -> Any:
        global _topic_idx_wp, _topic_idx_flarum

        try:
            from app.services.agent_spawner import get_agent_spawner

            prompt_template = task.config.get("prompt_template", "")
            prompt          = task.config.get("prompt", "")
            spawner         = get_agent_spawner()

            if prompt_template == "content_wp":
                topic = WP_TOPICS[_topic_idx_wp % len(WP_TOPICS)]
                _topic_idx_wp += 1
                context = (
                    f"Erstelle einen WordPress-Post zum Thema: {topic}\n\n"
                    "Nutze web_search für aktuelle Informationen. "
                    "Direkt publishen mit wp_publish_post. "
                    "Am Ende: Einladung auf forum.ailinux.me zu diskutieren."
                )
                result = await spawner.spawn_for_issue(
                    issue_type="content_agent",
                    context=context,
                    source=f"scheduler:{task.id}",
                )
                logger.info(f"Content-Agent gespawnt für WP-Post: {topic[:50]}")
                return result

            elif prompt_template == "content_flarum":
                topic = FLARUM_TOPICS[_topic_idx_flarum % len(FLARUM_TOPICS)]
                _topic_idx_flarum += 1
                context = (
                    f"Erstelle einen Flarum-Discussion-Post zum Thema: {topic}\n\n"
                    "Kurz und einladend (150-250 Wörter). "
                    "flarum_discussion_create nutzen. "
                    "Ziel: Community zur Diskussion einladen."
                )
                result = await spawner.spawn_for_issue(
                    issue_type="content_agent",
                    context=context,
                    source=f"scheduler:{task.id}",
                )
                logger.info(f"Content-Agent gespawnt für Flarum: {topic[:50]}")
                return result

            elif prompt_template == "research_scan":
                context = (
                    "Führe einen systematischen Code-Scan durch:\n"
                    "1. app/services/ — Performance und Fehler-Handling\n"
                    "2. app/routes/ — Security und Validierung\n"
                    "3. app/mcp/ — Tool-Definitionen und Edge-Cases\n\n"
                    "Sende Findings per Mail an nova@ailinux.me.\n"
                    "Betreff: [RESEARCH] <Thema>\n"
                    "Maximal 3 Findings pro Scan (Fokus auf High-Impact)."
                )
                result = await spawner.spawn_for_issue(
                    issue_type="research_agent",
                    context=context,
                    source=f"scheduler:{task.id}",
                )
                logger.info("Research-Agent gespawnt für Code-Scan")
                return result

            elif prompt:
                from app.routes.mcp import MCP_HANDLERS
                agent_call = MCP_HANDLERS.get("agent_call") or MCP_HANDLERS.get("cli-agents_call")
                if agent_call:
                    agent_id = task.config.get("agent", "claude-mcp")
                    msg      = prompt.format(date=time.strftime("%Y-%m-%d"))
                    return await asyncio.wait_for(
                        agent_call({"agent_id": agent_id, "message": msg}), timeout=60
                    )
            return {}

        except Exception as e:
            return {"error": str(e)}

    async def _handle_result(self, task: ScheduledTask, result: Any):
        on_result = task.on_result

        if on_result == "log_only":
            return

        elif on_result == "notify_if_new_forum":
            await self._check_new_forum_posts(result)

        elif on_result == "process_mail":
            await self._process_mail(result)

        elif on_result == "notify_if_new":
            await self._notify_if_new(task, result)

    async def _check_new_forum_posts(self, result: Any):
        # v2: Forum processing moved to notification_manager.py pollers
        return
        """Neue Flarum-Posts → Support-Agent spawnen."""
        try:
            if not isinstance(result, dict):
                return
            discussions = result.get("data", []) or result.get("discussions", [])
            if not discussions:
                return

            from app.mcp.notification_manager import create_notification
            from app.services.agent_spawner import get_agent_spawner
            spawner = get_agent_spawner()

            for d in discussions[:3]:
                did   = str(d.get("id", ""))
                title = d.get("attributes", {}).get("title", d.get("title", "Unbekannt"))
                if not did or did in self._forum_seen:
                    continue

                self._forum_seen.add(did)

                # Forum-Post → Support-Agent spawnen
                context = (
                    f"Neuer Forum-Post: {title}\n"
                    f"Discussion-ID: {did}\n"
                    f"URL: https://forum.ailinux.me/d/{did}\n\n"
                    "Prüfe ob es eine Support-Anfrage ist und antworte entsprechend."
                )
                await spawner.spawn_for_issue(
                    issue_type="support_agent",
                    context=context,
                    source=f"forum:{did}",
                )
                logger.info(f"Support-Agent für Forum-Post gespawnt: {title[:40]}")

        except Exception as e:
            logger.debug(f"_check_new_forum_posts Fehler: {e}")

    async def _process_mail(self, result: Any):
        # v2: Mail processing moved to notification_manager.py pollers
        return
        """
        Eingehende Mails verarbeiten.
        Mail-Guard:
          nova@ailinux.me → nur notify (kein Backend-Zugriff)
          admin@ailinux.me → Forum-Proposal
          [RESEARCH] im Betreff → research_agent spawnen
        """
        try:
            if not isinstance(result, dict):
                return

            messages = result.get("messages", []) or result.get("emails", [])
            if not messages:
                return

            from app.mcp.notification_manager import create_notification
            from app.services.agent_spawner import get_agent_spawner
            spawner = get_agent_spawner()

            for mail in messages:
                uid     = str(mail.get("uid", mail.get("id", "")))
                subject = mail.get("subject", "Kein Betreff")
                sender  = str(mail.get("from", "")).lower()
                snippet = mail.get("snippet", mail.get("body", ""))[:300]

                if not uid or uid in self._mail_seen:
                    continue
                self._mail_seen.add(uid)

                subj_lower = subject.lower()

                # [RESEARCH] Antwort von Zombie → implementation_agent spawnen
                # Erkennungsregeln:
                #   - Betreff beginnt mit "Re:" und enthält "[research]"
                #   - ODER Betreff beginnt mit "[approved]"
                #   - ODER Betreff beginnt mit "[umsetzen]"
                is_research_approval = (
                    ("[research]" in subj_lower and (
                        subj_lower.startswith("re:") or
                        subj_lower.startswith("aw:") or
                        subj_lower.startswith("antw:")
                    )) or
                    subj_lower.startswith("[approved]") or
                    subj_lower.startswith("[umsetzen]")
                )
                if is_research_approval:
                    context = (
                        f"RESEARCH APPROVED — Implementation-Auftrag:\n"
                        f"Betreff: {subject}\n"
                        f"Von: {sender}\n\n"
                        f"Genehmigter Vorschlag:\n{snippet}\n\n"
                        f"WORKFLOW:\n"
                        f"1. code_read → betroffene Datei(en) lesen\n"
                        f"2. code_edit/code_patch → implementieren\n"
                        f"3. dev_lint → Syntax prüfen\n"
                        f"4. Shadow-Test auf zombie-pc (10.10.0.2) via remote_task\n"
                        f"   Befehl: cd /home/zombie/workspace/triforce && .venv/bin/python3 -m pytest tests/ -x -q 2>&1 | tail -20\n"
                        f"5. Nur bei grünen Tests: git_ops commit + push\n"
                        f"   Commit-Format: fix(<modul>): <beschreibung> — research-approved by zombie\n"
                        f"6. notify_send → Ergebnis melden"
                    )
                    result_spawn = await spawner.spawn_for_issue(
                        issue_type="implementation_agent",
                        context=context,
                        source=f"mail:research_approval:{uid}",
                    )
                    create_notification({
                        "title": f"🔧 Implementation gestartet: {subject[:50]}",
                        "body": f"Session: {result_spawn.get('session_id', 'queued')} | {snippet[:150]}",
                        "source": "mail", "priority": "high",
                        "tags": ["mail", "implementation", "research-approved"],
                    })
                    logger.info(f"Implementation-Agent gespawnt für: {subject[:60]}")
                    continue

                # Neue [RESEARCH]-Mail (kein Reply) → nur notify, kein Spawn
                if "[research]" in subj_lower:
                    create_notification({
                        "title": f"📬 Research-Mail: {subject[:60]}",
                        "body": snippet[:200],
                        "source": "mail", "priority": "normal",
                        "tags": ["mail", "research"],
                    })
                    continue

                # admin@ailinux.me → Forum-Proposal
                if "admin@ailinux.me" in sender:
                    create_notification({
                        "title": f"📩 Admin-Anfrage: {subject[:60]}",
                        "body": f"Von: {sender}\n\n{snippet}",
                        "source": "mail", "priority": "high",
                        "tags": ["mail", "admin-request"],
                        "action_url": "",
                    })
                    continue

                # Alle anderen Mails → nur notify (Mail-Guard)
                create_notification({
                    "title": f"📧 Mail: {subject[:60]}",
                    "body": f"Von: {sender}\n{snippet}",
                    "source": "mail", "priority": "normal",
                    "tags": ["mail", "auto"],
                })

        except Exception as e:
            logger.debug(f"_process_mail Fehler: {e}")

    async def _notify_if_new(self, task: ScheduledTask, result: Any):
        try:
            if not isinstance(result, dict):
                return
            from app.mcp.notification_manager import create_notification
            create_notification({
                "title": f"[{task.name}] Neues Ergebnis",
                "body":  str(result)[:200],
                "source": "system", "priority": "normal",
                "tags":  ["scheduler", task.id],
            })
        except Exception as e:
            logger.debug(f"_notify_if_new Fehler: {e}")

    async def _spawn_if_critical(self, task: ScheduledTask, result: Any):
        """Nur bei echten Crash-Signalen spawnen — mit Cooldown."""
        try:
            result_str = json.dumps(result) if isinstance(result, dict) else str(result)
            CRITICAL_SIGNALS = [
                "traceback", "exception", "crashed", "service failed",
                "connection refused", "disk full", "oom killed",
            ]
            if not any(kw in result_str.lower() for kw in CRITICAL_SIGNALS):
                return

            now  = time.time()
            last = _scheduler_spawn_cooldown.get(task.id, 0)
            if now - last < SCHEDULER_SPAWN_COOLDOWN_S:
                logger.debug(f"Scheduler-Spawn Cooldown für '{task.id}'")
                return
            _scheduler_spawn_cooldown[task.id] = now

            from app.services.agent_spawner import get_agent_spawner
            spawner = get_agent_spawner()
            await spawner.spawn_for_issue(
                issue_type="system_error",
                context=result_str[:2000],
                source=f"scheduler:{task.id}",
            )
        except Exception as e:
            logger.debug(f"spawn_if_critical Fehler: {e}")

    # ── Management ────────────────────────────────────────────────────────────

    def list_tasks(self) -> List[Dict]:
        return [t.to_dict() for t in self._tasks.values()]

    def enable(self, task_id: str) -> bool:
        if task_id in self._tasks:
            self._tasks[task_id].enabled = True
            return True
        return False

    def disable(self, task_id: str) -> bool:
        if task_id in self._tasks:
            self._tasks[task_id].enabled = False
            return True
        return False

    def get_task(self, task_id: str) -> Optional[Dict]:
        t = self._tasks.get(task_id)
        return t.to_dict() if t else None


# Singleton
_scheduler_instance: Optional[TaskScheduler] = None

def get_task_scheduler() -> TaskScheduler:
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = TaskScheduler()
    return _scheduler_instance
