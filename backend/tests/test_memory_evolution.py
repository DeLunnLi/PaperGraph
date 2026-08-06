"""Tests for the self-evolving memory system.

Covers: schema migration (utility/access_count/last_used + evolution log),
access tracking on injection, LLM-judge selection with recency fallback,
feedback attribution (judge-decided deltas), periodic evolution
(judge-decided keep/merge/prune), and stat preservation across upserts.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.memory import memory_judge
from app.services.memory.agent_memory import AgentMemory
from app.services.memory.memory_evolution import evolve_memory_db
from app.services.memory.memory_store import MemoryStore
from app.services.memory.sqlite_document_store_compat import SQLiteDocumentStore
from app.services.reader.paper_reader_history import append_turn
from app.services.reader.reader_feedback_service import record_reader_feedback


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Deterministic tests: never call a real LLM even if the env has a key.

    Individual tests override this by monkeypatching judge functions or
    ``memory_judge._llm_or_none`` themselves (applied after this fixture).
    """
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)


@pytest.fixture()
def store(tmp_path) -> MemoryStore:
    return MemoryStore(str(tmp_path / "papers.db"))


def _docs(s: MemoryStore, scope="paper", paper_id=1, kinds=("working", "short", "long", "paper_summary", "preference")):
    return s._list_recent_docs(scope=scope, paper_id=paper_id, kinds=list(kinds), limit_per_kind=100)


def _backdate(db_path: str, memory_id: str, days: float) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE memories SET timestamp = ? WHERE memory_id = ?",
            (time.time() - days * 86400, memory_id),
        )


# ---------- schema ----------

def test_schema_has_evolution_columns_and_log_table(tmp_path):
    s = MemoryStore(str(tmp_path / "papers.db"))
    with sqlite3.connect(s.db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(memories)")}
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"utility", "access_count", "last_used"} <= cols
    assert "memory_evolution_log" in tables


def test_new_memory_has_zero_stats(store):
    store.add(scope="paper", paper_id=1, kind="working", content="用户问：什么是注意力", importance=0.5)
    d = _docs(store)[0]
    assert d["utility"] == 0.0
    assert d["access_count"] == 0
    assert d["last_used"] == 0


def test_upsert_single_preserves_utility_across_replace(store):
    store.upsert_single(scope="paper", paper_id=1, kind="paper_summary", content="v1 摘要", importance=0.7)
    mid = "papergraph:paper:1:paper_summary:singleton"
    assert store.adjust_utility(mid, delta=1.5, reason="test")
    store.upsert_single(scope="paper", paper_id=1, kind="paper_summary", content="v2 摘要", importance=0.8)
    docs = _docs(store, kinds=("paper_summary",))
    assert docs[0]["content"] == "v2 摘要"
    assert docs[0]["importance"] == pytest.approx(0.8)
    assert docs[0]["utility"] == pytest.approx(1.5)  # preserved


# ---------- access tracking ----------

def test_build_context_block_increments_access_and_returns_ids(store, monkeypatch):
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)  # force fallback
    store.add(scope="paper", paper_id=1, kind="short", content="方法：双塔召回", importance=0.7)
    text, _q, ids = store.build_chat_memory(paper_id=1)
    assert "双塔召回" in text
    assert len(ids) == 1
    d = _docs(store, kinds=("short",))[0]
    assert d["access_count"] == 1
    assert d["last_used"] > 0


def test_chat_memory_injects_and_counts_once(store, monkeypatch):
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    store.add(scope="paper", paper_id=1, kind="short", content=" transformer 架构解析", importance=0.6)
    base, _qtext, ids = store.build_chat_memory(paper_id=1, query="transformer")
    assert "transformer" in base
    assert ids
    assert len(ids) == len(set(ids))  # never double-injected across slices
    d = _docs(store, kinds=("short",))[0]
    assert d["access_count"] == 1  # and never double-counted


def test_judge_selection_overrides_recency(store, monkeypatch):
    old = store.add(scope="paper", paper_id=1, kind="short", content="旧记忆", importance=0.5)
    new = store.add(scope="paper", paper_id=1, kind="short", content="新记忆", importance=0.5)
    _backdate(store.db_path, old, days=0.1)

    class _FakeLLM:
        def invoke(self, messages, **kw):
            # Judge picks the OLD memory despite recency
            return type("R", (), {"content": json.dumps({"selected": [old]})})()

    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: _FakeLLM())
    text, _q, ids = store.build_chat_memory(paper_id=1)
    assert ids == [old]
    assert "旧记忆" in text


# ---------- feedback attribution ----------

