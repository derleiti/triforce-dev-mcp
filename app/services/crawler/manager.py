from __future__ import annotations

import asyncio
import json
import random
import re
import uuid
from collections import OrderedDict, defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from playwright.async_api import async_playwright, Playwright
import playwright
# Temporär auskommentiert wegen crawlee API-Änderungen
# from crawlee.playwright_crawler import PlaywrightCrawler, PlaywrightCrawlingContext
# from crawlee._request import Request
# from crawlee.storages._request_queue import RequestQueue

# Temporäre Mock-Klassen für Kompatibilität
from unittest.mock import MagicMock

class PlaywrightCrawlingContext:
    def __init__(self):
        self.request = MagicMock()
        self.response = MagicMock()
        self.page = MagicMock()
        
class PlaywrightCrawler:
    def __init__(self, **kwargs):
        pass
    
    async def run(self, requests):
        pass

class Request:
    def __init__(self, url, **kwargs):
        self.url = url
        self.headers = kwargs.get('headers', {})
        self.user_data = kwargs.get('user_data', {})

# Mock für RequestQueue
class RequestQueue:
    pass


import httpx
import ipaddress
import socket
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

import hashlib


# SECURITY: SSRF Protection. Private RFC1918/ULA ranges are still blocked
# by default and may only be enabled by a trusted internal MCP caller.
PRIVATE_IP_RANGES = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)

BLOCKED_IP_RANGES = [
    *PRIVATE_IP_RANGES,
    ipaddress.ip_network("127.0.0.0/8"),     # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local
    ipaddress.ip_network("::1/128"),         # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
    ipaddress.ip_network("0.0.0.0/8"),       # "This" network
    ipaddress.ip_network("100.64.0.0/10"),   # Carrier-grade NAT
    ipaddress.ip_network("192.0.0.0/24"),    # IETF Protocol Assignments
    ipaddress.ip_network("192.0.2.0/24"),    # TEST-NET-1
    ipaddress.ip_network("198.51.100.0/24"), # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),     # Multicast
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved
]

# Blocked hostnames that could bypass IP checks
BLOCKED_HOSTNAMES = frozenset([
    "localhost",
    "localhost.localdomain",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "metadata.google.internal",        # GCP metadata
    "169.254.169.254",                 # AWS/GCP/Azure metadata
    "metadata.google.internal.",
])


def is_ssrf_safe(url: str, *, allow_private_networks: bool = False) -> Tuple[bool, str]:
    """
    Check if a URL is safe from SSRF attacks.

    Returns:
        Tuple of (is_safe, reason)
    """
    try:
        parsed = urlparse(url)

        # Check scheme
        if parsed.scheme not in ("http", "https"):
            return False, f"Invalid scheme: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL"

        # Check against blocked hostnames
        hostname_lower = hostname.lower()
        if hostname_lower in BLOCKED_HOSTNAMES:
            return False, f"Blocked hostname: {hostname}"

        # Resolve hostname to IP
        try:
            # Use getaddrinfo for both IPv4 and IPv6
            addr_info = socket.getaddrinfo(hostname, parsed.port or 80, proto=socket.IPPROTO_TCP)

            for family, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)

                    if allow_private_networks and any(ip in private for private in PRIVATE_IP_RANGES):
                        continue

                    # Check against blocked ranges
                    for blocked_range in BLOCKED_IP_RANGES:
                        if ip in blocked_range:
                            return False, f"IP {ip} is in blocked range {blocked_range}"

                except ValueError:
                    continue

        except socket.gaierror as e:
            return False, f"DNS resolution failed: {e}"
        except socket.timeout:
            return False, "DNS resolution timed out"

        return True, "URL is safe"

    except Exception as e:
        return False, f"URL validation error: {e}"
import gzip
import jsonlines
from rank_bm25 import BM25Okapi
from app.paths import DATA_DIR
from ...config import get_settings
from .shared_state import CrawlerSharedState, shared_crawler_state

logger = __import__("logging").getLogger("ailinux.crawler")

# Optional imports from existing services; imported lazily to avoid heavy dependencies
try:  # pragma: no cover - registry import is optional during unit tests
    from ..model_registry import registry
    from .. import chat as chat_service
except Exception:  # pragma: no cover
    registry = None
    chat_service = None


USER_AGENT = "AILinuxCrawler/1.0"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

SUMMARY_SYSTEM_PROMPT = (
    "You are Nova AI. Summarise the crawled article for publication on the AILinux network. "
    "Return three bullet points highlighting key takeaways and one short headline (<=120 chars)."
)

RELEVANT_META_KEYS = {
    "description",
    "og:description",
    "twitter:description",
}

ARTICLE_SELECTORS = [
    "article",
    "main article",
    "div[itemtype='http://schema.org/Article']",
    "div[itemtype='https://schema.org/Article']",
    "div.post-content",
    "div.entry-content",
]

PUBLISH_META_KEYS = [
    "article:published_time",
    "article:modified_time",
    "og:updated_time",
    "date",
    "dc.date",
    "dc.date.issued",
    "dc.date.created",
    "pubdate",
]


@dataclass
class CrawlFeedback:
    score: float
    comment: Optional[str]
    source: str
    confirmed: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "comment": self.comment,
            "source": self.source,
            "confirmed": self.confirmed,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class CrawlerMetrics:
    """Simple metrics tracker per crawler category."""

    pages_crawled: int = 0
    pages_failed: int = 0
    requests_429: int = 0
    requests_5xx: int = 0
    last_error_at: Optional[datetime] = None

    def record_success(self) -> None:
        self.pages_crawled += 1

    def record_failure(self, status_code: int) -> None:
        self.pages_failed += 1
        if status_code == 429:
            self.requests_429 += 1
        elif status_code >= 500:
            self.requests_5xx += 1
        self.last_error_at = datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, Any]:
        return {
            "pages_crawled": self.pages_crawled,
            "pages_failed": self.pages_failed,
            "requests_429": self.requests_429,
            "requests_5xx": self.requests_5xx,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
        }


