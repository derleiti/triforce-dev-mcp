"""
Nova Deploy Pipeline v1.0 — zombie-pc(dev) → hetzner(prod) via Federation
Backup macht alle 6h Snapshots. Bei 3x Production-Fail → Backup übernimmt.
"""
from __future__ import annotations
import asyncio, logging, time
logger = logging.getLogger("ailinux.deploy_pipeline")
_last_snapshot = 0.0; SNAPSHOT_INTERVAL = 21600
_prod_fail_count = 0; _backup_active = False

async def snapshot_production() -> bool:
    try:
        from .agent_spawner import get_agent_spawner
        await get_agent_spawner().spawn_for_issue(issue_type="ops_worker", source="deploy_pipeline:snapshot", context=(
            "SNAPSHOT PRODUCTION→BACKUP (10.10.0.3) via remote_task host=backup:\n"
            "1. mkdir -p /home/zombie/triforce-snapshots\n"
            "2. SNAP=snap_$(date +%Y%m%d_%H%M%S)\n"
            "   cp -r /home/zombie/workspace/triforce /home/zombie/triforce-snapshots/$SNAP\n"
            "3. ls -dt /home/zombie/triforce-snapshots/snap_* | tail -n +11 | xargs rm -rf 2>/dev/null\n"
            "4. notify_send '📸 Snapshot OK' priority=low auto_resolve=true\n5. TASK_COMPLETE"))
        logger.info("deploy_pipeline: Snapshot-Job → backup"); return True
    except Exception as e:
        logger.error(f"snapshot_production: {e}"); return False

async def deploy_to_production(commit_info: str = "") -> dict:
    try:
        from .agent_spawner import get_agent_spawner
        short = (commit_info[:60] or "latest").strip()
        asyncio.create_task(snapshot_production()); await asyncio.sleep(3)
        result = await get_agent_spawner().spawn_for_issue(issue_type="ops_worker",
            source="deploy_pipeline:prod", context=(
            f"PRODUCTION DEPLOY zombie-pc→hetzner: {commit_info[:200]}\n\n"
            "1. remote_task host=hetzner: cd /home/zombie/workspace/triforce && git pull origin master 2>&1|tail -5\n"
            "2. remote_task host=hetzner: find app/ -name '*.py' -newer app/__init__.py|head -20|"
            "   xargs -I{} .venv/bin/python3 -m py_compile {} 2>&1 && echo SYNTAX_OK\n"
            "3. NUR bei SYNTAX_OK: remote_task host=hetzner: "
            "   sudo systemctl restart triforce && sleep 12 && curl -sf http://localhost:9000/health|grep -o '\"ok\"'\n"
            f"4a. Output 'ok': notify_send '✅ Deploy OK: {short}' priority=high tags=[deploy,production]\n"
            f"4b. Sonst: git -C /home/zombie/workspace/triforce revert HEAD --no-edit && git push && "
            f"sudo systemctl restart triforce && "
            f"notify_send '🚨 Deploy FAILED+Rollback: {short}' priority=critical tags=[deploy,rollback]\n"
            "5. TASK_COMPLETE"))
        from app.mcp.notification_manager import create_event
        await create_event(
            title=f"Deploy gestartet: {short}",
            body=f"Session: {result.get('session_id','queued')}",
            source="system", priority="high", tags=["deploy","production"],
        )
        return {"status":"started","session_id":result.get("session_id")}
    except Exception as e:
        logger.error(f"deploy_to_production: {e}"); return {"status":"error","error":str(e)}

async def watch_for_deploy_ready(title: str, body: str, tags: list) -> bool:
    if "deploy_ready" not in (tags or []) and "deploy_ready" not in (title+body).lower(): return False
    logger.info(f"deploy_pipeline: DEPLOY_READY → {title[:60]}")
    await deploy_to_production(commit_info=body[:200]); return True

async def check_production_health() -> bool:
    global _prod_fail_count, _backup_active
    try:
        from app.mcp.structured_admin import handle_remote_exec
        res = await handle_remote_exec({"node": "hetzner", "command": "uptime"})
        if res and not res.get("error"):
            if _backup_active: await _deactivate_backup()
            _prod_fail_count = 0; return True
    except ImportError as e:
        logger.error(f"deploy_pipeline: ImportError in health check (not a prod failure): {e}")
        return True
    except Exception: pass
    _prod_fail_count += 1
    logger.warning(f"deploy_pipeline: Production unreachable ({_prod_fail_count}/3)")
    if _prod_fail_count >= 3 and not _backup_active: await _activate_backup()
    return False

async def _activate_backup():
    global _backup_active; _backup_active = True
    logger.critical("deploy_pipeline: Production DOWN — Backup aktiviert")
    try:
        from app.mcp.notification_manager import create_event
        await create_event(
            title="PRODUCTION DOWN - Backup uebernimmt",
            body="Production 3x nicht erreichbar. Backup (letzte Snapshot-Version) uebernimmt.",
            source="system", priority="critical", tags=["production", "backup", "failover"],
        )
        from app.mcp.structured_admin import handle_remote_exec
        await handle_remote_exec({"node": "backup", "command": "restart_triforce"})
    except Exception as e: logger.error(f"_activate_backup: {e}")

async def _deactivate_backup():
    global _backup_active, _prod_fail_count; _backup_active = False; _prod_fail_count = 0
    try:
        from app.mcp.notification_manager import create_event
        await create_event(
            title="Production wieder online",
            body="Failover beendet.",
            source="system", priority="high", tags=["production", "recovery"],
        )
    except Exception as e: logger.error(f"_deactivate_backup: {e}")

async def deploy_pipeline_tick():
    global _last_snapshot
    await check_production_health()
    if time.time() - _last_snapshot >= SNAPSHOT_INTERVAL:
        if await snapshot_production(): _last_snapshot = time.time()
