"""Tests for storage v4 index migration + paper_ids_missing_local_pdf — locks
in the round-1 perf fixes (user_id-leading indexes + batched missing-pdf lookup
that replaced an N+1 get_paper_by_id loop on the save path).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def db(tmp_path):
    from app.core.storage import PaperDatabase
    return PaperDatabase(str(tmp_path / "test.db"))


def test_v4_migration_creates_user_scoped_indexes(db):
    """Opening a fresh DB reaches user_version=4 and creates the hot-path indexes."""
    with sqlite3.connect(db.db_path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == 4
        names = {row[1] for row in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE type='index'"
        )}
    for idx in (
        "idx_papers_user_id",
        "idx_papers_user_created",
        "idx_papers_user_category",
        "idx_authors_name",
        "idx_paper_authors_paper",
    ):
        assert idx in names, f"missing index {idx}"


def test_paper_ids_missing_local_pdf_scopes_by_user(db):
    db._query("INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)", (1, "u1 missing", 1, None))
    db._query("INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)", (2, "u1 has", 1, "pdfs/2.pdf"))
    db._query("INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)", (3, "u2 missing", 2, None))
    db._query("INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)", (4, "u1 empty", 1, "   "))

    missing = db.paper_ids_missing_local_pdf([1, 2, 3, 4], user_id=1)
    # Only user 1's papers with NULL/empty path: ids 1 and 4 (not 2 which has a path,
    # not 3 which belongs to user 2).
    assert missing == [1, 4]


def test_paper_ids_missing_local_pdf_chunks_large_in_list(db):
    """Verify the 900-variable chunking handles >900 ids without error."""
    ids = []
    for i in range(1, 950):
        db._query("INSERT INTO papers(id, title, user_id, local_pdf_path) VALUES(?, ?, ?, ?)", (i, f"p{i}", 1, None))
        ids.append(i)
    missing = db.paper_ids_missing_local_pdf(ids, user_id=1)
    assert len(missing) == 949
