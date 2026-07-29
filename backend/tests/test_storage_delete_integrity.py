"""Tests for storage delete_paper cascade + _add_paper_internal IntegrityError
race — locks in round-6 robustness fixes.
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


def test_delete_paper_cascades_relations_and_turns(db):
    """delete_paper must clean paper_relations + paper_reader_turns, not orphan them."""
    db._query("INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)", (1, "P", 7))
    # Create the optional tables (as other modules do lazily) + seed rows.
    with sqlite3.connect(db.db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_relations(
                id INTEGER PRIMARY KEY, source_paper_id INTEGER, target_paper_id INTEGER,
                relation TEXT, score REAL, evidence TEXT,
                created_at INTEGER, updated_at INTEGER);
            CREATE TABLE IF NOT EXISTS paper_reader_turns(
                id INTEGER PRIMARY KEY, paper_id INTEGER, user_id INTEGER,
                role TEXT, content TEXT, created_at INTEGER, metadata TEXT);
            INSERT INTO paper_relations(source_paper_id, target_paper_id, relation) VALUES(1, 2, 'cites');
            INSERT INTO paper_relations(source_paper_id, target_paper_id, relation) VALUES(3, 1, 'cited-by');
            INSERT INTO paper_reader_turns(paper_id, user_id, role, content, created_at) VALUES(1, 7, 'user', 'hi', 100);
            """
        )

    assert db.delete_paper(1, user_id=7) is True

    with sqlite3.connect(db.db_path) as conn:
        rels = conn.execute("SELECT COUNT(*) FROM paper_relations WHERE source_paper_id=1 OR target_paper_id=1").fetchone()[0]
        turns = conn.execute("SELECT COUNT(*) FROM paper_reader_turns WHERE paper_id=1").fetchone()[0]
        papers = conn.execute("SELECT COUNT(*) FROM papers WHERE id=1").fetchone()[0]
    assert rels == 0
    assert turns == 0
    assert papers == 0


def test_delete_paper_rejects_wrong_user(db):
    db._query("INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)", (1, "P", 7))
    assert db.delete_paper(1, user_id=8) is False
    # Paper still exists (wrong-user delete is a no-op).
    assert db.get_paper_by_id(1, user_id=7) is not None


def test_delete_paper_succeeds_even_without_optional_tables(db):
    """A fresh DB has no paper_relations/paper_reader_turns tables; delete must
    not raise (the cascade tolerates their absence)."""
    db._query("INSERT INTO papers(id, title, user_id) VALUES(?, ?, ?)", (1, "P", 7))
    assert db.delete_paper(1, user_id=7) is True


def test_add_paper_concurrent_duplicate_returns_existing_not_failure(db):
    """A duplicate insert (same DOI, same user) must return the existing id with
    is_new=False rather than raising. The check-then-insert path handles the
    non-race duplicate; the IntegrityError catch handles the race."""
    from app.core.paper import Paper
    p1 = Paper(title="Dup", doi="10.1/x", user_id=7)
    id1, is_new1 = db.add_paper(p1)
    assert is_new1 is True

    p2 = Paper(title="Dup", doi="10.1/x", user_id=7)
    id2, is_new2 = db.add_paper(p2)
    assert id2 == id1
    assert is_new2 is False
