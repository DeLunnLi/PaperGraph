"""Offline regressions for memory budgets, compression and full KB queries."""
from __future__ import annotations

import asyncio
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.routes import knowledge
from app.services.memory import memory_judge, memory_semantic
from app.services.memory.memory_store import MemoryStore
from app.services.memory.sqlite_document_store_compat import SQLiteDocumentStore
from app.settings import get_settings


@pytest.fixture(autouse=True)
def no_external_models(monkeypatch):
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    monkeypatch.setattr(memory_semantic, "semantic_recall", lambda *args, **kwargs: [])


@pytest.fixture
def stores(tmp_path, monkeypatch):
    reader = SQLiteDocumentStore(str(tmp_path / "reader_memory.db"))
    agent = SQLiteDocumentStore(str(tmp_path / "agent_memory.db"))
    monkeypatch.setattr(knowledge, "memory_db_paths", lambda: {"reader": reader.db_path, "agent": agent.db_path})
    return reader, agent


def seed_working(store, count=50):
    return [store.store.add_memory(
        user_id="papergraph:paper:1", content=f"note {i}", memory_type="working", timestamp=i,
    ) for i in range(count)]


def test_compression_policy_and_exact_old_batch(tmp_path, monkeypatch):
    store = MemoryStore(str(tmp_path / "papers.db"))
    ids = seed_working(store, 49)
    calls = []
    monkeypatch.setattr(store, "_summarize_via_llm", lambda docs: calls.append(docs) or "summary")
    assert store.compress_working(paper_id=1, min_entries=6) == "not enough entries to compress"
    assert not calls
    ids += seed_working(store, 1)
    assert store.compress_working(paper_id=1) == "compressed"
    assert {d["memory_id"] for d in calls[0]} == set(ids[:19] + ids[-1:])
    assert len(store.store.search_memories("papergraph:paper:1", "working", limit=None)) == 30
    assert len(store.store.search_memories("papergraph:paper:1", "long", limit=None)) == 1
    assert all(d["deleted_at"] == 0 for d in store.store.get_memories_by_ids(ids[29:49]))


def test_add_uses_same_compression_threshold(tmp_path, monkeypatch):
    store = MemoryStore(str(tmp_path / "papers.db"))
    seed_working(store, 49)
    calls = []
    monkeypatch.setattr(store, "_summarize_via_llm", lambda docs: calls.append(docs) or "summary")
    store.add(scope="paper", paper_id=1, kind="working", content="unique-final-question")
    assert len(calls) == 1 and len(calls[0]) == 20


def test_compression_single_flight_across_store_instances(tmp_path, monkeypatch):
    a = MemoryStore(str(tmp_path / "papers.db"))
    b = MemoryStore(str(tmp_path / "papers.db"))
    seed_working(a)
    started, release = threading.Event(), threading.Event()
    calls, results = [], []

    def summarize(self, docs):
        calls.append(docs)
        started.set()
        assert release.wait(5)
        return "summary"

    monkeypatch.setattr(MemoryStore, "_summarize_via_llm", summarize)
    worker = threading.Thread(target=lambda: results.append(a.compress_working(paper_id=1)))
    worker.start()
    try:
        assert started.wait(5)
        assert b.compress_working(paper_id=1) == "compression in progress"
    finally:
        release.set()
        worker.join(5)
    assert results == ["compressed"] and len(calls) == 1
    assert b.compress_working(paper_id=1) == "not enough entries to compress"


