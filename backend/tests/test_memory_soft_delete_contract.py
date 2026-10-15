"""Contract tests locking the CURRENT deletion behaviour (physical delete).

These exist as a regression net BEFORE the Karpathy soft-delete refactor
(plan stage 1a). They assert the behaviour that the refactor will CHANGE:
today ``delete_memory`` physically removes the row, so deleted memories are
absent from every read path.

When stage 1a lands (soft delete via ``deleted_at`` column), these tests are
EXPECTED to fail and must be updated to the new contract:
  - deleted memory absent from default reads (still true — reads filter deleted_at=0)
  - deleted memory PRESENT via ``list_all_memories(include_deleted=True)`` (new)
  - ``deleted_at`` column exists and is >0 after deletion (new)

Locking the current contract first makes the behaviour change explicit and
reviewable: the diff of these tests at stage 1a IS the behaviour contract delta.
"""
from __future__ import annotations

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
from app.services.memory.memory_store import MemoryStore
from app.services.memory.sqlite_document_store_compat import SQLiteDocumentStore


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Deterministic tests: never call a real LLM even if the env has a key.
    Mirrors the autouse fixture in test_memory_evolution.py."""
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)


# ---------- current contract: delete is physical ----------

def test_delete_memory_soft_deletes_row(tmp_path):
    """Stage 1a contract: delete_memory marks deleted_at (soft), row stays.

    Default reads filter it out; include_deleted=True surfaces it for the KB
    admin view. The row is recoverable until purge_deleted reaps it.
    """
    store = SQLiteDocumentStore(str(tmp_path / "test.db"))
    mid = store.add_memory(
        memory_id="m1", user_id="u1", content="hello",
        memory_type="working", timestamp=time.time(),
        importance=0.5, properties={},
    )
    assert store.search_memories("u1", "working", limit=10)

    deleted = store.delete_memory(mid)

    assert deleted is True
    # Default read filters deleted memories out.
    assert store.search_memories("u1", "working", limit=10) == []
    # Row is still in the table (soft delete, not physical).
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = ?", (mid,),
        ).fetchone()
    assert row is not None
    assert row[0] > 0  # deleted_at set
    # Admin view surfaces retired memories.
    assert len(store.search_memories("u1", "working", limit=10, include_deleted=True)) == 1


def test_delete_memory_drops_embedding_keeps_links_until_purge(tmp_path):
    """Soft delete drops the embedding vector immediately (no recall for retired
    memories) but keeps link rows until purge_deleted reaps them — links
    naturally filter because both endpoints become invisible to default reads.
    """
    store = SQLiteDocumentStore(str(tmp_path / "test.db"))
    mid = store.add_memory(
        memory_id="m1", user_id="u1", content="hello",
        memory_type="working", timestamp=time.time(),
        importance=0.5, properties={},
    )
    store.upsert_embedding(mid, [0.1, 0.2, 0.3])
    store.add_links(mid, ["other"], reason="test")

    store.delete_memory(mid)

    with sqlite3.connect(store.db_path) as conn:
        emb = conn.execute("SELECT COUNT(*) FROM memory_embeddings WHERE memory_id = ?", (mid,)).fetchone()[0]
        links = conn.execute(
            "SELECT COUNT(*) FROM memory_links WHERE src = ? OR dst = ?", (mid, mid),
        ).fetchone()[0]
    assert emb == 0          # embedding dropped immediately
    assert links == 1        # link kept until purge

    # purge_deleted only reaps after TTL; with before_days huge, nothing purged.
    assert store.purge_deleted(before_days=10**6) == 0
    # Backdate deleted_at so purge reaps it.
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE memories SET deleted_at = ? WHERE memory_id = ?", (time.time() - 999999, mid))
        conn.commit()
    assert store.purge_deleted(before_days=1) == 1
    with sqlite3.connect(store.db_path) as conn:
        links_after = conn.execute(
            "SELECT COUNT(*) FROM memory_links WHERE src = ? OR dst = ?", (mid, mid),
        ).fetchone()[0]
    assert links_after == 0  # purge cascades links


def test_delete_memory_returns_false_for_unknown_id(tmp_path):
    store = SQLiteDocumentStore(str(tmp_path / "test.db"))
    assert store.delete_memory("does-not-exist") is False


def test_memory_store_delete_docs_soft_deletes(tmp_path):
    """MemoryStore._delete_docs (TTL sweep / compress / evolve merge) soft-deletes:
    retired memories vanish from default reads but stay recoverable.
    """
    s = MemoryStore(str(tmp_path / "papers.db"))
    s.add(scope="paper", paper_id=1, kind="working", content="x", importance=0.5)
    docs = s._list_recent_docs(scope="paper", paper_id=1, kinds=["working"], limit_per_kind=10)
    assert docs

    s._delete_docs(docs)

    assert s._list_recent_docs(scope="paper", paper_id=1, kinds=["working"], limit_per_kind=10) == []
    # Row still present, just soft-deleted (deleted_at > 0).
    mid = docs[0].get("memory_id") or docs[0].get("id")
    with sqlite3.connect(s.db_path) as conn:
        deleted_at = conn.execute(
            "SELECT deleted_at FROM memories WHERE memory_id = ?", (mid,),
        ).fetchone()
    assert deleted_at is not None and deleted_at[0] > 0


def test_agent_memory_eviction_soft_deletes(tmp_path, monkeypatch):
    """AgentMemory._evict_old_memories soft-deletes (recency fallback, no judge):
    evicted memories are retired (deleted_at>0), not physically removed.
    """
    am = AgentMemory(db_path=str(tmp_path / "agent.db"))
    cap = AgentMemory.MAX_EPISODIC_PER_AGENT
    # Distinctive content (varied tokens) so TF-IDF dedup doesn't collapse them
    # before the cap is reached — eviction must actually fire.
    topics = ["transformer attention", "graph neural net", "reinforce policy gradient",
              "diffusion noise schedule", "mixture of experts routing", "contrastive loss",
              "quantization int8", "pruning lottery ticket", "distillation soft target",
              "embedding cosine", "beam search decoding", "dropout regularization",
              "batch norm statistics", "residual skip connection", "positional encoding",
              "layer normalization", "sgd momentum", "adam optimizer", "learning rate warmup",
              "weight decay", "data augmentation crop", "early stopping patience",
              "gradient clipping", "label smoothing"]
    for i in range(cap + 3):
        am.add(agent_name="a", content=topics[i % len(topics)] + f" #{i}", memory_type="episodic", importance=0.5)

    from app.services.memory import memory_judge
    monkeypatch.setattr(memory_judge, "_llm_or_none", lambda: None)

    rows = am.store.search_memories(
        user_id="papergraph:agent:a", memory_type="episodic", limit=cap + 10,
    )
    assert len(rows) <= cap  # live count bounded
    # Evicted memories are soft-deleted, not physically gone.
    with sqlite3.connect(am.db_path) as conn:
        retired = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE memory_type = 'episodic' "
            "AND user_id = ? AND deleted_at > 0",
            ("papergraph:agent:a",),
        ).fetchone()[0]
    assert retired >= 3  # the overflow was retired (soft)
