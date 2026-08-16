"""Periodic self-evolution of the memory DBs — LLM-judged keep/merge/prune.

Candidate gathering is instrumentation-based (old + never retrieved), but the
keep / merge / prune decision itself is made by the model
(:func:`memory_judge.judge_evolution_batch`) — there is no hardcoded scoring
threshold or multiplicative decay. Singleton kinds (``preference`` /
``paper_summary``) are never candidates: they are safety-critical user state.

Every applied decision is written to ``memory_evolution_log`` so the
knowledge-base page can render the evolution timeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import FastAPI

from ...settings import get_settings
from .sqlite_document_store_compat import SQLiteDocumentStore

logger = logging.getLogger(__name__)

# Singleton kinds + reflections must never enter the evolution review:
# pruning a reflection would reset the reflection activity gate and re-derive
# near-identical meta-memories at full LLM cost every cycle (churn loop).
_NEVER_REVIEW_KINDS = ("preference", "paper_summary", "reflection")


def evolve_memory_db(*, db_path: str) -> dict[str, Any]:
    """Run one LLM-judged evolution cycle over the memory DB at ``db_path``.

    Returns stats: candidates / kept / revised / merged / pruned / skipped.
    """
    s = get_settings()
    batch = max(4, int(s.memory_evolution_batch_size))
    min_age = max(1, int(s.memory_evolution_min_age_days))
    stats: dict[str, Any] = {
        "db_path": db_path, "candidates": 0, "kept": 0, "revised": 0,
        "merged": 0, "pruned": 0, "rescued": 0, "skipped": False,
    }
    if not os.path.exists(db_path):
        return stats

    store = SQLiteDocumentStore(db_path)
    candidates = store.list_stale_memories(
        min_age_days=min_age,
        exclude_types=_NEVER_REVIEW_KINDS,
        limit=batch,
    )
    stats["candidates"] = len(candidates)
    if not candidates:
        return stats

    from .memory_judge import judge_evolution_batch

    # Self-iteration: feed the judge its past-decision scorecard so it can
    # self-calibrate (rescue/reflection hit rate, prune over-aggression).
    scorecard = store.judge_scorecard(lookback_days=30)
    decisions = judge_evolution_batch(candidates=candidates, scorecard=scorecard)
    if not decisions:
        # Judge unavailable — skip this cycle entirely (no deletions without
        # a model decision).
        stats["skipped"] = True
        return stats

    by_id = {str(d.get("memory_id")): d for d in candidates}
    for dec in decisions:
        mid = str(dec.get("memory_id") or "")
        doc = by_id.get(mid)
        if not doc:
            continue
        action = str(dec.get("action") or "keep")
        reason = str(dec.get("reason") or "")[:200]
        imp_after = float(doc.get("importance") or 0.5)
        util_after = float(doc.get("utility") or 0.0)
        acc_after = int(doc.get("access_count") or 0)

        if action == "prune":
            if store.delete_memory(mid):
                stats["pruned"] += 1
                store.log_evolution(
                    mid, "prune",
                    utility_after=util_after, importance_after=imp_after,
                    access_count_after=acc_after,
                    reason=f"周期审查软归档: {reason}",
                )
        elif action == "merge":
            target = str(dec.get("merge_into") or "")
            if store.delete_memory(mid):
                stats["merged"] += 1
                store.log_evolution(
                    mid, "merge",
                    utility_after=util_after, importance_after=imp_after,
                    access_count_after=acc_after,
                    reason=f"合并进 {target}: {reason}",
                )
        elif action == "rescue":
            # Karpathy "rescue to the top": refresh last_used so the attention
            # sort floats this forgotten-but-still-valuable memory back to the
            # top of mind. No content/importance change — pure repositioning.
            if store.touch_last_used(mid):
                stats["rescued"] += 1
                store.log_evolution(
                    mid, "rescue",
                    utility_after=util_after, importance_after=imp_after,
                    access_count_after=acc_after,
                    reason=f"周期审查上浮: {reason}",
                )
        else:  # keep
            stats["kept"] += 1
            new_imp = dec.get("importance")
            new_util = dec.get("utility")
            revised = (new_imp is not None and abs(float(new_imp) - imp_after) > 1e-6) or (
                new_util is not None and abs(float(new_util) - util_after) > 1e-6
            )
            if revised:
                store.update_importance_utility(
                    mid,
                    importance=new_imp if new_imp is not None else None,
                    utility=new_util if new_util is not None else None,
                )
                stats["revised"] += 1
                store.log_evolution(
                    mid, "keep",
                    utility_delta=(float(new_util) - util_after) if new_util is not None else 0.0,
                    utility_after=float(new_util) if new_util is not None else util_after,
                    importance_after=float(new_imp) if new_imp is not None else imp_after,
                    access_count_after=acc_after,
                    reason=f"周期审查保留并修订: {reason}",
                )
    if stats["pruned"] or stats["merged"] or stats["revised"] or stats["rescued"]:
        logger.info(
            "memory evolution: %s 候选=%d 保留=%d 修订=%d 合并=%d 上浮=%d 归档=%d",
            os.path.basename(db_path), stats["candidates"], stats["kept"],
            stats["revised"], stats["merged"], stats["rescued"], stats["pruned"],
        )
    return stats


def _gather_reflection_evidence(store: SQLiteDocumentStore, *, lookback_days: int, cap: int) -> list[dict[str, Any]]:
    """Evidence for reflection: high-signal memories (top-accessed + recently
    feedback-affected) within the lookback window."""
    since = time.time() - lookback_days * 86400
    by_id: dict[str, dict[str, Any]] = {}
    for d in store.list_all_memories(limit=500):
        if float(d.get("access_count") or 0) > 0:
            by_id[str(d["memory_id"])] = d
    wanted: list[str] = []
    for entry in store.list_evolution_log(limit=200):
        if float(entry.get("created_at") or 0) < since:
            continue
        if not str(entry.get("event") or "").startswith(("feedback_", "citation_", "engaged_")):
            continue
        mid = str(entry.get("memory_id") or "")
        if mid and mid not in by_id and mid not in wanted:
            wanted.append(mid)
    if wanted:
        for doc in store.get_memories_by_ids(wanted):
            by_id[str(doc["memory_id"])] = doc
    ranked = sorted(
        by_id.values(),
        key=lambda d: (int(d.get("access_count") or 0), abs(float(d.get("utility") or 0.0))),
        reverse=True,
    )
    return ranked[:cap]


def _run_reflection_core(
    store: SQLiteDocumentStore, *, db_path: str
) -> tuple[list[str], list[str], float] | None:
    """Shared reflection core: activity gate + evidence gathering + judge.

    Returns ``(reflections, evidence_ids, importance)`` or ``None`` if the
    gate is closed / no evidence / judge unavailable. The caller writes each
    reflection to its own module (MemoryStore for reader, AgentMemory for the
    knowledge base) — the if/db_kind dispatch that used to live here is gone.
    """
    s = get_settings()
    if not bool(s.memory_reflection_enabled) or not os.path.exists(db_path):
        return None

    existing = [d for d in store.list_all_memories(memory_type="reflection", limit=50)]
    latest_reflection_ts = max((float(d.get("timestamp") or 0) for d in existing), default=0.0)
    min_events = int(s.memory_reflection_min_events)
    lookback_days = int(s.memory_reflection_lookback_days)

    # Activity gate: new events since the newest reflection (or within the
    # lookback window when no reflection exists yet).
    gate_since = latest_reflection_ts or (time.time() - lookback_days * 86400)
    new_events = store.count_evolution_events_since(gate_since)
    if new_events < min_events:
        return None

    cap = int(s.memory_judge_max_candidates)
    evidence = _gather_reflection_evidence(store, lookback_days=lookback_days, cap=cap)
    if not evidence:
        return None

    from .memory_judge import judge_reflect
    from .policy import get_memory_policy

    reflections = judge_reflect(
        evidence=evidence,
        existing_reflections=[str(d.get("content") or "") for d in existing],
    )
    if not reflections:
        return None

    importance = get_memory_policy().reflection_importance
    evidence_ids = [str(d.get("memory_id")) for d in evidence[:5]]
    return reflections, evidence_ids, importance


def _finalize_reflection(
    store: SQLiteDocumentStore, *, db_path: str, reflections: list[str],
    evidence_ids: list[str], importance: float, writer: Any,
) -> dict[str, Any]:
    """Write reflections via ``writer`` (a callable text->mid), link, log."""
    stats: dict[str, Any] = {"db_path": db_path, "reflected": 0, "skipped": False}
    written = 0
    for text in reflections:
        mid = writer(text)
        if not mid:
            continue
        written += 1
        store.add_links(mid, evidence_ids, reason="反思证据")
        store.log_evolution(mid, "reflection", importance_after=importance, reason=f"反思提炼: {text[:60]}")
    stats["reflected"] = written
    if written:
        logger.info("memory reflection: %s 提炼 %d 条元记忆", os.path.basename(db_path), written)
    return stats


def reflect_reader_memory(db_path: str) -> dict[str, Any]:
    """Reader-memory reflection: writes 元记忆 to the per-user reader store
    via MemoryStore (global scope). No dispatch — owns the reader write path."""
    store = SQLiteDocumentStore(db_path)
    core = _run_reflection_core(store, db_path=db_path)
    if core is None:
        return {"db_path": db_path, "reflected": 0, "skipped": True}
    reflections, evidence_ids, importance = core
    from .memory_store import MemoryStore

    ms = MemoryStore(db_path)
    return _finalize_reflection(
        store, db_path=db_path, reflections=reflections, evidence_ids=evidence_ids,
        importance=importance, writer=lambda text: ms.add(
            scope="global", paper_id=None, kind="reflection", content=text, importance=importance,
        ),
    )


def reflect_knowledge_base(db_path: str) -> dict[str, Any]:
    """Knowledge-base reflection: writes 元记忆 to the shared agent store via
    AgentMemory. No dispatch — owns the knowledge write path."""
    store = SQLiteDocumentStore(db_path)
    core = _run_reflection_core(store, db_path=db_path)
    if core is None:
        return {"db_path": db_path, "reflected": 0, "skipped": True}
    reflections, evidence_ids, importance = core
    from .agent_memory import AgentMemory

    am = AgentMemory(db_path=db_path)
    return _finalize_reflection(
        store, db_path=db_path, reflections=reflections, evidence_ids=evidence_ids,
        importance=importance, writer=lambda text: am.add(
            agent_name="shared", content=text, memory_type="reflection",
            importance=importance, shared=True, tags=["reflection"],
        ),
    )


def reflect_on_memory_db(*, db_path: str, db_kind: str) -> dict[str, Any]:
    """Backward-compatible dispatcher — delegates to the per-module reflect.
    Prefer calling ``reflect_reader_memory`` / ``reflect_knowledge_base`` directly.
    """
    if db_kind == "reader":
        return reflect_reader_memory(db_path)
    return reflect_knowledge_base(db_path)


def evolve_reader_memory() -> list[dict[str, Any]]:
    """One evolution cycle over the per-user reader memory DB only."""
    from .db_paths import memory_db_paths

    path = memory_db_paths()["reader"]
    out = [evolve_memory_db(db_path=path)]
    try:
        out.append(reflect_reader_memory(path))
    except Exception:
        logger.debug("reader memory reflection failed", exc_info=True)
    return out


def evolve_knowledge_base() -> list[dict[str, Any]]:
    """One evolution cycle over the shared knowledge-base (agent) DB only."""
    from .db_paths import memory_db_paths

    path = memory_db_paths()["agent"]
    out = [evolve_memory_db(db_path=path)]
    try:
        out.append(reflect_knowledge_base(path))
    except Exception:
        logger.debug("knowledge base reflection failed", exc_info=True)
    return out


def evolve_all_memory_dbs() -> list[dict[str, Any]]:
    """Backward-compatible combined cycle — delegates to the two independent
    entry points. New callers should call them directly; the combined loop is
    kept for tests/probes that want one-shot evolution of both DBs.
    """
    out = evolve_reader_memory()
    out += evolve_knowledge_base()
    return out


async def _run_evolution_loop(*, name: str, evolve_fn, interval: int) -> None:
    """Shared periodic-loop boilerplate for the two evolution tasks.

    The cycle does blocking LLM calls + sqlite I/O — always run it in a thread
    so the event loop never stalls, and guard the baseline run separately so
    a baseline failure cannot disable the periodic loop.
    """
    try:
        try:
            await asyncio.to_thread(evolve_fn)
        except Exception:
            logger.debug("%s baseline 失败（周期任务仍会继续）", name, exc_info=True)
        if interval <= 0:
            return
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(evolve_fn)
            except Exception:
                logger.debug("%s 周期进化失败", name, exc_info=True)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.debug("%s loop 异常退出", name, exc_info=True)


async def reader_memory_evolution_loop() -> None:
    s = get_settings()
    await _run_evolution_loop(
        name="reader memory evolution",
        evolve_fn=evolve_reader_memory,
        interval=int(s.memory_evolution_interval_sec),
    )


async def knowledge_base_evolution_loop() -> None:
    s = get_settings()
    await _run_evolution_loop(
        name="knowledge base evolution",
        evolve_fn=evolve_knowledge_base,
        interval=int(s.memory_evolution_interval_sec),
    )


# Kept for backward compatibility with any caller that imports the old name.
async def memory_evolution_loop() -> None:
    await _run_evolution_loop(
        name="memory evolution",
        evolve_fn=evolve_all_memory_dbs,
        interval=int(get_settings().memory_evolution_interval_sec),
    )


def spawn_memory_evolution_tasks(app: FastAPI) -> list[asyncio.Task]:
    """Start the two independent evolution background tasks (reader + knowledge).

    Each runs on its own DB with its own baseline-failure isolation; one
    stalling cannot stop the other. Returns the tasks for shutdown cancellation.
    """
    tasks: list[asyncio.Task] = []
    try:
        tasks.append(asyncio.create_task(
            reader_memory_evolution_loop(), name="papergraph_reader_memory_evolution",
        ))
        tasks.append(asyncio.create_task(
            knowledge_base_evolution_loop(), name="papergraph_knowledge_base_evolution",
        ))
    except RuntimeError:
        logger.debug("memory evolution: 无 event loop，跳过后台任务")
    return tasks