@dataclass
class CrawlResult:
    id: str
    job_id: str
    url: str
    depth: int
    parent_url: Optional[str]
    status: str
    title: str
    summary: Optional[str]
    headline: Optional[str]
    content: str
    excerpt: str
    meta_description: Optional[str]
    keywords_matched: List[str]
    score: float
    publish_date: Optional[str]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ratings: List[CrawlFeedback] = field(default_factory=list)
    rating_average: float = 0.0
    rating_count: int = 0
    confirmations: int = 0
    tags: List[str] = field(default_factory=list)
    spool_path: Optional[Path] = None
    size_bytes: int = 0
    posted_at: Optional[datetime] = None
    post_id: Optional[int] = None
    topic_id: Optional[int] = None
    normalized_text: Optional[str] = None
    content_hash: Optional[str] = None
    source_domain: Optional[str] = None
    labels: List[str] = field(default_factory=list)
    tokens_est: Optional[int] = None
    extracted_content_ollama: Optional[str] = None

    def to_dict(self, include_content: bool = True) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "job_id": self.job_id,
            "url": self.url,
            "depth": self.depth,
            "parent_url": self.parent_url,
            "status": self.status,
            "title": self.title,
            "summary": self.summary,
            "headline": self.headline,
            "excerpt": self.excerpt,
            "meta_description": self.meta_description,
            "keywords_matched": self.keywords_matched,
            "score": self.score,
            "publish_date": self.publish_date,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "rating_average": self.rating_average,
            "rating_count": self.rating_count,
            "confirmations": self.confirmations,
            "tags": self.tags,
            "posted_at": self.posted_at.isoformat() if self.posted_at else None,
            "post_id": self.post_id,
            "topic_id": self.topic_id,
            "normalized_text": self.normalized_text,
            "content_hash": self.content_hash,
            "source_domain": self.source_domain,
            "labels": self.labels,
            "tokens_est": self.tokens_est,
            "extracted_content_ollama": self.extracted_content_ollama,
            "size_bytes": self.size_bytes,
            "ratings": [feedback.to_dict() for feedback in self.ratings],
        }
        if include_content:
            payload["content"] = self.content
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrawlResult":
        result = cls(
            id=data["id"],
            job_id=data["job_id"],
            url=data["url"],
            depth=data.get("depth", 0),
            parent_url=data.get("parent_url"),
            status=data.get("status", "pending"),
            title=data.get("title", ""),
            summary=data.get("summary"),
            headline=data.get("headline"),
            content=data.get("content", ""),
            excerpt=data.get("excerpt", ""),
            meta_description=data.get("meta_description"),
            keywords_matched=data.get("keywords_matched", []),
            score=data.get("score", 0.0),
            publish_date=data.get("publish_date"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(timezone.utc),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(timezone.utc),
            tags=data.get("tags", []),
            normalized_text=data.get("normalized_text"),
            content_hash=data.get("content_hash"),
            source_domain=data.get("source_domain"),
            labels=data.get("labels", []),
            tokens_est=data.get("tokens_est"),
            extracted_content_ollama=data.get("extracted_content_ollama"),
        )
        for rating in data.get("ratings", []):
            result.ratings.append(
                CrawlFeedback(
                    score=rating.get("score", 0.0),
                    comment=rating.get("comment"),
                    source=rating.get("source", "unknown"),
                    confirmed=rating.get("confirmed", False),
                    created_at=datetime.fromisoformat(rating["created_at"]) if rating.get("created_at") else datetime.now(timezone.utc),
                )
            )
        result.rating_count = data.get("rating_count", len(result.ratings))
        result.rating_average = data.get("rating_average", 0.0)
        result.confirmations = data.get("confirmations", 0)
        if data.get("posted_at"):
            result.posted_at = datetime.fromisoformat(data["posted_at"])
        result.post_id = data.get("post_id")
        result.topic_id = data.get("topic_id")
        result.size_bytes = data.get("size_bytes", 0)
        return result


@dataclass
class CrawlJob:
    id: str
    keywords: List[str]
    seeds: List[str]
    max_depth: int
    max_pages: int
    allowed_domains: Set[str]
    allow_external: bool
    relevance_threshold: float
    rate_limit: float
    user_context: Optional[str]
    requested_by: Optional[str]
    metadata: dict[str, Any] = field(default_factory=dict)
    ollama_assisted: bool = False
    ollama_query: Optional[str] = None
    status: str = "queued"
    priority: str = "low"
    idempotency_key: Optional[str] = None
    category: str = "background"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    pages_crawled: int = 0
    results: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "keywords": self.keywords,
            "seeds": self.seeds,
            "max_depth": self.max_depth,
            "max_pages": self.max_pages,
            "allowed_domains": list(self.allowed_domains),
            "allow_external": self.allow_external,
            "relevance_threshold": self.relevance_threshold,
            "rate_limit": self.rate_limit,
            "user_context": self.user_context,
            "requested_by": self.requested_by,
            "metadata": self.metadata,
            "idempotency_key": self.idempotency_key,
            "category": self.category,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "pages_crawled": self.pages_crawled,
            "results": self.results,
            "error": self.error,
        }


class CrawlerStore:
    """In-memory cache with disk spill-over for crawl results."""

    def __init__(self, max_memory_bytes: int, spool_dir: Path):
        self.max_memory_bytes = max_memory_bytes
        self.spool_dir = spool_dir
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self._records: OrderedDict[str, CrawlResult] = OrderedDict()
        self._sizes: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self._memory_usage = 0

    async def add(self, result: CrawlResult) -> None:
        data = json.dumps(result.to_dict(include_content=True), ensure_ascii=False)
        size = len(data.encode("utf-8"))
        result.size_bytes = size
        async with self._lock:
            # Deduplicate by content_hash
            if result.content_hash:
                for existing_result in self._records.values():
                    if existing_result.content_hash == result.content_hash:
                        # Update existing result if new one is better (e.g., higher score, more recent)
                        if result.score > existing_result.score or result.updated_at > existing_result.updated_at:
                            self._records[existing_result.id] = result
                            self._sizes[result.id] = size
                            self._memory_usage += (size - self._sizes.get(existing_result.id, 0))
                            logger.debug("Updated duplicate crawl result %s with new data", result.id)
                        return

            await self._ensure_capacity(size)
            self._records[result.id] = result
            self._sizes[result.id] = size
            self._memory_usage += size

    async def _ensure_capacity(self, required: int) -> None:
        if self.max_memory_bytes <= 0:
            return
        while self._memory_usage + required > self.max_memory_bytes and self._records:
            record_id, record = self._records.popitem(last=False)
            size = self._sizes.pop(record_id, 0)
            self._memory_usage -= size
            # Removed _spill_to_disk call, now handled by CrawlerManager's flush methods

    async def get(self, result_id: str) -> Optional[CrawlResult]:
        async with self._lock:
            record = self._records.get(result_id)
            if record:
                return record
        # Disk lookup for individual files is removed, now handled by search across shards
        return None

    async def list(self, predicate) -> List[CrawlResult]:
        async with self._lock:
            records = list(self._records.values())
        matched = [record for record in records if predicate(record)]
        # Disk lookup for individual files is removed, now handled by search across shards
        return matched

    async def update(self: 'CrawlerStore', result: CrawlResult) -> None:
        async with self._lock:
            if result.id in self._records:
                self._records[result.id] = result
                data = json.dumps(result.to_dict(include_content=True), ensure_ascii=False)
                size = len(data.encode("utf-8"))
                delta = size - self._sizes.get(result.id, 0)
                self._memory_usage += delta
                self._sizes[result.id] = size
            else:
                # If not in RAM, it must be on disk, but we don't update individual files anymore
                pass