def _setup_turn_with_memories(tmp_path, store: MemoryStore, memory_ids: list[str]) -> int:
    papers_db = str(tmp_path / "papers.db")
    tid = append_turn(
        papers_db, paper_id=1, user_id=1, role="assistant", content="这是回答",
        metadata=json.dumps({"memory_ids": memory_ids}),
    )
    assert tid is not None
    return tid


def test_feedback_adjusts_utility_via_judge(tmp_path, store, monkeypatch):
    mid = store.add(scope="paper", paper_id=1, kind="short", content="关键结论", importance=0.6)
    tid = _setup_turn_with_memories(tmp_path, store, [mid])

    monkeypatch.setattr(
        memory_judge, "judge_feedback_deltas",
        lambda **kw: {mid: 0.7},
    )
    out = record_reader_feedback(db_path=str(tmp_path / "papers.db"), user_id=1, paper_id=1, turn_id=tid, signal="up")
    assert out["success"] and out["affected_memory_count"] == 1
    d = _docs(store, kinds=("short",))[0]
    assert d["utility"] == pytest.approx(0.7)
    logs = store.store.list_evolution_log()
    assert any(e["event"] == "feedback_up" and e["utility_delta"] == pytest.approx(0.7) for e in logs)


def test_feedback_without_judge_logs_only(tmp_path, store, monkeypatch):
    mid = store.add(scope="paper", paper_id=1, kind="short", content="关键结论", importance=0.6)
    tid = _setup_turn_with_memories(tmp_path, store, [mid])
    monkeypatch.setattr(memory_judge, "judge_feedback_deltas", lambda **kw: {})
    out = record_reader_feedback(db_path=str(tmp_path / "papers.db"), user_id=1, paper_id=1, turn_id=tid, signal="down")
    assert out["success"] and out["affected_memory_count"] == 1
    d = _docs(store, kinds=("short",))[0]
    assert d["utility"] == 0.0  # unchanged
    logs = store.store.list_evolution_log()
    assert any(e["event"] == "feedback_down" and e["utility_delta"] == 0.0 for e in logs)


def test_feedback_rejects_wrong_turn_or_signal(tmp_path, store):
    tid = _setup_turn_with_memories(tmp_path, store, ["x"])
    db = str(tmp_path / "papers.db")
    assert not record_reader_feedback(db_path=db, user_id=1, paper_id=1, turn_id=tid, signal="bogus")["success"]
    assert not record_reader_feedback(db_path=db, user_id=1, paper_id=999, turn_id=tid, signal="up")["success"]
    assert not record_reader_feedback(db_path=db, user_id=2, paper_id=1, turn_id=tid, signal="up")["success"]


def test_adjust_utility_clamps(store):
    mid = store.add(scope="paper", paper_id=1, kind="short", content="x", importance=0.5)
    store.adjust_utility(mid, delta=99.0)
    assert _docs(store, kinds=("short",))[0]["utility"] == 5.0
    store.adjust_utility(mid, delta=-99.0)
    assert _docs(store, kinds=("short",))[0]["utility"] == -2.0


# ---------- periodic evolution (LLM-judged) ----------

def test_evolve_skips_when_judge_unavailable(store, monkeypatch):
    mid = store.add(scope="paper", paper_id=1, kind="working", content="老记忆", importance=0.1)
    _backdate(store.db_path, mid, days=30)
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    stats = evolve_memory_db(db_path=store.db_path)
    assert stats["skipped"] is True
    assert stats["pruned"] == 0
    assert _docs(store)  # nothing deleted


def test_evolve_applies_judge_decisions(store, monkeypatch):
    doomed = store.add(scope="paper", paper_id=1, kind="working", content="过时结论", importance=0.2)
    keeper = store.add(scope="paper", paper_id=1, kind="working", content="仍有价值", importance=0.4)
    target = store.add(scope="paper", paper_id=1, kind="working", content="原始表述", importance=0.4)
    dup = store.add(scope="paper", paper_id=1, kind="working", content="重复表述", importance=0.3)
    for m in (doomed, keeper, target, dup):
        _backdate(store.db_path, m, days=30)

    monkeypatch.setattr(
        memory_judge, "judge_evolution_batch",
        lambda *, candidates: [
            {"memory_id": doomed, "action": "prune", "merge_into": None, "importance": None, "utility": None, "reason": "已过时"},
            {"memory_id": keeper, "action": "keep", "merge_into": None, "importance": 0.9, "utility": None, "reason": "核心价值"},
            {"memory_id": dup, "action": "merge", "merge_into": target, "importance": None, "utility": None, "reason": "语义重复"},
        ],
    )
    stats = evolve_memory_db(db_path=store.db_path)
    assert stats["pruned"] == 1 and stats["merged"] == 1 and stats["revised"] == 1
    remaining = {d["content"] for d in _docs(store)}
    assert "过时结论" not in remaining  # pruned
    assert "重复表述" not in remaining  # merged away
    assert "仍有价值" in remaining and "原始表述" in remaining
    keeper_doc = [d for d in _docs(store) if d["content"] == "仍有价值"][0]
    assert keeper_doc["importance"] == pytest.approx(0.9)  # judge revised
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert {"prune", "merge", "keep"} <= events


