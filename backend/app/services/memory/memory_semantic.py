"""Embedding-based semantic recall for the self-evolving memory system.

The LLM judge can only rank what it sees — recency buckets alone hide
semantically relevant older memories. This module adds the missing *recall*
channel: cosine top-K over persisted per-memory embeddings, mixed into the
candidate pool *before* the judge decides. Recall is gathering, not scoring —
the final selection stays with the judge.

Embeddings are lazily backfilled (capped per call) and persisted in the
``memory_embeddings`` sidecar table; content changes invalidate them (see
``SQLiteDocumentStore.add_memory``). Everything fails soft: when the
embedding endpoint is unconfigured or errors, recall returns [] and the
retrieval paths degrade to their recency-only candidate pools.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from cachetools import TTLCache

from ...settings import get_settings
from ..embedding.embedding_service import cosine_similarity, embed_texts, embedding_enabled
from .policy import get_memory_policy

logger = logging.getLogger(__name__)

# list_embeddings() re-parses the whole embedding table per call (tens of ms +
# MBs of JSON at a few hundred memories) and recall runs twice per chat turn —
# cache briefly per db_path; fresh embeddings surface within the TTL.
_EMB_CACHE: TTLCache = TTLCache(maxsize=8, ttl=30)
_EMB_CACHE_LOCK = threading.Lock()


def _load_embeddings(store: Any) -> dict[str, list[float]]:
    key = str(getattr(store, "db_path", ""))
    with _EMB_CACHE_LOCK:
        cached = _EMB_CACHE.get(key)
    if cached is not None:
        return cached
    loaded = store.list_embeddings()
    with _EMB_CACHE_LOCK:
        _EMB_CACHE[key] = loaded
    return loaded


def clear_embedding_cache() -> None:
    """Drop all cached embedding tables (test isolation hook)."""
    with _EMB_CACHE_LOCK:
        _EMB_CACHE.clear()


def precompute_embeddings(store: Any, docs: list[dict[str, Any]], *, cap: int = 100) -> None:
    """Eagerly backfill embeddings for freshly written memories.

    Called from background write paths (e.g. the post-chat memory task) so the
    lazy backfill in ``semantic_recall`` rarely blocks a live chat turn on
    embedding API calls. Mutates the cached embedding dict in place, so the
    vectors are visible to recall immediately.
    """
    if not docs or not _enabled():
        return
    try:
        existing = _load_embeddings(store)
        _backfill_embeddings(store, docs, existing, cap=max(0, int(cap)))
    except Exception:
        logger.debug("memory_semantic precompute failed", exc_info=True)


def _enabled() -> bool:
    try:
        return bool(get_settings().memory_semantic_recall_enabled) and embedding_enabled()
    except Exception:
        return False


def _backfill_embeddings(
    store: Any,
    docs: list[dict[str, Any]],
    existing: dict[str, list[float]],
    *,
    cap: int,
) -> None:
    """Compute + persist embeddings for docs missing them (bounded by ``cap``)."""
    missing: list[tuple[str, str]] = []
    for d in docs:
        mid = str(d.get("memory_id") or "")
        content = str(d.get("content") or "").strip()
        if mid and content and mid not in existing:
            missing.append((mid, content))
        if len(missing) >= cap:
            break
    batch_size = get_memory_policy().embed_batch_size
    for start in range(0, len(missing), batch_size):
        batch = missing[start : start + batch_size]
        try:
            vectors = embed_texts([c for _mid, c in batch])
        except Exception:
            logger.debug("memory_semantic backfill failed", exc_info=True)
            return
        for (mid, _c), vec in zip(batch, vectors):
            try:
                store.upsert_embedding(mid, vec)
                existing[mid] = vec
            except Exception:
                logger.debug("memory_semantic upsert failed", exc_info=True)


def semantic_recall(
    store: Any,
    *,
    docs: list[dict[str, Any]],
    query: str,
) -> list[dict[str, Any]]:
    """Rank ``docs`` by embedding cosine to ``query``; [] when unavailable.

    ``docs`` should be the full scope pool (not just the recency bucket) so
    long-tail memories can surface. A loose similarity floor filters noise —
    this is recall gathering; the judge makes the final call.
    """
    q = (query or "").strip()
    if not q or not docs or not _enabled():
        return []
    s = get_settings()
    k = int(s.memory_semantic_top_k)
    backfill_cap = int(s.memory_semantic_backfill_cap)
    try:
        existing = _load_embeddings(store)
        _backfill_embeddings(store, docs, existing, cap=max(0, backfill_cap))
        if not existing:
            return []
        qv = embed_texts([q])[0]
    except Exception:
        logger.debug("memory_semantic recall setup failed", exc_info=True)
        return []
    floor = get_memory_policy().semantic_similarity_floor
    scored: list[tuple[float, dict[str, Any]]] = []
    for d in docs:
        mid = str(d.get("memory_id") or "")
        vec = existing.get(mid)
        if not vec:
            continue
        sim = cosine_similarity(qv, vec)
        if sim >= floor:  # loose recall floor; judge does the real filtering
            scored.append((sim, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _sim, d in scored[: max(1, k)]]
