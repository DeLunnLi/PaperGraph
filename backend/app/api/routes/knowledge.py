"""Self-evolving knowledge base — read-only user-facing API.

Exposes the two memory DBs (reader_memory.db + agent_memory.db) with their
evolution instrumentation (utility / access_count / last_used) and the
evolution log timeline. Reader memories use synthetic ``papergraph:*``
identities (not per-app-user), consistent with the reader-memory model, so
the page shows the whole knowledge base behind ``require_user`` auth.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, time as day_time, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from ...services.memory.db_paths import memory_db_paths
from ...services.memory.sqlite_document_store_compat import SQLiteDocumentStore
from ...utils.common import route_errors
from ..dependencies import get_db_path
from ..deps import require_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["知识库"])


def _paper_titles(db_path: str, paper_ids: list[int]) -> dict[int, str]:
    """Batch-resolve paper titles for paper-scoped memory items."""
    ids = sorted({int(i) for i in paper_ids if i})
    if not ids:
        return {}
    try:
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                f"SELECT id, title FROM papers WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.debug("knowledge._paper_titles failed", exc_info=True)
        return {}
    return {int(r[0]): str(r[1] or "") for r in rows}


def _scope_of(user_id: str) -> tuple[str, int | None]:
    """Map a synthetic memory user_id to (scope, paper_id|None)."""
    uid = str(user_id or "")
    if uid == "papergraph:global":
        return "global", None
    if uid.startswith("papergraph:paper:"):
        try:
            return "paper", int(uid.rsplit(":", 1)[1])
        except (ValueError, IndexError):
            return "paper", None
    # agent memories (papergraph:agent:* / papergraph:shared) — also the
    # fallthrough: the memory DBs only contain papergraph:* identities.
    return "agent", None


def _serialize(doc: dict[str, Any], *, source_db: str) -> dict[str, Any]:
    scope, pid = _scope_of(str(doc.get("user_id") or ""))
    content = str(doc.get("content") or "")
    return {
        "memory_id": str(doc.get("memory_id") or ""),
        "source_db": source_db,
        "scope": scope,
        "paper_id": pid,
        "kind": str(doc.get("memory_type") or "working"),
        "content": content[:400],
        "importance": float(doc["importance"] if doc.get("importance") is not None else 0.5),
        "utility": float(doc.get("utility") or 0.0),
        "access_count": int(doc.get("access_count") or 0),
        "last_used": float(doc.get("last_used") or 0.0),
        "timestamp": float(doc.get("timestamp") or 0),
    }


def _stores(scope: str = "all") -> list[tuple[str, SQLiteDocumentStore]]:
    wanted_dbs = ("reader", "agent") if scope != "agent" else ("agent",)
    paths = memory_db_paths()
    return [(key, SQLiteDocumentStore(paths[key])) for key in wanted_dbs if os.path.exists(paths[key])]


def _sort_key(item: dict[str, Any], sort: str) -> tuple:
    field = {"utility": "utility", "access": "access_count", "importance": "importance", "recency": "timestamp"}[sort]
    return item[field], item["timestamp"], item["memory_id"], item["source_db"]


def _collect(scope: str, paper_id: int | None, kind: str | None, *, sort: str = "recency", limit: int = 100) -> list[dict[str, Any]]:
    # The global top N is contained in the union of each database's top N.
    # Scope/kind filtering must precede LIMIT, including for old paper notes.
    out = [
        _serialize(doc, source_db=key)
        for key, store in _stores(scope)
        for doc in store.list_all_memories(
            memory_type=kind or "", scope=scope, paper_id=paper_id, sort=sort, limit=limit,
        )
    ]
    out.sort(key=lambda item: _sort_key(item, sort), reverse=True)
    return out[:limit]


@router.get("/memories")
async def list_knowledge_memories(
    scope: Literal["global", "paper", "agent", "all"] = "all",
    paper_id: int | None = Query(default=None, ge=1),
    kind: str | None = Query(default=None),
    sort: Literal["utility", "access", "recency", "importance"] = "recency",
    limit: int = Query(default=100, ge=1, le=500),
    db_path: str = Depends(get_db_path),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    with route_errors("knowledge_memories"):
        # sqlite reads stay off the event loop (collect + title join together)
        def _work() -> dict[str, Any]:
            items = _collect(scope, paper_id, kind, sort=sort, limit=int(limit))
            total = sum(store.count_memories(scope=scope, paper_id=paper_id, memory_type=kind or "") for _, store in _stores(scope))
            titles = _paper_titles(db_path, [it["paper_id"] for it in items if it["paper_id"]])
            for it in items:
                it["paper_title"] = titles.get(it["paper_id"] or 0) or None
            return {"success": True, "total": total, "items": items}

        return await run_in_threadpool(_work)


def _stats_payload(time_zone: str = "UTC") -> dict[str, Any]:
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="time_zone 必须为有效的 IANA 时区")
    now = time.time()
    today = datetime.fromtimestamp(now, zone).date()
    dates = [today - timedelta(days=days) for days in range(6, -1, -1)]
    day_ranges = [
        (day.isoformat(), datetime.combine(day, day_time.min, zone).timestamp(),
         datetime.combine(day + timedelta(days=1), day_time.min, zone).timestamp())
        for day in dates
    ]
    activity = {day.isoformat(): 0 for day in dates}
    by_kind: dict[str, int] = {}
    total, total_accesses, utility_sum = 0, 0, 0.0
    utility_distribution = {"negative": 0, "neutral": 0, "positive": 0}
    top_accessed = _collect("all", None, None, sort="access", limit=5)
    day_ago = now - 86400
    week_ago = now - 7 * 86400
    events_24h = 0
    feedback_counts: dict[str, int] = {}
    evolution_7d: dict[str, int] = {}
    for _, store in _stores():
        stats = store.memory_statistics()
        total += stats["total"]
        total_accesses += stats["total_accesses"]
        utility_sum += stats["utility_sum"]
        for kind, count in stats["by_kind"].items():
            by_kind[kind] = by_kind.get(kind, 0) + count
        for bucket, count in stats["utility_distribution"].items():
            utility_distribution[bucket] += count
        for day, count in store.evolution_day_counts(day_ranges).items():
            activity[day] += count
        events_24h += store.count_evolution_events_since(day_ago)
        for ev, n in store.evolution_event_counts().items():
            if ev in ("feedback_up", "feedback_down", "citation_click", "engaged_read"):
                feedback_counts[ev] = feedback_counts.get(ev, 0) + n
        for ev, n in store.evolution_event_counts(since_ts=week_ago).items():
            evolution_7d[ev] = evolution_7d.get(ev, 0) + n
    ups = feedback_counts.get("feedback_up", 0)
    downs = feedback_counts.get("feedback_down", 0)
    return {
        "success": True,
        "total": total,
        "by_kind": by_kind,
        "avg_utility": (utility_sum / total) if total else 0.0,
        "total_accesses": total_accesses,
        "top_accessed": [
            {"memory_id": t["memory_id"], "content": t["content"][:120],
             "access_count": t["access_count"], "kind": t["kind"]}
            for t in top_accessed
        ],
        "evolution_events_24h": events_24h,
        "activity_7d": [{"date": day, "count": count} for day, count in activity.items()],
        # 进化健康度
        "health": {
            "feedback": {
                "up": ups,
                "down": downs,
                "citation_click": feedback_counts.get("citation_click", 0),
                "engaged_read": feedback_counts.get("engaged_read", 0),
                "up_ratio": (ups / (ups + downs)) if (ups + downs) else None,
            },
            "utility_distribution": utility_distribution,
            "evolution_7d": evolution_7d,
        },
    }


@router.get("/stats")
async def knowledge_stats(time_zone: str = "UTC", user: dict = Depends(require_user)) -> dict[str, Any]:
    with route_errors("knowledge_stats"):
        return await run_in_threadpool(_stats_payload, time_zone)


def _evolution_entries(limit: int) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for db_key, path in memory_db_paths().items():
        if not os.path.exists(path):
            continue
        for e in SQLiteDocumentStore(path).list_evolution_log(limit=int(limit)):
            e["source_db"] = db_key
            entries.append(e)
    entries.sort(key=lambda e: float(e.get("created_at") or 0), reverse=True)
    return {"success": True, "total": len(entries), "items": entries[: int(limit)]}


@router.get("/evolution")
async def knowledge_evolution(
    limit: int = Query(default=50, ge=1, le=500),
    user: dict = Depends(require_user),
) -> dict[str, Any]:
    with route_errors("knowledge_evolution"):
        return await run_in_threadpool(_evolution_entries, int(limit))