def test_stale_candidates_exclude_singletons_and_fresh(store):
    old_working = store.add(scope="paper", paper_id=1, kind="working", content="早期问题：召回策略", importance=0.2)
    _backdate(store.db_path, old_working, days=30)
    store.upsert_single(scope="global", paper_id=None, kind="preference", content="偏好中文", importance=0.85)
    _backdate(store.db_path, "papergraph:global:preference:singleton", days=30)
    store.add(scope="paper", paper_id=1, kind="working", content="近期问题：排序模型", importance=0.2)
    stale = store.store.list_stale_memories(min_age_days=7, exclude_types=("preference", "paper_summary"))
    contents = {d["content"] for d in stale}
    assert "早期问题：召回策略" in contents
    assert "偏好中文" not in contents  # singleton excluded
    assert "近期问题：排序模型" not in contents  # too fresh


def test_accessed_memory_not_in_stale_candidates(store):
    mid = store.add(scope="paper", paper_id=1, kind="working", content="被命中的老记忆", importance=0.2)
    _backdate(store.db_path, mid, days=30)
    store.store.increment_access([mid])
    stale = store.store.list_stale_memories(min_age_days=7, exclude_types=("preference", "paper_summary"))
    assert all(d["content"] != "被命中的老记忆" for d in stale)


# ---------- AgentMemory judge integration ----------

def test_agent_memory_eviction_uses_judge(tmp_path, monkeypatch):
    # Seed directly (distinct ids) — AgentMemory.add ids are second-granularity,
    # so rapid successive adds would collide; eviction is tested directly.
    uid = "papergraph:agent:paper_analysis"
    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    total = AgentMemory.MAX_EPISODIC_PER_AGENT + 2
    for i in range(total):
        am.store.add_memory(
            memory_id=f"ep-{i}", user_id=uid, content=f"insight {i}",
            memory_type="episodic", importance=0.5, timestamp=time.time() - (total - i),
        )
    am._evict_old_memories(uid, "episodic", AgentMemory.MAX_EPISODIC_PER_AGENT)
    rows = am.store.search_memories(user_id=uid, memory_type="episodic", limit=100)
    assert len(rows) == AgentMemory.MAX_EPISODIC_PER_AGENT  # eviction ran
    # Judge unavailable → fallback keeps most recent
    ids = {r["memory_id"] for r in rows}
    assert f"ep-{total - 1}" in ids
    assert "ep-0" not in ids  # oldest evicted
    events = {e["event"] for e in am.store.list_evolution_log()}
    assert "prune" in events  # evictions logged


def test_agent_memory_eviction_judge_keep_list(tmp_path, monkeypatch):
    uid = "papergraph:agent:paper_analysis"
    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    total = AgentMemory.MAX_EPISODIC_PER_AGENT + 2
    for i in range(total):
        am.store.add_memory(
            memory_id=f"ep-{i}", user_id=uid, content=f"insight {i}",
            memory_type="episodic", importance=0.5, timestamp=time.time() - (total - i),
        )
    # Judge keeps the two OLDEST (against recency) plus the fresh ones
    keep = [f"ep-{i}" for i in range(2, total)] + ["ep-0", "ep-1"]
    keep = keep[: AgentMemory.MAX_EPISODIC_PER_AGENT]
    monkeypatch.setattr(
        memory_judge, "judge_select_memories",
        lambda *, candidates, purpose, context_hint="", max_items=10: keep,
    )
    am._evict_old_memories(uid, "episodic", AgentMemory.MAX_EPISODIC_PER_AGENT)
    ids = {r["memory_id"] for r in am.store.search_memories(user_id=uid, memory_type="episodic", limit=100)}
    assert ids == set(keep)  # judge decision honored over recency