class CrawlerManager:
    def __init__(self, *, shared_state: Optional[CrawlerSharedState] = None, instance_name: str = "default") -> None:
        settings = get_settings()
        spool_dir = Path(getattr(settings, "crawler_spool_dir", str(DATA_DIR / "crawler_spool")))
        self._store = CrawlerStore(
            max_memory_bytes=int(getattr(settings, "crawler_max_memory_bytes", 2 * 1024**3)),
            spool_dir=spool_dir,
        )
        self._jobs: Dict[str, CrawlJob] = {}
        self._job_queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._high_priority_job_queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._robots_cache: Dict[str, Optional[RobotFileParser]] = {}
        self._worker_tasks: list[asyncio.Task] = []
        self._worker_pool_size = 1
        self._auto_crawl_task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._random = random.Random()
        self._instance_name = instance_name

        self._shared_state = shared_state or shared_crawler_state
        self._metrics_by_category: defaultdict[str, CrawlerMetrics] = defaultdict(CrawlerMetrics)
        self._metrics_lock = asyncio.Lock()
        self._host_locks: Dict[str, asyncio.Lock] = {}
        self._host_state_lock = asyncio.Lock()
        self._host_backoff: Dict[str, float] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._max_concurrent_requests = int(getattr(settings, "user_crawler_max_concurrent", 4))
        self._last_heartbeat: datetime = datetime.now(timezone.utc)

        # Training data management
        self._train_dir = Path(getattr(settings, "crawler_train_dir", "data/crawler_spool/train"))
        self._train_dir.mkdir(parents=True, exist_ok=True)
        self._train_index_path = self._train_dir / "index.json"
        self._current_shard_path: Optional[Path] = None
        self._last_flush_time: datetime = datetime.now(timezone.utc)
        self._train_buffer: List[CrawlResult] = []
        self._train_buffer_max_size = int(getattr(settings, "crawler_buffer_max_size", 1000))
        self._flush_interval = int(getattr(settings, "crawler_flush_interval", 3600))
        self._retention_days = int(getattr(settings, "crawler_retention_days", 30))
        self._load_train_index()

    def _load_train_index(self) -> None:
        if self._train_index_path.exists():
            try:
                with open(self._train_index_path, "r", encoding="utf-8") as f:
                    self._train_index = json.load(f)
            except json.JSONDecodeError:
                logger.warning("Could not decode train index file, starting fresh.")
                self._train_index = {"shards": []}
        else:
            self._train_index = {"shards": []}

    def _save_train_index(self) -> None:
        with open(self._train_index_path, "w", encoding="utf-8") as f:
            json.dump(self._train_index, f, indent=2)

    async def start(
        self,
        *,
        worker_count: Optional[int] = None,
        max_concurrent: Optional[int] = None,
    ) -> None:
        if worker_count is not None:
            self._worker_pool_size = max(1, int(worker_count))
        if max_concurrent is not None:
            self._max_concurrent_requests = max(1, int(max_concurrent))

        active_workers = [task for task in self._worker_tasks if not task.done()]
        if active_workers:
            await self._ensure_worker_pool()
            return

        self._stop_event.clear()
        self._loop = asyncio.get_running_loop()
        self._last_heartbeat = datetime.now(timezone.utc)
        logger.info(
            "Starting crawler manager '%s' with %d workers (max_concurrent=%d)",
            self._instance_name,
            self._worker_pool_size,
            self._max_concurrent_requests,
        )

        for idx in range(self._worker_pool_size):
            worker = asyncio.create_task(
                self._run_worker(worker_id=idx),
                name=f"{self._instance_name}-worker-{idx}",
            )
            self._worker_tasks.append(worker)

        # Start background flush task
        if not any(t.get_name() == f"{self._instance_name}-flush" for t in self._worker_tasks):
            self._worker_tasks.append(
                asyncio.create_task(self.flush_hourly(), name=f"{self._instance_name}-flush")
            )

        # Only the primary manager runs the legacy auto-crawl loop
        if self._instance_name == "default" and not self._auto_crawl_task:
            self._auto_crawl_task = asyncio.create_task(self._run_auto_crawler(), name="auto-crawler")

        logger.info("Crawler manager '%s' started", self._instance_name)

    async def flush_hourly(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(self._flush_interval)
            await self.flush_to_jsonl()

    async def _ensure_worker_pool(self) -> None:
        """Ensure the running worker pool matches the desired size."""
        self._worker_tasks = [task for task in self._worker_tasks if not task.done()]
        current = len(self._worker_tasks)

        if current < self._worker_pool_size:
            logger.info(
                "Scaling up crawler manager '%s' workers from %d to %d",
                self._instance_name,
                current,
                self._worker_pool_size,
            )
            for idx in range(current, self._worker_pool_size):
                worker = asyncio.create_task(
                    self._run_worker(worker_id=idx),
                    name=f"{self._instance_name}-worker-{idx}",
                )
                self._worker_tasks.append(worker)
        elif current > self._worker_pool_size:
            logger.info(
                "Scaling down crawler manager '%s' workers from %d to %d",
                self._instance_name,
                current,
                self._worker_pool_size,
            )
            # cancel extra workers
            extra = self._worker_tasks[self._worker_pool_size :]
            for task in extra:
                task.cancel()
            await asyncio.gather(*extra, return_exceptions=True)
            self._worker_tasks = self._worker_tasks[: self._worker_pool_size]

    async def flush_to_jsonl(self) -> None:
        if not self._train_buffer:
            return

        current_hour = datetime.now(timezone.utc).strftime("%Y%m%d-%H")
        shard_name = f"crawl-train-{current_hour}.jsonl"
        shard_path = self._train_dir / shard_name

        records_flushed = 0
        size_flushed = 0

        async with self._lock:
            with jsonlines.open(shard_path, mode="a") as writer:
                for result in self._train_buffer:
                    data = result.to_dict(include_content=False) # Don't include full content in JSONL
                    writer.write(data)
                    records_flushed += 1
                    size_flushed += len(json.dumps(data).encode("utf-8"))
            self._train_buffer.clear()

            # Update train index
            found = False
            for shard_entry in self._train_index["shards"]:
                if shard_entry["name"] == shard_name:
                    shard_entry["records"] += records_flushed
                    shard_entry["size_bytes"] += size_flushed
                    found = True
                    break
            if not found:
                self._train_index["shards"].append({
                    "name": shard_name,
                    "records": records_flushed,
                    "size_bytes": size_flushed,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                })
            self._save_train_index()

        logger.info("Flushed %d records (%.2f KB) to %s", records_flushed, size_flushed / 1024, shard_name)

    async def shutdown_flush(self) -> None:
        logger.info("Performing final flush of RAM buffer to JSONL shards.")
        await self.flush_to_jsonl()

    async def compact_spool(self) -> None:
        while not self._stop_event.is_set():
            # Run daily
            await asyncio.sleep(86400)
            logger.info("Starting spool compaction and archiving.")
            archive_dir = self._train_dir / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=self._retention_days)

            async with self._lock:
                shards_to_update = []
                for shard_info in self._train_index["shards"]:
                    shard_path = self._train_dir / shard_info["name"]
                    if not shard_path.exists():
                        continue

                    # Parse date from shard name (e.g., crawl-train-YYYYMMDD-HH.jsonl)
                    try:
                        date_str = shard_info["name"].replace("crawl-train-", "").replace(".jsonl", "")
                        shard_date = datetime.strptime(date_str, "%Y%m%d-%H").replace(tzinfo=timezone.utc)
                    except ValueError:
                        logger.warning("Could not parse date from shard name: %s", shard_info["name"])
                        shards_to_update.append(shard_info)
                        continue

                    if shard_date < cutoff_date:
                        gzipped_shard_name = shard_path.name + ".gz"
                        gzipped_shard_path = archive_dir / gzipped_shard_name
                        try:
                            with open(shard_path, "rb") as f_in:
                                with gzip.open(gzipped_shard_path, "wb") as f_out:
                                    f_out.writelines(f_in)
                            shard_path.unlink() # Delete original
                            logger.info("Gzipped and archived shard: %s", shard_path.name)
                        except Exception as exc:
                            logger.error("Error gzipping shard %s: %s", shard_path.name, exc)
                            shards_to_update.append(shard_info) # Keep in index if error
                    else:
                        shards_to_update.append(shard_info)
                self._train_index["shards"] = shards_to_update
                self._save_train_index()
            logger.info("Spool compaction and archiving completed.")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._worker_tasks:
            for task in self._worker_tasks:
                task.cancel()
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
            self._worker_tasks = []
        if self._auto_crawl_task:
            self._auto_crawl_task.cancel()
            try:
                await self._auto_crawl_task
            except asyncio.CancelledError:
                pass
            self._auto_crawl_task = None
        await self._shared_state.flush()
        self._last_heartbeat = datetime.now(timezone.utc)
        # Crawlee manages its own client and playwright instances
        logger.info("Crawler manager stopped")

    async def crawl_url(self, url: str, *, keywords: Optional[List[str]] = None, max_pages: int = 10) -> CrawlJob:
        """
        Lightweight wrapper for orchestrator to crawl a single URL.

        Creates a job with sensible defaults for quick crawling and content extraction.
        Used by orchestration workflows (e.g., crawl_summarize_and_post).

        Args:
            url: The URL to crawl
            keywords: Optional keywords for relevance scoring (defaults to generic terms)
            max_pages: Maximum pages to crawl (default: 10)

        Returns:
            CrawlJob instance
        """
        if not keywords:
            keywords = ["tech", "news", "ai", "linux", "software"]

        return await self.create_job(
            keywords=keywords,
            seeds=[url],
            max_depth=2,
            max_pages=max_pages,
            allow_external=False,
            user_context="Orchestrator crawl",
            requested_by="orchestrator",
            priority="normal",
        )

    async def create_job(
        self,
        *,
        keywords: List[str],
        seeds: List[str],
        max_depth: int = 2,
        max_pages: int = 60,
        rate_limit: float = 1.0,
        relevance_threshold: float = 0.35,
        allow_external: bool = False,
        user_context: Optional[str] = None,
        requested_by: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
        allow_private_networks: bool = False,
        ollama_assisted: bool = False,
        ollama_query: Optional[str] = None,
        priority: str = "low", # New priority parameter
        idempotency_key: Optional[str] = None,
    ) -> CrawlJob:
        if not seeds:
            raise ValueError("At least one seed URL is required")

        # SECURITY: SSRF Protection - validate all seed URLs
        safe_seeds = []
        blocked_seeds = []
        for seed in seeds:
            is_safe, reason = is_ssrf_safe(seed, allow_private_networks=allow_private_networks)
            if is_safe:
                safe_seeds.append(seed)
            else:
                logger.warning("SECURITY: Blocked SSRF-unsafe seed URL: %s - %s", seed, reason)
                blocked_seeds.append({"url": seed, "reason": reason})

        if not safe_seeds:
            raise ValueError(f"All seed URLs were blocked for security reasons: {blocked_seeds}")

        if blocked_seeds:
            logger.warning(
                "SECURITY: %d of %d seed URLs were blocked: %s",
                len(blocked_seeds), len(seeds), blocked_seeds
            )

        # Use only safe seeds
        seeds = safe_seeds

        if idempotency_key:
            existing_id = await self._shared_state.get_job_for_key(idempotency_key)
            if existing_id:
                existing_job = await self.get_job(existing_id)
                if existing_job:
                    logger.debug(
                        "Returning existing job %s for idempotency key %s",
                        existing_id,
                        idempotency_key,
                    )
                    return existing_job
        normalized_keywords = [kw.strip() for kw in keywords if kw.strip()]
        normalized_seeds = [seed.strip() for seed in seeds if seed.strip()]
        allowed_domains = {urlparse(seed).netloc for seed in normalized_seeds}
        category = self._categorize_job(priority=priority, requested_by=requested_by)
        job_metadata = dict(metadata or {})
        job_metadata["allow_private_networks"] = bool(allow_private_networks)
        job = CrawlJob(
            id=str(uuid.uuid4()),
            keywords=normalized_keywords,
            seeds=normalized_seeds,
            max_depth=max(0, min(max_depth, 5)),
            max_pages=max(1, min(max_pages, 500)),
            allowed_domains=allowed_domains,
            allow_external=allow_external,
            relevance_threshold=max(0.1, min(relevance_threshold, 0.95)),
            rate_limit=max(0.1, min(rate_limit, 10.0)),
            user_context=user_context,
            requested_by=requested_by,
            metadata=job_metadata,
            ollama_assisted=ollama_assisted,
            ollama_query=ollama_query,
            priority=priority, # Pass priority to CrawlJob
            idempotency_key=idempotency_key,
            category=category,
        )
        async with self._lock:
            self._jobs[job.id] = job
        if priority == "high":
            await self._high_priority_job_queue.put(job.id)
            logger.debug("High-priority job %s added to queue. High-priority queue size: %d", job.id, self._high_priority_job_queue.qsize())
        else:
            await self._job_queue.put(job.id)
            logger.debug("Low-priority job %s added to queue. Low-priority queue size: %d", job.id, self._job_queue.qsize())
        logger.info("Crawler job %s (priority: %s) queued with %d seeds", job.id, priority, len(job.seeds))

        if idempotency_key:
            await self._shared_state.register_job_for_key(idempotency_key, job.id)

        for seed in job.seeds:
            await self._mark_url_seen(seed)

        await self.start()
        self._last_heartbeat = datetime.now(timezone.utc)

        return job

    async def list_jobs(self) -> List[CrawlJob]:
        async with self._lock:
            return list(self._jobs.values())

    async def metrics(self) -> dict[str, Any]:
        categories = await self._get_metrics_snapshot()
        return {
            "queue_depth": {
                "high_priority": self._high_priority_job_queue.qsize(),
                "low_priority": self._job_queue.qsize(),
                "total": self._high_priority_job_queue.qsize() + self._job_queue.qsize(),
            },
            "categories": categories,
            "last_heartbeat": self._last_heartbeat.isoformat(),
        }

    @property
    def last_heartbeat(self) -> datetime:
        return self._last_heartbeat

    def _categorize_job(self, *, priority: str, requested_by: Optional[str]) -> str:
        requested_by = (requested_by or "").lower()
        priority = priority.lower()
        if requested_by == "user" or priority == "high":
            return "user"
        if requested_by in {"auto_crawler", "auto"}:
            return "auto"
        return "background"

    async def _mark_url_seen(self, url: str) -> bool:
        normalized = url.strip()
        if not normalized:
            return False
        url_hash = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
        return await self._shared_state.mark_url_seen(url_hash)

    async def _should_visit(self, url: str) -> bool:
        normalized = url.strip()
        if not normalized:
            return False
        url_hash = hashlib.sha1(normalized.encode("utf-8")).hexdigest()
        return await self._shared_state.mark_url_seen(url_hash)

    async def _record_metric(self, category: str, *, success: bool, status: int = 200) -> None:
        async with self._metrics_lock:
            metrics = self._metrics_by_category[category]
            if success:
                metrics.record_success()
            else:
                metrics.record_failure(status)

    async def _get_metrics_snapshot(self) -> dict[str, Any]:
        async with self._metrics_lock:
            return {
                category: metric.snapshot()
                for category, metric in self._metrics_by_category.items()
            }

    async def _get_host_lock(self, host: str) -> asyncio.Lock:
        async with self._host_state_lock:
            lock = self._host_locks.get(host)
            if lock is None:
                lock = asyncio.Lock()
                self._host_locks[host] = lock
            return lock

    def _loop_time(self) -> float:
        loop = self._loop
        if loop is None or loop.is_closed():
            try:
                loop = asyncio.get_running_loop()
                self._loop = loop
            except RuntimeError:
                # Event loop not available, use monotonic time as fallback
                import time
                return time.monotonic()
        return loop.time()

    async def _respect_host_backoff(self, host: str) -> None:
        while True:
            async with self._host_state_lock:
                ready_at = self._host_backoff.get(host, 0.0)
            now = self._loop_time()
            if ready_at <= now:
                return
            await asyncio.sleep(ready_at - now)

    async def _schedule_backoff(self, host: str, status_code: int) -> None:
        increment = 10.0 if status_code == 429 else 5.0
        async with self._host_state_lock:
            now = self._loop_time()
            current = self._host_backoff.get(host, now)
            next_allowed = max(current, now) + increment
            # Cap backoff to 60 seconds to avoid total starvation
            self._host_backoff[host] = min(next_allowed, now + 60.0)

    async def _clear_backoff(self, host: str) -> None:
        async with self._host_state_lock:
            if host in self._host_backoff:
                self._host_backoff.pop(host, None)

    async def get_job(self, job_id: str) -> Optional[CrawlJob]:
        async with self._lock:
            return self._jobs.get(job_id)

    async def get_result(self, result_id: str) -> Optional[CrawlResult]:
        return await self._store.get(result_id)

    async def add_feedback(
        self,
        result_id: str,
        *,
        score: float,
        comment: Optional[str],
        confirmed: bool,
        source: str,
    ) -> Optional[CrawlResult]:
        result = await self._store.get(result_id)
        if not result:
            return None
        feedback = CrawlFeedback(score=max(0.0, min(score, 5.0)), comment=comment, source=source, confirmed=confirmed)
        result.ratings.append(feedback)
        result.rating_count = len(result.ratings)
        result.rating_average = sum(item.score for item in result.ratings) / max(1, result.rating_count)
        if confirmed:
            result.confirmations += 1
        result.updated_at = datetime.now(timezone.utc)
        await self._store.update(result)
        return result

    async def mark_posted(
        self,
        result_id: str,
        *,
        post_id: Optional[int],
        topic_id: Optional[int],
    ) -> Optional[CrawlResult]:
        result = await self._store.get(result_id)
        if not result:
            return None
        result.status = "published"
        result.posted_at = datetime.now(timezone.utc)
        result.post_id = post_id
        result.topic_id = topic_id
        result.updated_at = datetime.now(timezone.utc)
        await self._store.update(result)
        return result

    async def ready_for_publication(self, *, limit: int = 10, min_age_minutes: int = 60) -> List[CrawlResult]:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=min_age_minutes)

        def predicate(result: CrawlResult) -> bool:
            if result.status == "published":
                return False
            if result.rating_count < 2:
                return False
            if result.rating_average < 4.0:
                return False
            if result.confirmations < 1:
                return False
            if result.created_at > cutoff:
                return False
            return True

        results = await self._store.list(predicate)
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:limit]

    async def search(
        self,
        query: str,
        limit: int = 20,
        min_score: float = 0.35,
        freshness_days: int = 7,
    ) -> List[Dict[str, Any]]:
        all_results: List[Dict[str, Any]] = []
        query_tokens = query.lower().split()
        max_scan_docs = 10000  # Safety limit to prevent OOM

        # 1. Search RAM first
        async with self._store._lock:
            for result in self._store._records.values():
                if result.normalized_text:
                    all_results.append(asdict(result))

        # 2. Search disk shards
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=freshness_days)
        async with self._lock:
            for shard_info in self._train_index["shards"]:
                shard_path = self._train_dir / shard_info["name"]
                if not shard_path.exists():
                    continue

                try:
                    date_str = shard_info["name"].replace("crawl-train-", "").replace(".jsonl", "")
                    shard_date = datetime.strptime(date_str, "%Y%m%d-%H").replace(tzinfo=timezone.utc)
                    if shard_date < cutoff_date:
                        continue
                except ValueError:
                    continue

                with jsonlines.open(shard_path, mode="r") as reader:
                    for obj in reader:
                        # Reconstruct CrawlResult from dict for consistent processing
                        result = CrawlResult.from_dict(obj)
                        if result.normalized_text:
                            all_results.append(obj)
                        if len(all_results) >= max_scan_docs:
                            break
                if len(all_results) >= max_scan_docs:
                    break

        # 3. Rerank results (BM25)
        if not all_results:
            return []

        corpus = [res["normalized_text"] for res in all_results if res.get("normalized_text")]
        if not corpus:
            return []

        tokenized_corpus = [doc.lower().split() for doc in corpus]
        bm25 = BM25Okapi(tokenized_corpus)
        doc_scores = bm25.get_scores(query_tokens)

        scored_results = []
        for i, res in enumerate(all_results):
            res_score = doc_scores[i] # BM25 score
            # Combine with original score, if any, or just use BM25
            final_score = (res.get("score", 0.0) + res_score) / 2.0 if res.get("score") else res_score
            if final_score >= min_score:
                scored_results.append({
                    "url": res["url"],
                    "title": res["title"],
                    "excerpt": res["excerpt"],
                    "score": final_score,
                    "ts": res["created_at"],
                    "source_domain": res["source_domain"],
                })

        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:limit]

    async def _run_worker(self, worker_id: int) -> None:
        logger.info("Crawler worker %s has started.", worker_id)
        while not self._stop_event.is_set():
            self._last_heartbeat = datetime.now(timezone.utc)
            job_id = None
            source_queue: Optional[asyncio.Queue[str]] = None
            try:
                # Try to get a job from the high-priority queue first
                job_id = await asyncio.wait_for(self._high_priority_job_queue.get(), timeout=0.1)
                source_queue = self._high_priority_job_queue
                logger.debug("Worker %s retrieved high-priority job_id %s from queue.", worker_id, job_id)
            except asyncio.TimeoutError:
                pass # High-priority queue is empty, try low-priority

            if job_id is None:
                try:
                    # If high-priority queue is empty, get from low-priority queue
                    job_id = await asyncio.wait_for(self._job_queue.get(), timeout=1.0)
                    source_queue = self._job_queue
                    logger.debug("Worker %s retrieved low-priority job_id %s from queue.", worker_id, job_id)
                except asyncio.TimeoutError:
                    continue # Both queues empty, continue loop

            job = await self.get_job(job_id)
            if not job:
                logger.warning("Worker %s: job %s not found in _jobs, skipping.", worker_id, job_id)
                if source_queue:
                    source_queue.task_done()
                continue
            logger.debug("Worker %s processing job %s", worker_id, job.id)

            job.status = "running"
            job.updated_at = datetime.now(timezone.utc)
            await self._persist_job(job)

            async def local_request_handler(context: PlaywrightCrawlingContext) -> None:
                await self._process_request(context, job)

            try:
                logger.debug("Initializing PlaywrightCrawler for job %s", job.id)

                crawler = PlaywrightCrawler(
                    request_handler=local_request_handler,
                    max_request_retries=2,
                    max_requests_per_crawl=job.max_pages,
                    max_crawl_depth=job.max_depth,
                    headless=True,
                    request_handler_timeout=timedelta(seconds=300),
                )
                logger.debug("PlaywrightCrawler initialized for job %s", job.id)
            except Exception as exc:
                logger.error("Error initializing PlaywrightCrawler for job %s: %s", job.id, exc)
                job.status = "failed"
                job.error = f"Crawler initialization failed: {exc}"
                await self._persist_job(job)
                if source_queue:
                    source_queue.task_done()
                continue

            # Add seeds to Crawlee's request queue
            initial_requests = []
            logger.debug("Preparing initial requests for job %s.", job.id)
            for seed in job.seeds:
                try:
                    # Generate a unique ID for the request
                    request_id = hashlib.sha256(seed.encode('utf-8')).hexdigest()
                    initial_requests.append(Request(url=seed, uniqueKey=request_id, id=request_id, headers={"X-Crawl-Parent": "seed"}))
                except Exception as exc:
                    logger.error("Error creating Crawlee Request object for seed %s: %s", seed, exc)
            logger.debug("Initial requests prepared for job %s: %s", job.id, [req.url for req in initial_requests])

            try:
                logger.debug("Starting PlaywrightCrawler run for job %s with %d initial requests.", job.id, len(initial_requests))
                # Debug print removed - use logger.debug instead
                await asyncio.wait_for(crawler.run(initial_requests), timeout=300.0)
                logger.debug("PlaywrightCrawler run completed for job %s.", job.id)
                job.status = "completed"
                job.completed_at = datetime.now(timezone.utc)
                await self._persist_job(job)
            except asyncio.TimeoutError:
                logger.warning("Crawl job %s timed out after 300 seconds - marking as partial complete.", job.id)
                job.status = "partial_complete"
                job.error = "Crawl timed out after 300 seconds (partial results saved)."
                job.completed_at = datetime.now(timezone.utc)
                await self._persist_job(job)
            except playwright._impl._errors.Error as exc:
                logger.error("Playwright error during crawl for job %s: %s", job.id, exc, exc_info=True)
                job.status = "failed"
                job.error = f"Playwright error ({type(exc).__name__}): {str(exc)}"
                job.completed_at = datetime.now(timezone.utc)
                await self._persist_job(job)
            except Exception as exc:
                logger.error("Error during PlaywrightCrawler run for job %s: %s", job.id, exc, exc_info=True)
                job.status = "failed"
                job.error = f"Crawler run failed: {exc}"
                job.completed_at = datetime.now(timezone.utc)
                await self._persist_job(job)
            
            if source_queue:
                source_queue.task_done()

    async def _run_auto_crawler(self) -> None:
        while not self._stop_event.is_set():
            await asyncio.sleep(86400) # Run every 24 hours
            logger.info("Starting automatic crawl for AI news and tech topics.")
            
            keywords = ["ai", "artificial intelligence", "machine learning", "deep learning", "linux", "windows", "software", "programming", "coding"]
            seeds = [
                "https://www.artificialintelligence-news.com/",
                "https://www.sciencedaily.com/news/computers_math/artificial_intelligence/",
                "https://www.technologyreview.com/tag/artificial-intelligence/",
                "https://venturebeat.com/category/ai/",
                "https://www.theverge.com/tech",
                "https://techcrunch.com/",
                "https://www.wired.com/category/technology/",
                "https://arstechnica.com/gadgets/",
                "https://news.ycombinator.com/",
                "https://dev.to/",
                "https://www.phoronix.com/"
            ]
            
            await self.create_job(
                keywords=keywords,
                seeds=seeds,
                max_depth=3,
                max_pages=100,
                allow_external=True,
                requested_by="auto_crawler",
                user_context="Automatic 24/7 crawl for AI news and tech topics.",
                ollama_assisted=True,
                ollama_query="Extract key information about AI, Linux, Windows, software, and programming.",
                priority="low", # Explicitly set priority to low for auto-crawler jobs
            )

    async def _persist_job(self, job: CrawlJob) -> None:
        async with self._lock:
            self._jobs[job.id] = job

    async def _process_request(self, context: PlaywrightCrawlingContext, job: CrawlJob) -> None:
        url = context.request.url
        logger.debug("Processing URL %s for job %s", url, job.id)
        host = urlparse(url).netloc or "unknown"
        status = 0

        self._last_heartbeat = datetime.now(timezone.utc)

        # Get response from context (fix for undefined variable bug)
        response = getattr(context, 'response', None)

        host_lock = await self._get_host_lock(host)
        async with host_lock:
            await self._respect_host_backoff(host)

            # Add randomized delay based on job.rate_limit with jitter
            delay = job.rate_limit + self._random.uniform(0, job.rate_limit * 0.5)
            await asyncio.sleep(delay)

            if not response:
                logger.warning("No response object available for %s - skipping", url)
                await self._record_metric(job.category, success=False, status=0)
                return

            status = response.status

            if status >= 500:
                logger.error("Server error (%d) at %s - skipping", status, url)
                await self._record_metric(job.category, success=False, status=status)
                return

            if status == 429:
                logger.warning("Received throttling status %d for %s - backing off", status, url)
                await self._schedule_backoff(host, status)
                await self._record_metric(job.category, success=False, status=status)
                return

            if status >= 400:
                logger.warning("Client error (%d) at %s - skipping", status, url)
                await self._record_metric(job.category, success=False, status=status)
                return

            content_type = response.headers.get("content-type", "").lower()
            if "text/html" not in content_type:
                logger.info("Skipping non-HTML content at %s (type: %s)", url, content_type)
                await self._record_metric(job.category, success=False, status=status)
                return

            # Set realistic browser properties to avoid detection
            try:
                await context.page.evaluate("""
                    () => {
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined
                        });
                        Object.defineProperty(navigator, 'plugins', {
                            get: () => [1, 2, 3, 4, 5]
                        });
                        Object.defineProperty(navigator, 'languages', {
                            get: () => ['en-US', 'en']
                        });
                        window.chrome = {
                            runtime: {}
                        };
                    }
                """)
            except Exception as exc:
                logger.debug("Could not set stealth properties for %s: %s", url, exc)

            # Handle cookie consent with multiple selectors
            cookie_selectors = [
                'button:has-text("Accept All")',
                'button:has-text("Alle akzeptieren")',
                'button:has-text("Accept")',
                'button:has-text("Akzeptieren")',
                'button[id*="accept"]',
                'button[class*="accept"]',
                'a:has-text("Accept All")',
                '#cookie-accept',
                '.cookie-accept',
            ]

            for selector in cookie_selectors:
                try:
                    await context.page.click(selector, timeout=3000)
                    logger.debug("Clicked cookie banner using selector: %s", selector)
                    break
                except playwright._impl._errors.TimeoutError:
                    continue
                except Exception as exc:
                    logger.debug("Error clicking selector %s on %s: %s", selector, url, exc)
                    continue

            try:
                html = await context.page.content()
                soup = BeautifulSoup(html, "html.parser")
                text_content = self._extract_text(soup)
                logger.debug("Extracted %d characters from %s", len(text_content), url)
            except Exception as exc:
                logger.error("Error extracting content from %s: %s", url, exc, exc_info=True)
                await self._record_metric(job.category, success=False, status=status)
                return

            try:
                score, matched_keywords = self._score_content(text_content, job.keywords)
                logger.debug("Content score for %s: %.2f (matched: %s)", url, score, matched_keywords)
            except Exception as exc:
                logger.error("Error scoring content from %s: %s", url, exc, exc_info=True)
                score = 0.0
                matched_keywords = []

            extracted_content_ollama = None
            relevance_score = 0.0
            if job.ollama_assisted and job.ollama_query:
                try:
                    ollama_analysis = await self._ollama_analyze_content(text_content, job.ollama_query)
                    relevance_score = ollama_analysis.get("relevance_score", 0.0)
                    extracted_content_ollama = ollama_analysis.get("extracted_content")
                    if relevance_score > 0:
                        score = (score + relevance_score) / 2.0
                        logger.debug("Ollama-adjusted score for %s: %.2f", url, score)
                except Exception as exc:
                    logger.warning("Ollama analysis failed for %s: %s (continuing)", url, exc)

            if score >= job.relevance_threshold:
                result = await self._build_result(
                    job=job,
                    url=url,
                    parent_url=context.request.headers.get("X-Crawl-Parent"),
                    depth=context.request.user_data.get("depth", 0) if context.request.user_data else 0,
                    soup=soup,
                    text_content=text_content,
                    score=score,
                    matched_keywords=matched_keywords,
                    extracted_content_ollama=extracted_content_ollama,
                )
                await self._store.add(result)
                self._train_buffer.append(result)
                if len(self._train_buffer) >= self._train_buffer_max_size:
                    # Trigger background flush
                    asyncio.create_task(self.flush_to_jsonl())

                job.results.append(result.id)
                logger.info("Stored relevant result %s (score %.2f)", result.url, result.score)

            job.pages_crawled += 1
            job.updated_at = datetime.now(timezone.utc)
            await self._persist_job(job)

            if job.pages_crawled < job.max_pages:
                try:
                    links = await self._extract_links(context, job)
                    remaining_slots = job.max_pages - job.pages_crawled
                    links_to_enqueue = links[:min(len(links), remaining_slots)]

                    logger.debug(
                        "Found %d links at %s, enqueueing %d (remaining slots: %d)",
                        len(links),
                        url,
                        len(links_to_enqueue),
                        remaining_slots,
                    )

                    for link in links_to_enqueue:
                        try:
                            request_id = hashlib.sha256(link.encode('utf-8')).hexdigest()
                            new_request = Request(
                                url=link,
                                uniqueKey=request_id,
                                id=request_id,
                                headers={"X-Crawl-Parent": url},
                                user_data={
                                    "depth": context.request.user_data.get("depth", 0) + 1 if context.request.user_data else 1
                                },
                            )
                            await context.add_requests([new_request])
                        except Exception as exc:
                            logger.warning("Failed to enqueue link %s: %s", link, exc)
                except Exception as exc:
                    logger.error("Error extracting links from %s: %s", url, exc, exc_info=True)

            await self._record_metric(job.category, success=True, status=status)
            await self._clear_backoff(host)



    @staticmethod
    def _extract_text(soup: BeautifulSoup) -> str:
        for selector in ARTICLE_SELECTORS:
            node = soup.select_one(selector)
            if node:
                return node.get_text(separator=" ", strip=True)
        paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all("p")]
        return " ".join(paragraphs)

    @staticmethod
    def _extract_title(soup: BeautifulSoup) -> str:
        if soup.title and soup.title.string:
            return soup.title.get_text(strip=True)
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)
        return "Untitled Document"

    @staticmethod
    def _extract_meta_description(soup: BeautifulSoup) -> Optional[str]:
        for key in RELEVANT_META_KEYS:
            node = soup.find("meta", attrs={"name": key}) or soup.find("meta", property=key)
            if node and node.get("content"):
                return node["content"].strip()
        return None

    @staticmethod
    def _extract_publish_date(soup: BeautifulSoup) -> Optional[str]:
        for key in PUBLISH_META_KEYS:
            node = soup.find("meta", attrs={"name": key}) or soup.find("meta", property=key)
            if node and node.get("content"):
                try:
                    dt = dateparser.parse(node["content"])
                    if dt:
                        return dt.astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError):
                    continue
        time_node = soup.find("time")
        if time_node and time_node.get("datetime"):
            try:
                dt = dateparser.parse(time_node["datetime"])
                if dt:
                    return dt.astimezone(timezone.utc).isoformat()
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _build_excerpt(text_content: str, *, max_length: int = 420) -> str:
        clean = re.sub(r"\\s+", " ", text_content).strip()
        if len(clean) <= max_length:
            return clean
        return clean[: max_length - 3].rstrip() + "..."

    def _guess_tags(self, matched_keywords: Iterable[str], additional: Optional[Iterable[str]]) -> List[str]:
        tags = set(keyword.lower() for keyword in matched_keywords)
        if additional:
            tags.update(str(tag).lower() for tag in additional)
        return sorted(tags)

    async def _extract_links(self, context: PlaywrightCrawlingContext, job: CrawlJob) -> List[str]:
        links: List[str] = []
        excluded_keywords = [
            "login", "register", "signin", "signup", "admin", "cart", "checkout",
            "facebook.com", "twitter.com", "linkedin.com", "instagram.com", "pinterest.com",
            "youtube.com", "reddit.com", "addtoany.com", "sharethis.com", "mailto:", "tel:",
            "whatsapp.com", "t.me"
        ]
        
        # Use Playwright to get all anchor tags
        anchors = await context.page.locator("a").all()
        for anchor in anchors:
            href = await anchor.get_attribute("href")
            if not href:
                continue
            href = href.strip()
            
            if any(keyword in href.lower() for keyword in excluded_keywords):
                continue

            absolute = urljoin(context.request.url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            if not job.allow_external and parsed.netloc not in job.allowed_domains:
                continue

            # SECURITY: SSRF check for discovered links
            is_safe, reason = is_ssrf_safe(
                absolute,
                allow_private_networks=bool(job.metadata.get("allow_private_networks", False)),
            )
            if not is_safe:
                logger.debug("SECURITY: Skipping SSRF-unsafe link: %s - %s", absolute, reason)
                continue

            if await self._should_visit(absolute):
                links.append(absolute)
        return links

    def _score_content(self, text: str, keywords: List[str]) -> Tuple[float, List[str]]:
        if not keywords:
            return 0.0, []
        text_lower = text.lower()
        matched = [keyword for keyword in keywords if keyword.lower() in text_lower]
        score = len(matched) / len(keywords)
        return score, matched

    async def _ollama_analyze_content(self, text: str, query: str) -> Dict[str, Any]:
        settings = get_settings()
        model_name = getattr(settings, "crawler_ollama_model", None) # New setting for Ollama model
        if not model_name or not registry or not chat_service:
            logger.warning("Ollama model for crawler not configured or chat service unavailable.")
            return {"relevance_score": 0.0, "extracted_content": None, "suggested_links": []}

        try:
            model = await registry.get_model(model_name)
        except Exception as exc:
            logger.warning("Ollama model lookup failed: %s", exc)
            return {"relevance_score": 0.0, "extracted_content": None, "suggested_links": []}

        if model and "chat" in model.capabilities:
            try:
                # Prompt for relevance and content extraction
                prompt = (
                    f"Analyze the following text for its relevance to the query: '{query}'. "
                    "Provide a relevance score between 0.0 and 1.0. "
                    "If the query asks for specific content, extract that content. "
                    "Also, identify any highly relevant URLs within the text that could be further crawled. "
                    "Return a JSON object with 'relevance_score' (float), 'extracted_content' (string, or null), and 'suggested_links' (list of strings)."
                    f"\\n\\nText: {text[:8000]}" # Limit text to avoid exceeding context window
                )
                messages = [
                    {"role": "system", "content": "You are an intelligent content analyzer."},
                    {"role": "user", "content": prompt},
                ]
                chunks: List[str] = []
                async for chunk in chat_service.stream_chat(
                    model,
                    model_name,
                    messages,
                    stream=True,
                    temperature=0.2,
                ):
                    chunks.append(chunk)
                ollama_response_text = "".join(chunks).strip()

                try:
                    ollama_response = json.loads(ollama_response_text)
                    return {
                        "relevance_score": ollama_response.get("relevance_score", 0.0),
                        "extracted_content": ollama_response.get("extracted_content"),
                        "suggested_links": ollama_response.get("suggested_links", []),
                    }
                except json.JSONDecodeError:
                    logger.warning("Ollama returned malformed JSON: %s", ollama_response_text)
                    # Fallback: try to infer relevance from text if JSON parsing fails
                    if query.lower() in ollama_response_text.lower():
                        return {"relevance_score": 0.5, "extracted_content": ollama_response_text, "suggested_links": []}
                    return {"relevance_score": 0.0, "extracted_content": ollama_response_text, "suggested_links": []}

            except Exception as exc:
                logger.warning("Ollama content analysis failed: %s", exc)
        return {"relevance_score": 0.0, "extracted_content": None, "suggested_links": []}

    async def _generate_summary(self, text: str, meta_description: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        settings = get_settings()
        model_name = getattr(settings, "crawler_summary_model", None)
        if not text:
            return None, meta_description
        if model_name and registry and chat_service:
            try:
                model = await registry.get_model(model_name)
            except Exception as exc:  # pragma: no cover
                logger.warning("Crawler summary model lookup failed: %s", exc)
                model = None
            if model and "chat" in model.capabilities:
                try:
                    messages = [
                        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": text[:6000]},
                    ]
                    chunks: List[str] = []
                    async for chunk in chat_service.stream_chat(
                        model,
                        model_name,
                        messages,
                        stream=True,
                        temperature=0.2,
                    ):
                        chunks.append(chunk)
                    summary_text = "".join(chunks).strip()
                    if summary_text:
                        headline, bullet_summary = self._split_summary(summary_text)
                        return headline, bullet_summary
                except Exception as exc:  # pragma: no cover
                    logger.warning("Crawler summary generation failed: %s", exc)
        # Fallback summary
        fallback = meta_description or self._build_excerpt(text, max_length=360)
        headline = None
        return headline, fallback

    @staticmethod
    def _split_summary(summary_text: str) -> Tuple[Optional[str], Optional[str]]:
        lines = [line.strip() for line in summary_text.splitlines() if line.strip()]
        if not lines:
            return None, None
        headline = lines[0][:120]
        body = "\n".join(lines[1:]) if len(lines) > 1 else None
        return headline, body

    @staticmethod
    def _normalize_text(soup: BeautifulSoup) -> str:
        # Remove script and style elements
        for script_or_style in soup(["script", "style"]):
            script_or_style.extract()

        # Remove navigation and footer elements (common patterns)
        for unwanted_tag in soup.find_all(["nav", "footer", "aside"]):
            unwanted_tag.extract()

        # Get text, preserving paragraph breaks and headings
        text_parts = []
        for element in soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6", "li"]):
            text_parts.append(element.get_text(separator=" ", strip=True))

        # Join parts and collapse multiple whitespaces
        full_text = "\n".join(text_parts)
        return re.sub(r"\\s+", " ", full_text).strip()

    async def _build_result(
        self,
        *,
        job: CrawlJob,
        url: str,
        parent_url: Optional[str],
        depth: int,
        soup: BeautifulSoup,
        text_content: str,
        score: float,
        matched_keywords: List[str],
        extracted_content_ollama: Optional[str],
    ) -> CrawlResult:
        """Build a CrawlResult with all necessary metadata and content."""
        try:
            # Extract metadata
            title = self._extract_title(soup)
            meta_description = self._extract_meta_description(soup)
            publish_date = self._extract_publish_date(soup)
            excerpt = self._build_excerpt(text_content)
            normalized_text = self._normalize_text(soup)

            # Generate summary if configured
            headline, summary = await self._generate_summary(text_content, meta_description)

            # Calculate content hash for deduplication
            content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()

            # Extract source domain
            source_domain = urlparse(url).netloc

            # Generate tags
            tags = self._guess_tags(matched_keywords, job.metadata.get("tags"))

            # Estimate token count (rough approximation: 1 token ≈ 4 chars)
            tokens_est = len(normalized_text) // 4

            # Create result object
            result = CrawlResult(
                id=str(uuid.uuid4()),
                job_id=job.id,
                url=url,
                depth=depth,
                parent_url=parent_url,
                status="crawled",
                title=title,
                summary=summary,
                headline=headline,
                content=text_content,
                excerpt=excerpt,
                meta_description=meta_description,
                keywords_matched=matched_keywords,
                score=score,
                publish_date=publish_date,
                tags=tags,
                normalized_text=normalized_text,
                content_hash=content_hash,
                source_domain=source_domain,
                tokens_est=tokens_est,
                extracted_content_ollama=extracted_content_ollama,
            )

            logger.debug("Built result for %s: title='%s', score=%.2f, tokens=%d",
                        url, title, score, tokens_est)
            return result

        except Exception as exc:
            logger.error("Error building result for %s: %s", url, exc, exc_info=True)
            # Return minimal result on error
            return CrawlResult(
                id=str(uuid.uuid4()),
                job_id=job.id,
                url=url,
                depth=depth,
                parent_url=parent_url,
                status="error",
                title="Error Processing Page",
                summary=None,
                headline=None,
                content=text_content[:1000] if text_content else "",
                excerpt=f"Error: {str(exc)}",
                meta_description=None,
                keywords_matched=matched_keywords,
                score=score,
                publish_date=None,
            )


crawler_manager = CrawlerManager(shared_state=shared_crawler_state, instance_name="default")