@pytest.mark.parametrize("failure", ["empty_summary", "storage_failure", "edited_input"])
def test_compression_failure_preserves_inputs(tmp_path, monkeypatch, failure):
    store = MemoryStore(str(tmp_path / "papers.db"))
    ids = seed_working(store)

    def summarize(docs):
        if failure == "edited_input":
            store.store.add_memory(user_id="papergraph:paper:1", memory_id=ids[0], content="edited", memory_type="working")
        return "" if failure == "empty_summary" else "summary"

    monkeypatch.setattr(store, "_summarize_via_llm", summarize)
    if failure == "storage_failure":
        with sqlite3.connect(store.db_path) as conn:
            conn.execute("CREATE TRIGGER reject_prune BEFORE INSERT ON memory_evolution_log BEGIN SELECT RAISE(FAIL, 'test'); END")
    assert store.compress_working(paper_id=1) != "compressed"
    assert len(store.store.search_memories("papergraph:paper:1", "working", limit=None)) == 50
    assert not store.store.search_memories("papergraph:paper:1", "long", limit=None)


@pytest.mark.parametrize("cap", [4, 12, 24, 40])
def test_full_candidate_buckets_reserve_recent_quotas(tmp_path, monkeypatch, cap):
    store = MemoryStore(str(tmp_path / "papers.db"))
    monkeypatch.setattr(get_settings(), "memory_judge_max_candidates", cap)
    recent_ids = {"working": [], "short": []}
    for scope, pid, kind, count in [
        ("global", None, "preference", 3), ("global", None, "reflection", 3),
        ("paper", 1, "paper_summary", 4), ("paper", 1, "long", 4),
        ("paper", 1, "short", 4), ("paper", 1, "working", 4),
    ]:
        for i in range(count):
            mid = store.store.add_memory(user_id=store._user_id_for(scope, pid), content=f"{kind} {i}", memory_type=kind)
            if kind in recent_ids:
                recent_ids[kind].append(mid)
    recalled = [{"memory_id": f"tail-{i}", "content": f"tail {i}", "timestamp": i} for i in range(6)]
    # Repeated semantic results must not consume the candidate budget.
    monkeypatch.setattr(store, "_semantic_recall_docs", lambda **kw: [recalled[0], *recalled])
    captured = []
    monkeypatch.setattr(store, "_rank_with_judge", lambda docs, **kw: captured.extend(docs) or [])
    store.build_chat_memory(paper_id=1, query="topic")
    ids = [d["memory_id"] for d in captured]
    assert len(ids) == len(set(ids)) <= cap
    for kind in ("working", "short"):
        assert len(set(ids) & set(recent_ids[kind])) >= min(4, cap // 2)


def test_knowledge_filters_old_memories_before_limit(stores, tmp_path):
    reader, agent = stores
    for i in range(1001):
        reader.add_memory(user_id="papergraph:paper:2", content=f"new {i}", timestamp=1000+i)
    old = reader.add_memory(user_id="papergraph:paper:1", content="old important", timestamp=1, utility=4)
    out = asyncio.run(knowledge.list_knowledge_memories(
        scope="paper", paper_id=1, kind=None, sort="utility", limit=1, db_path=str(tmp_path / "papers.db"), user={},
    ))
    assert out["total"] == 1 and out["items"][0]["memory_id"] == old
    assert knowledge._collect("all", None, None, sort="utility", limit=1)[0]["memory_id"] == old


@pytest.mark.parametrize("sort, field", [("recency", "timestamp"), ("access", "access_count"), ("utility", "utility"), ("importance", "importance")])
def test_knowledge_top_n_merges_both_databases(stores, sort, field):
    for store, values in zip(stores, ([1, 5, 3], [6, 2, 4])):
        for value in values:
            store.add_memory(user_id="papergraph:global", content=str(value), **{field: value})
    out = knowledge._collect("all", None, None, sort=sort, limit=3)
    assert [int(item["content"]) for item in out] == [6, 5, 4]


def test_knowledge_stats_uses_all_live_rows(stores):
    reader, agent = stores
    for i in range(1001):
        reader.add_memory(user_id="papergraph:global", content=str(i), memory_type="long", utility=1, access_count=2)
    agent.add_memory(user_id="papergraph:agent:test", content="negative", utility=-1, access_count=3)
    retired = agent.add_memory(user_id="papergraph:shared", content="retired", utility=4, access_count=500)
    agent.delete_memory(retired)
    out = knowledge._stats_payload()
    assert out["total"] == 1002
    assert out["by_kind"] == {"long": 1001, "working": 1}
    assert out["avg_utility"] == pytest.approx(1000 / 1002)
    assert out["total_accesses"] == 2005
    assert out["health"]["utility_distribution"] == {"negative": 1, "neutral": 0, "positive": 1001}
    assert out["top_accessed"][0]["content"] == "negative"


def test_zero_importance_preserved_when_merging_top_n(stores):
    stores[0].add_memory(user_id="papergraph:global", content="zero", importance=0)
    stores[0].add_memory(user_id="papergraph:global", content="low", importance=0.1)
    stores[1].add_memory(user_id="papergraph:global", content="higher", importance=0.2)
    items = knowledge._collect("all", None, None, sort="importance", limit=2)
    assert [item["content"] for item in items] == ["higher", "low"]


@pytest.mark.parametrize("importance, expected", [(None, "legacy"), (0, "higher")])
def test_importance_null_default_and_zero_match_sql_top_one(stores, importance, expected):
    legacy = stores[0].add_memory(user_id="papergraph:global", content="legacy", timestamp=1)
    with sqlite3.connect(stores[0].db_path) as conn:
        conn.execute("UPDATE memories SET importance = ? WHERE memory_id = ?", (importance, legacy))
    stores[0].add_memory(user_id="papergraph:global", content="low", importance=0.1, timestamp=2)
    stores[1].add_memory(user_id="papergraph:global", content="higher", importance=0.4, timestamp=3)
    # Reading an explicit zero must preserve it; legacy NULL gets its default
    # at the API boundary, with exactly the same default in the SQL ordering.
    assert stores[0].get_memories_by_ids([legacy])[0]["importance"] == importance
    items = knowledge._collect("all", None, None, sort="importance", limit=1)
    assert items[0]["content"] == expected
    assert items[0]["importance"] == (0.5 if importance is None else 0.4)


def epoch(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def insert_event(store, timestamp):
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("INSERT INTO memory_evolution_log (memory_id,event,created_at) VALUES ('test','access',?)", (timestamp,))


def test_activity_7d_timezone_boundaries_and_both_databases(stores, monkeypatch):
    reader, agent = stores
    monkeypatch.setattr(knowledge.time, "time", lambda: epoch("2026-10-14T03:00:00"))
    for store in (reader, agent):
        for when in ("2026-10-13T15:59:59", "2026-10-13T16:00:00", "2026-10-07T16:00:00", "2026-10-07T15:59:59", "2026-10-14T16:00:00"):
            insert_event(store, epoch(when))
    shanghai = knowledge._stats_payload("Asia/Shanghai")["activity_7d"]
    assert [d["date"] for d in shanghai] == [f"2026-10-{i:02d}" for i in range(8, 15)]
    assert [d["count"] for d in shanghai] == [2, 0, 0, 0, 0, 2, 2]
    utc = knowledge._stats_payload()["activity_7d"]
    assert utc[-2]["count"] == 4 and utc[-1]["count"] == 2


def test_activity_7d_handles_daylight_saving(stores, monkeypatch):
    monkeypatch.setattr(knowledge.time, "time", lambda: epoch("2026-03-09T12:00:00"))
    for when in ("2026-03-08T04:59:59", "2026-03-08T05:00:00", "2026-03-09T03:59:59", "2026-03-09T04:00:00"):
        insert_event(stores[0], epoch(when))
    days = knowledge._stats_payload("America/New_York")["activity_7d"]
    assert days[-3:] == [{"date": "2026-03-07", "count": 1}, {"date": "2026-03-08", "count": 2}, {"date": "2026-03-09", "count": 1}]


def test_stats_rejects_invalid_timezone(stores):
    with pytest.raises(HTTPException) as error:
        knowledge._stats_payload("Not/A_Timezone")
    assert error.value.status_code == 422