def test_agent_memory_context_block_tracks_access(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    am.add(agent_name="paper_analysis", content="读者偏好简洁回答", memory_type="working", importance=0.6)
    am.build_context_block(agent_name="paper_analysis", query=None, tags=None)
    rows = am.store.search_memories(user_id="papergraph:agent:paper_analysis", memory_type="working", limit=10)
    assert rows[0]["access_count"] >= 1


# ---------- semantic recall (embedding bucket) ----------

def test_semantic_recall_ranks_by_cosine(store, monkeypatch):
    from app.services.memory import memory_semantic

    monkeypatch.setattr(memory_semantic, "embedding_enabled", lambda: True)
    vectors = {
        "query": [1.0, 0.0],
        "transformer 架构": [0.9, 0.1],
        "食谱 红烧肉": [0.0, 1.0],
    }
    monkeypatch.setattr(memory_semantic, "embed_texts", lambda texts: [vectors[t] for t in texts])
    mid_rel = store.add(scope="paper", paper_id=1, kind="short", content="transformer 架构", importance=0.5)
    mid_irr = store.add(scope="paper", paper_id=1, kind="short", content="食谱 红烧肉", importance=0.5)
    out = memory_semantic.semantic_recall(store.store, docs=_docs(store, kinds=("short",)), query="query")
    assert [d["memory_id"] for d in out] == [mid_rel]  # 红烧肉 cosine 0.0 < 0.2 floor


def test_semantic_recall_disabled_returns_empty(store, monkeypatch):
    from app.services.memory import memory_semantic

    monkeypatch.setattr(memory_semantic, "embedding_enabled", lambda: False)
    store.add(scope="paper", paper_id=1, kind="short", content="transformer 架构", importance=0.5)
    assert memory_semantic.semantic_recall(store.store, docs=_docs(store, kinds=("short",)), query="transformer") == []


def test_semantic_bucket_reaches_judge_candidates(store, monkeypatch):
    """A recalled long-tail doc (not in recency buckets) enters the judge pool."""
    from app.services.memory import memory_semantic

    tail_mid = store.add(scope="paper", paper_id=1, kind="long", content="长尾 旧洞察", importance=0.5)
    monkeypatch.setattr(
        memory_semantic, "semantic_recall",
        lambda store_, *, docs, query, top_k=None: [d for d in docs if d["memory_id"] == tail_mid],
    )
    captured: dict = {}

    def _fake_rank(docs, *, purpose, context_hint="", max_items):
        captured["ids"] = {store._memory_id_of(d) for d in docs}
        return docs

    monkeypatch.setattr(MemoryStore, "_rank_with_judge", lambda self, docs, **kw: _fake_rank(docs, **kw))
    _base, _text, ids = store.build_chat_memory(paper_id=1, query="旧洞察")
    assert tail_mid in captured["ids"]
    assert tail_mid in ids  # injected → access tracked
    d = [x for x in _docs(store, kinds=("long",)) if x["memory_id"] == tail_mid][0]
    assert d["access_count"] == 1


def test_embedding_invalidated_on_content_change(store):
    mid = store.add(scope="paper", paper_id=1, kind="short", content="原始 内容", importance=0.5)
    store.store.upsert_embedding(mid, [0.1, 0.2])
    store.store.add_memory(user_id="papergraph:paper:1", content="改写 内容", memory_type="short",
                           importance=0.5, memory_id=mid)
    assert store.store.list_embeddings() == {}


def test_delete_memory_cascades_embedding(store):
    mid = store.add(scope="paper", paper_id=1, kind="short", content="x", importance=0.5)
    store.store.upsert_embedding(mid, [0.1, 0.2])
    store.store.delete_memory(mid)
    assert store.store.list_embeddings() == {}


# ---------- engaged read (implicit dwell signal) ----------

def test_engaged_read_credits_session_memories(store, monkeypatch):
    from app.services.reader.reader_feedback_service import record_engaged_read

    mid = store.add(scope="paper", paper_id=1, kind="short", content="核心 方法", importance=0.6)
    store.store.increment_access([mid])  # injected just now → inside session window
    monkeypatch.setattr(memory_judge, "judge_feedback_deltas", lambda **kw: {mid: 0.3})
    out = record_engaged_read(db_path=store.db_path.replace("reader_memory.db", "papers.db"),
                              paper_id=1, user_id=1, duration_sec=600)
    assert out["affected_memory_count"] == 1
    d = _docs(store, kinds=("short",))[0]
    assert d["utility"] == pytest.approx(0.3)
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert "engaged_read" in events


def test_engaged_read_below_threshold_noop(store):
    from app.services.reader.reader_feedback_service import record_engaged_read

    mid = store.add(scope="paper", paper_id=1, kind="short", content="核心 方法", importance=0.6)
    store.store.increment_access([mid])
    out = record_engaged_read(db_path=store.db_path.replace("reader_memory.db", "papers.db"),
                              paper_id=1, user_id=1, duration_sec=30)  # < 240s threshold
    assert out["affected_memory_count"] == 0
    assert _docs(store, kinds=("short",))[0]["utility"] == 0.0


def test_engaged_read_ignores_never_injected(store):
    from app.services.reader.reader_feedback_service import record_engaged_read

    store.add(scope="paper", paper_id=1, kind="short", content="核心 方法", importance=0.6)
    out = record_engaged_read(db_path=store.db_path.replace("reader_memory.db", "papers.db"),
                              paper_id=1, user_id=1, duration_sec=600)
    assert out["affected_memory_count"] == 0  # last_used == 0 → outside window


# ---------- write-time conflict judgment (Mem0-style) ----------

def test_write_conflict_same_merges_preserving_stats(store, monkeypatch):
    old_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户偏好 concise 回答", importance=0.5)
    store.adjust_utility(old_mid, delta=1.2, reason="seed")
    store.store.increment_access([old_mid])
    monkeypatch.setattr(
        memory_judge, "judge_write_conflict",
        lambda *, new_content, candidates: {old_mid: "same"},
    )
    new_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户偏好 concise 风格", importance=0.7)
    assert new_mid == old_mid  # merged into the old id
    d = _docs(store, kinds=("short",))[0]
    assert d["content"] == "用户偏好 concise 风格"
    assert d["importance"] == pytest.approx(0.7)  # max(old, new)
    assert d["utility"] == pytest.approx(1.2)  # stats preserved
    assert d["access_count"] == 1
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert "merge" in events


def test_write_conflict_supersede_replaces_in_place(store, monkeypatch):
    old_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户喜欢 transformer 模型", importance=0.6)
    store.adjust_utility(old_mid, delta=0.8, reason="seed")
    monkeypatch.setattr(
        memory_judge, "judge_write_conflict",
        lambda *, new_content, candidates: {old_mid: "supersede"},
    )
    new_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户不再喜欢 transformer 模型", importance=0.6)
    assert new_mid == old_mid
    docs = _docs(store, kinds=("short",))
    assert len(docs) == 1
    assert docs[0]["content"] == "用户不再喜欢 transformer 模型"  # replaced
    assert docs[0]["utility"] == pytest.approx(0.8)  # stats survive the contradiction
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert "supersede" in events


def test_write_conflict_keep_adds_new(store, monkeypatch):
    old_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户关注 retrieval 召回", importance=0.6)
    monkeypatch.setattr(
        memory_judge, "judge_write_conflict",
        lambda *, new_content, candidates: {old_mid: "keep"},
    )
    new_mid = store.add(scope="paper", paper_id=1, kind="short", content="用户关注 retrieval 排序", importance=0.6)
    assert new_mid != old_mid
    assert len(_docs(store, kinds=("short",))) == 2


def test_write_conflict_fallback_heuristic_merges_and_preserves_stats(store, monkeypatch):
    # judge unavailable (autouse fixture) → heuristic path (ov > 0.7 → merge)
    old_mid = store.add(scope="paper", paper_id=1, kind="short",
                        content="attention is all you need transformer paper", importance=0.5)
    store.adjust_utility(old_mid, delta=0.9, reason="seed")
    new_mid = store.add(scope="paper", paper_id=1, kind="short",
                        content="attention is all you need transformer paper !", importance=0.6)
    assert new_mid == old_mid
    d = _docs(store, kinds=("short",))[0]
    assert d["utility"] == pytest.approx(0.9)  # old code lost stats (new uuid); now preserved


# ---------- A-MEM-style associative links ----------

def test_links_created_on_write(store, monkeypatch):
    a = store.add(scope="paper", paper_id=1, kind="short", content="RAG 重排序 研究", importance=0.6)
    monkeypatch.setattr(memory_judge, "judge_memory_links", lambda *, new_content, candidates: [a])
    b = store.add(scope="paper", paper_id=1, kind="short", content="colbert 延迟交互 重排序", importance=0.6)
    linked = store.store.linked_ids([a, b])
    assert linked.get(a) == [b] or linked.get(b) == [a]  # bidirectional view
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert "link" in events


def test_link_expansion_injects_related(store, monkeypatch):
    """A judged selection pulls its linked neighbor in as [关联] memory."""
    a = store.add(scope="paper", paper_id=1, kind="short", content="核心 结论 A", importance=0.6)
    b = store.add(scope="paper", paper_id=1, kind="short", content="关联 证据 B", importance=0.6)
    store.store.add_links(a, [b])
    # Judge selects only A; B should arrive via the link edge
    monkeypatch.setattr(
        memory_judge, "judge_select_memories",
        lambda *, candidates, purpose, context_hint="", max_items=10: [a],
    )
    text, _q, ids = store.build_chat_memory(paper_id=1)
    assert "核心 结论 A" in text
    assert "[关联] 关联 证据 B" in text
    assert set(ids) == {a, b}
    docs = {d["memory_id"]: d for d in _docs(store, kinds=("short",))}
    assert docs[a]["access_count"] == 1 and docs[b]["access_count"] == 1


def test_delete_cascades_links(store):
    a = store.add(scope="paper", paper_id=1, kind="short", content="甲 内容", importance=0.5)
    b = store.add(scope="paper", paper_id=1, kind="short", content="乙 内容", importance=0.5)
    store.store.add_links(a, [b])
    store.store.delete_memory(a)
    assert store.store.linked_ids([b]) == {}


def test_working_kind_not_linked(store, monkeypatch):
    called = []
    monkeypatch.setattr(
        memory_judge, "judge_memory_links",
        lambda *, new_content, candidates: called.append(1) or [],
    )
    store.add(scope="paper", paper_id=1, kind="working", content="用户问： 什么 是 RAG", importance=0.5)
    assert not called  # working (raw questions) never enters link generation


# ---------- health metrics aggregation ----------

def test_stats_health_aggregation(tmp_path, monkeypatch):
    import asyncio
    from app.api.routes import knowledge as knowledge_route

    reader_db = tmp_path / "reader_memory.db"
    agent_db = tmp_path / "agent_memory.db"
    monkeypatch.setattr(
        knowledge_route, "memory_db_paths",
        lambda: {"reader": str(reader_db), "agent": str(agent_db)},
    )
    s = MemoryStore(str(tmp_path / "papers.db"))
    m1 = s.add(scope="paper", paper_id=1, kind="short", content="有用 记忆", importance=0.6)
    m2 = s.add(scope="paper", paper_id=1, kind="short", content="有害 记忆", importance=0.6)
    s.adjust_utility(m1, delta=0.5, event="feedback_up", reason="t")
    s.adjust_utility(m2, delta=-0.5, event="feedback_down", reason="t")
    s.store.log_evolution(m1, "citation_click", reason="t")
    s.store.log_evolution(m2, "prune", reason="t")

    out = asyncio.run(knowledge_route.knowledge_stats(user={"user_id": 1}))
    h = out["health"]
    assert h["feedback"]["up"] == 1 and h["feedback"]["down"] == 1
    assert h["feedback"]["citation_click"] == 1
    assert h["feedback"]["up_ratio"] == pytest.approx(0.5)
    assert h["utility_distribution"]["positive"] == 1
    assert h["utility_distribution"]["negative"] == 1
    assert h["evolution_7d"].get("prune") == 1


# ---------- adversarial-review regressions ----------

def test_agent_memory_rapid_adds_both_survive(tmp_path):
    """memory_id second-granularity collision: two adds in the same second
    must not overwrite each other."""
    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    m1 = am.add(agent_name="knowledge_graph", content="图神经网络 引用预测 洞察", memory_type="working", importance=0.45)
    m2 = am.add(agent_name="knowledge_graph", content="蛋白质折叠 能量函数 洞察", memory_type="working", importance=0.45)
    assert m1 != m2
    rows = am.store.search_memories(user_id="papergraph:agent:knowledge_graph", memory_type="working", limit=10)
    assert len(rows) == 2


def test_cjk_overlap_reaches_conflict_judge(store, monkeypatch):
    """Pure-CJK near-duplicates (no whitespace tokens) must reach the judge."""
    store.add(scope="paper", paper_id=1, kind="short", content="用户偏好简洁的中文回答", importance=0.5)
    captured = {}
    monkeypatch.setattr(
        memory_judge, "judge_write_conflict",
        lambda *, new_content, candidates: captured.update(called=True) or {},
    )
    store.add(scope="paper", paper_id=1, kind="short", content="用户偏好简洁的中文回复", importance=0.5)
    assert captured.get("called") is True


def test_chat_memory_query_slice_disjoint_from_base(store, monkeypatch):
    """The query slice only takes memories the base block did not inject."""
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    for i in range(3):
        store.add(scope="paper", paper_id=1, kind="short", content=f"主题{i} 洞察", importance=0.6)
    base, qtext, ids = store.build_chat_memory(paper_id=1, query="主题")
    assert len(ids) == len(set(ids))
    for mid in ids:
        docs = _docs(store, kinds=("short",))
        d = [x for x in docs if x["memory_id"] == mid][0]
        assert d["access_count"] == 1
    # with only 3 memories all fit the base block; query slice is empty
    assert qtext == "" or "检索匹配" in qtext


def test_compress_deletes_only_summarized_batch(store, monkeypatch):
    """Compression must delete exactly the batch the summary covers."""
    monkeypatch.setattr(MemoryStore, "_summarize_via_llm", lambda self, docs: "压缩摘要")
    monkeypatch.setattr(MemoryStore, "_llm_is_duplicate", lambda self, a, b: False)
    topics = [
        "transformer attention", "graph neural citation", "contrastive retrieval",
        "bayesian uncertainty", "diffusion sampler", "federated gradient",
        "quantization lora", "retrieval augmented", "citation context",
        "prompt compression", "rope scaling", "experts routing",
        "reflection loop", "function calling", "embedding clustering",
        "temporal reasoning", "vision grounding", "code sandbox",
        "active learning", "knowledge distillation", "architecture search",
        "causal inference", "reward modeling", "speculative decoding",
        "mixture depth", "sparse attention", "kv cache",
        "beam search", "token healing", "watermark detection",
    ]
    for i in range(30):
        store.add(scope="paper", paper_id=1, kind="working", content=f"{topics[i]} 问题", importance=0.5)
    before = len(_docs(store, kinds=("working",)))
    assert before >= 26
    store.compress_working(scope="paper", paper_id=1, min_entries=6)
    after = len(_docs(store, kinds=("working",)))
    assert before - after == store.config.working_compress_batch  # exactly one batch
    logs = [e for e in store.store.list_evolution_log() if e["event"] == "prune"]
    assert len(logs) == store.config.working_compress_batch  # every deletion logged


def test_ttl_sweep_logs_prune(store, monkeypatch):
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    mid = store.store.add_memory(
        user_id="papergraph:global", content="过期 working", memory_type="working",
        importance=0.5, timestamp=time.time() - 7200,
    )
    store.build_chat_memory(paper_id=1)
    events = [e for e in store.store.list_evolution_log() if e["memory_id"] == mid]
    assert any(e["event"] == "prune" and "TTL" in e["reason"] for e in events)


# ---------- procedural memory (search strategy learning) ----------

def test_record_search_strategy_and_hints(tmp_path, monkeypatch):
    from app.api.routes.papers import _record_search_strategy

    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    monkeypatch.setattr("app.services.memory.agent_memory._AGENT_MEMORY_SINGLETON", None, raising=False)
    import app.services.memory.agent_memory as agent_memory_mod
    monkeypatch.setattr(agent_memory_mod, "_agent_memory_singleton", am)
    _record_search_strategy("RAG rerank 方法", ["ColBERT", "MonoT5"])
    rows = am.store.search_memories(user_id="papergraph:agent:search", memory_type="procedural", limit=10)
    assert len(rows) == 1
    assert "RAG rerank" in rows[0]["content"] and "ColBERT" in rows[0]["content"]
    # retrieval side: hints surface for a related query
    hints = am.procedural_hints(agent_name="search", query="RAG 重排序")
    assert any("RAG rerank" in h for h in hints)


def test_understand_intent_injects_procedural_hints(tmp_path, monkeypatch):
    from app.agents.search_agent import SearchAgent
    import app.services.memory.agent_memory as agent_memory_mod

    am = AgentMemory(db_path=str(tmp_path / "agent_memory.db"))
    am.add(agent_name="search", content="查询「RAG rerank」→ 用户保存了《ColBERT》",
           memory_type="procedural", importance=0.55, tags=["search", "procedural"])
    monkeypatch.setattr(agent_memory_mod, "_agent_memory_singleton", am)

    agent = SearchAgent.__new__(SearchAgent)
    captured = {}

    class _Parser:
        def parse(self, message, profile="accuracy"):
            captured["message"] = message
            from app.agents.support.search_models import SearchIntent
            return SearchIntent(query="x")

    agent.intent_parser = _Parser()
    agent.understand_intent("RAG 重排序怎么做")
    assert "用户历史上类似查询保存过的论文线索" in captured["message"]
    assert "ColBERT" in captured["message"]


# ---------- reflection (meta-memory) ----------

def _seed_activity(store: MemoryStore, n_events: int = 6) -> list[str]:
    mids = []
    for i, topic in enumerate(["重排序", "召回", "评测", "蒸馏", "剪枝", "量化"]):
        mid = store.add(scope="paper", paper_id=1, kind="short", content=f"{topic} 核心 结论", importance=0.6)
        mids.append(mid)
    store.store.increment_access(mids)
    for mid in mids[:n_events]:
        store.adjust_utility(mid, delta=0.4, event="feedback_up", reason="seed")
    return mids


def test_reflect_skipped_without_activity(store):
    from app.services.memory.memory_evolution import reflect_on_memory_db

    out = reflect_on_memory_db(db_path=store.db_path, db_kind="reader")
    assert out["skipped"] is True and out["reflected"] == 0
    assert _docs(store, scope="global", paper_id=None, kinds=("reflection",)) == []


def test_reflect_writes_meta_memories_with_links(store, monkeypatch):
    from app.services.memory.memory_evolution import reflect_on_memory_db

    mids = _seed_activity(store)
    monkeypatch.setattr(
        memory_judge, "judge_reflect",
        lambda *, evidence, existing_reflections=None: ["用户在检索话题上持续给出正反馈"],
    )
    out = reflect_on_memory_db(db_path=store.db_path, db_kind="reader")
    assert out["reflected"] == 1 and out["skipped"] is False
    reflections = _docs(store, scope="global", paper_id=None, kinds=("reflection",))
    assert len(reflections) == 1
    assert "正反馈" in reflections[0]["content"]
    linked = store.store.linked_ids([reflections[0]["memory_id"]])
    assert set(linked.get(reflections[0]["memory_id"], [])) <= set(mids)  # linked to evidence
    events = {e["event"] for e in store.store.list_evolution_log()}
    assert "reflection" in events


def test_reflect_gate_blocks_second_run_without_new_activity(store, monkeypatch):
    from app.services.memory.memory_evolution import reflect_on_memory_db

    _seed_activity(store)
    monkeypatch.setattr(
        memory_judge, "judge_reflect",
        lambda *, evidence, existing_reflections=None: ["元记忆一"],
    )
    first = reflect_on_memory_db(db_path=store.db_path, db_kind="reader")
    assert first["reflected"] == 1
    second = reflect_on_memory_db(db_path=store.db_path, db_kind="reader")
    assert second["reflected"] == 0  # no new events since the reflection → gated


def test_reflection_joins_context_block_candidates(store, monkeypatch):
    store.upsert_single(scope="global", paper_id=None, kind="reflection",
                        content="用户偏好 带例子 的解释", importance=0.8)
    captured = {}
    orig_rank = MemoryStore._rank_with_judge

    def _spy(self, docs, **kw):
        captured["kinds"] = {self._type_of(d) for d in docs}
        return orig_rank(self, docs, **kw)

    monkeypatch.setattr(MemoryStore, "_rank_with_judge", _spy)
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)
    store.build_chat_memory(paper_id=1)
    assert "reflection" in captured["kinds"]


