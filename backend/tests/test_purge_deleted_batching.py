"""Regression test: purge_deleted must batch DELETEs to stay within SQLite's
SQLITE_MAX_VARIABLE_NUMBER limit.

The original code built a single ``DELETE … WHERE src IN (?*N) OR dst IN (?*N)``
statement with 2N bound variables.  On SQLite builds with the historical default
limit of 999 (pre-3.32) or 32766 (>=3.32), this raises OperationalError when
the number of soft-deleted memories exceeds ~499 or ~16383 rows respectively.
"""
from __future__ import annotations

import pytest

from app.services.memory.sqlite_document_store_compat import SQLiteDocumentStore


@pytest.fixture()
def store(tmp_path) -> SQLiteDocumentStore:
    # tmp_path (not tempfile.mktemp) — mktemp can hand back a previously used
    # path after unlink, and ``ensure_once`` caches schema init keyed on the
    # db_path string, so a recycled path silently skips table creation and
    # pollutes unrelated tests.
    return SQLiteDocumentStore(str(tmp_path / "memory.db"))


class TestPurgeDeletedBatching:
    def test_purge_small_batch(self, store: SQLiteDocumentStore) -> None:
        """Normal case: a few soft-deleted memories are purged correctly."""
        for i in range(5):
            store.add_memory(f"user_{i}", f"mem {i}", memory_type="working")
        # Soft-delete two
        mids = [store.add_memory("u1", "to_delete_1"), store.add_memory("u1", "to_delete_2")]
        assert store.delete_memory(mids[0])
        assert store.delete_memory(mids[1])
        # Make them old enough to purge
        with store._get_conn() as conn:
            conn.execute("UPDATE memories SET deleted_at = 1 WHERE deleted_at > 0")
            conn.commit()
        count = store.purge_deleted(before_days=0)
        assert count == 2

    def test_purge_large_batch_exceeds_variable_limit(self, store: SQLiteDocumentStore) -> None:
        """More than 500 soft-deleted memories must not raise OperationalError."""
        mids: list[str] = []
        for i in range(1200):
            mid = store.add_memory("u1", f"bulk_delete_{i}")
            mids.append(mid)
        for mid in mids:
            store.delete_memory(mid)
        # Force deleted_at to the distant past so before_days=0 reaps them
        with store._get_conn() as conn:
            conn.execute("UPDATE memories SET deleted_at = 1 WHERE deleted_at > 0")
            conn.commit()
        count = store.purge_deleted(before_days=0)
        assert count == 1200

    def test_purge_cleans_links_and_embeddings(self, store: SQLiteDocumentStore) -> None:
        """Purging must also remove linked rows in memory_links and memory_embeddings."""
        mid1 = store.add_memory("u1", "source mem")
        mid2 = store.add_memory("u1", "target mem")
        store.add_links(mid1, [mid2], reason="related")
        store.upsert_embedding(mid1, [0.1, 0.2, 0.3])
        store.upsert_embedding(mid2, [0.4, 0.5, 0.6])
        # Soft-delete both
        store.delete_memory(mid1)
        store.delete_memory(mid2)
        with store._get_conn() as conn:
            conn.execute("UPDATE memories SET deleted_at = 1 WHERE deleted_at > 0")
            conn.commit()
        count = store.purge_deleted(before_days=0)
        assert count == 2
        # Verify cascading cleanup
        with store._get_conn() as conn:
            links = conn.execute("SELECT COUNT(*) FROM memory_links").fetchone()[0]
            embeds = conn.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0]
            mems = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert links == 0
        assert embeds == 0
        assert mems == 0

    def test_purge_preserves_live_memories(self, store: SQLiteDocumentStore) -> None:
        """Only soft-deleted memories are purged; live ones stay."""
        live_mid = store.add_memory("u1", "I am alive")
        dead_mid = store.add_memory("u1", "I am dead")
        store.delete_memory(dead_mid)
        with store._get_conn() as conn:
            conn.execute("UPDATE memories SET deleted_at = 1 WHERE deleted_at > 0")
            conn.commit()
        count = store.purge_deleted(before_days=0)
        assert count == 1
        live = store.get_memories_by_ids([live_mid])
        assert len(live) == 1
        assert live[0]["content"] == "I am alive"
