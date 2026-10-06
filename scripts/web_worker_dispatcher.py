#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from app.mcp.web_worker import _load as load_jobs, _save as save_jobs

log = logging.getLogger("ailinux.web_worker_dispatcher")
CDP_URL = "http://127.0.0.1:9223"
TRUSTED_ORIGINS = {"revenue_loop"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def select_job(jobs: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    candidates = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict) or job.get("state") != "queued":
            continue
        meta = job.get("metadata") or {}
        if meta.get("auto_dispatch") is not True:
            continue
        if str(meta.get("origin") or "") not in TRUSTED_ORIGINS:
            continue
        dispatch = meta.get("dispatch") or {}
        if dispatch.get("state") in {"submitted", "claimed"}:
            continue
        candidates.append((str(job.get("created_at") or ""), job_id, job))
    if not candidates:
        return None, None
    candidates.sort()
    _, job_id, job = candidates[0]
    return job_id, job


def mark(jobs: dict[str, dict[str, Any]], job_id: str, **values: Any) -> None:
    job = jobs[job_id]
    meta = dict(job.get("metadata") or {})
    dispatch = dict(meta.get("dispatch") or {})
    dispatch.update(values)
    meta["dispatch"] = dispatch
    job["metadata"] = meta
    jobs[job_id] = job
    save_jobs(jobs)


async def submit_job(job_id: str) -> str:
    prompt = (
        "You are an autonomous TriForce web worker. Call the TriForce MCP tool web_worker "
        f"with action=claim and job_id={job_id}. Follow the returned task exactly. "
        "When finished, call web_worker with action=complete for the same job_id and include the result. "
        "Do not ask for confirmation unless the claimed task itself explicitly requires human action."
    )
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(CDP_URL)
        if not browser.contexts:
            raise RuntimeError("worker browser has no context")
        ctx = browser.contexts[0]
        page = await ctx.new_page()
        await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1200)
        box = page.locator("#prompt-textarea").first
        await box.wait_for(state="visible", timeout=15000)
        await box.fill(prompt)
        button = page.locator('button[data-testid="send-button"]').first
        if await button.count() and await button.is_visible() and await button.is_enabled():
            await button.click()
        else:
            await box.press("Enter")
        await page.wait_for_timeout(800)
        return page.url


async def run_once() -> bool:
    jobs = load_jobs()
    job_id, job = select_job(jobs)
    if not job_id:
        return False
    try:
        mark(jobs, job_id, state="starting", last_attempt_at=now())
        url = await submit_job(job_id)
        jobs = load_jobs()
        mark(jobs, job_id, state="submitted", submitted_at=now(), chat_url=url)
        log.info("dispatched worker job %s", job_id)
        return True
    except Exception as exc:
        jobs = load_jobs()
        meta = (jobs.get(job_id, {}).get("metadata") or {})
        dispatch = meta.get("dispatch") or {}
        attempts = int(dispatch.get("attempts") or 0) + 1
        mark(jobs, job_id, state="retry", attempts=attempts, last_error=f"{type(exc).__name__}: {exc}"[:500])
        log.warning("dispatch failed for %s: %s", job_id, exc)
        return False


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    while True:
        await run_once()
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