# ---------- process_chat integration (regression: the metadata NameError) ----------

def test_process_chat_records_memory_ids_metadata(tmp_path, monkeypatch):
    """End-to-end through PaperReaderService.process_chat: reply succeeds and
    the assistant turn's metadata carries injected memory_ids (feedback
    attribution). Regression for an unbound-name 500 that only this level
    of test could catch."""
    import asyncio
    from app.core.paper import Paper
    from app.core.storage import PaperDatabase
    from app.services.reader.paper_reader_service import PaperReaderService
    from app.services.reader.paper_reader_history import list_turns

    db = PaperDatabase(str(tmp_path / "papers.db"))
    pid, _is_new = db.add_paper(Paper(title="RAG 综述", user_id=1))

    class _Agent:
        def paper_reader_reply(self, ctx, hist, msg, snap):
            return "这是回答 [p1]", [], [], [{"marker": "[p1]", "page": 1}]

    svc = PaperReaderService(db=db, agent=_Agent())

    class _BG:
        def add_task(self, fn, *a, **kw):
            pass  # background tasks not exercised here

    out = asyncio.run(svc.process_chat(
        paper_id=pid, user_id=1, messages=[], user_message="这篇讲什么", background_tasks=_BG(),
    ))
    assert out["reply"] == "这是回答 [p1]"
    assert out["assistant_turn_id"] is not None
    from app.services.reader.paper_reader_history import get_turn
    turn = get_turn(db.db_path, turn_id=out["assistant_turn_id"], user_id=1)
    assert turn is not None
    meta = json.loads(turn["metadata"] or "{}")
    assert "memory_ids" in meta  # feedback attribution chain intact
    assert meta["citation_count"] == 1


