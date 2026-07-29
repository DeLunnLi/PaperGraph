
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from ...settings import get_settings
import contextlib

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_daily_compute_lock: asyncio.Lock | None = None
_daily_empty_results: dict[tuple[str, int], int] = {}
_DAILY_EMPTY_RETRY_LIMIT = 3

def get_daily_compute_lock() -> asyncio.Lock:
    global _daily_compute_lock
    if _daily_compute_lock is None:
        _daily_compute_lock = asyncio.Lock()
    return _daily_compute_lock

_EXCLUDE_MEANINGFUL_PREFIXES: tuple[str, ...] = (
    "/health",
    "/api/papers/meta/summary",
    "/api/papers/reading/calendar",
)

def request_updates_meaningful_activity(method: str, path: str) -> bool:
    p = path or ""
    if p in ("/", "/health"):
        return False
    for pref in _EXCLUDE_MEANINGFUL_PREFIXES:
        if p.startswith(pref):
            return False
    return not (method.upper() == "GET" and p.startswith("/api/papers/daily"))

def touch_meaningful_activity_if_needed(app: FastAPI, method: str, path: str) -> None:
    if not request_updates_meaningful_activity(method, path):
        return
    with contextlib.suppress(Exception):
        app.state.last_meaningful_activity_monotonic = time.monotonic()

async def daily_auto_refresh_loop(app: FastAPI) -> None:
    s = get_settings()
    if not s.papergraph_daily_auto_refresh:
        logger.info("每日论文后台自动刷新已关闭（PAPERGRAPH_DAILY_AUTO_REFRESH=0）")
        return

    idle = max(15, s.papergraph_daily_auto_refresh_idle_sec)
    poll = max(30, s.papergraph_daily_auto_refresh_poll_sec)
    grace = max(10, s.papergraph_daily_auto_refresh_startup_grace_sec)
    logger.info("每日论文后台自动刷新已启用：idle=%ss poll=%ss startup_grace=%ss", idle, poll, grace)
    await asyncio.sleep(grace)

    from starlette.concurrency import run_in_threadpool
    from ...api.dependencies import get_db_path, get_searcher
    from ...models.schemas import DailyPapersRequest
    from ...services.daily.daily_cache_store import get_cache
    from ...services.daily.daily_service import compute_daily_papers as compute_daily
    import datetime as _dt
    from ...services.papers.papers_helpers import daily_paper_identity_sig

    def _cache_nonempty(cached) -> bool:
        if not cached:
            return False
        try:
            return bool(cached.get("arxiv_selected") or []) or bool(cached.get("personalized") or [])
        except Exception:
            return False

    lock = get_daily_compute_lock()

    while True:
        try:
            await asyncio.sleep(poll)
            ts = getattr(app.state, "last_meaningful_activity_monotonic", None)
            if ts is not None and time.monotonic() - ts < idle:
                continue
            db_path = get_db_path()
            date_key = _dt.datetime.now().strftime("%Y-%m-%d")
            import sqlite3

            def _user_ids() -> list[int]:
                with sqlite3.connect(db_path) as conn:
                    return [int(row[0]) for row in conn.execute("SELECT id FROM users ORDER BY id").fetchall()]

            user_ids = await run_in_threadpool(_user_ids)
            pending_user_id = None
            for uid in user_ids:
                if _daily_empty_results.get((date_key, uid), 0) >= _DAILY_EMPTY_RETRY_LIMIT:
                    continue
                cached = await run_in_threadpool(get_cache, db_path, date_key=date_key, cache_key=f"user:{uid}")
                if not _cache_nonempty(cached):
                    pending_user_id = uid
                    break
            if pending_user_id is None or lock.locked():
                continue

            async with lock:
                cached = await run_in_threadpool(
                    get_cache, db_path, date_key=date_key, cache_key=f"user:{pending_user_id}"
                )
                if _cache_nonempty(cached):
                    continue
                ts2 = getattr(app.state, "last_meaningful_activity_monotonic", None)
                if ts2 is not None and time.monotonic() - ts2 < idle:
                    continue
                settings = get_settings()
                from ...services.papers import papers_converters
                body = DailyPapersRequest(force_refresh=False)
                logger.info("每日论文：后台自动拉取开始（当日无有效缓存且系统空闲）")
                resp = await compute_daily(
                    body=body, db_path=db_path, searcher=get_searcher(),
                    daily_paper_identity_sig_fn=daily_paper_identity_sig,
                    daily_arxiv_cs_categories=settings.get_daily_arxiv_cs_categories(),
                    papergraph_to_api_fn=papers_converters.litpaper_to_api_paper,
                    logger=logger,
                    user_id=pending_user_id,
                )
                result_count = len(resp.arxiv_selected or []) + len(resp.personalized or [])
                retry_key = (date_key, pending_user_id)
                if result_count:
                    _daily_empty_results.pop(retry_key, None)
                else:
                    failures = _daily_empty_results.get(retry_key, 0) + 1
                    _daily_empty_results[retry_key] = failures
                    logger.warning(
                        "每日论文：后台拉取返回0篇 date=%s user=%s attempt=%s/%s",
                        date_key, pending_user_id, failures, _DAILY_EMPTY_RETRY_LIMIT,
                    )
                logger.info("每日论文：后台自动拉取完成 result_count=%s", result_count)
        except asyncio.CancelledError:
            logger.info("每日论文后台自动刷新任务已取消")
            raise
        except Exception:
            logger.exception("每日论文后台自动拉取失败（将按 poll 间隔重试）")

def spawn_daily_auto_refresh(app: FastAPI) -> asyncio.Task:
    return asyncio.create_task(daily_auto_refresh_loop(app), name="papergraph_daily_auto_refresh")