def test_working_memories_reach_judge_under_full_buckets(store, monkeypatch):
    """Bucket quotas sum to <= the judge candidate cap: even with a full
    semantic-recall bucket prepended, working memories must reach the judge
    (a global head-truncate previously cut them all)."""
    from app.services.memory import memory_semantic

    # Fill semantic recall with 8 long-tail docs (cap is 6 after the fix)
    tail_ids = []
    for i in range(8):
        tail_ids.append(store.add(scope="paper", paper_id=1, kind="long", content=f"长尾主题{i} 洞察", importance=0.4))
    monkeypatch.setattr(
        memory_semantic, "semantic_recall",
        lambda store_, *, docs, query: [d for d in docs if d["memory_id"] in set(tail_ids)],
    )
    working_mid = store.add(scope="paper", paper_id=1, kind="working", content="用户问： 当前 问题", importance=0.5)

    captured = {}
    def _spy(candidates, *, purpose, context_hint="", max_items=10):
        captured["ids"] = {str(d.get("memory_id")) for d in candidates}
        return None  # force recency fallback
    monkeypatch.setattr(memory_judge, "judge_select_memories", _spy)
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)

    store.build_chat_memory(paper_id=1, query="当前")
    assert working_mid in captured["ids"]  # working bucket reaches the judge
    assert len(captured["ids"]) <= 24  # within the judge candidate cap
