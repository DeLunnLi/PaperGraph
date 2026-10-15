"""Tests for robust deserialization of paper rows.

Guards the pre-existing data-integrity bug where 5 dev-DB rows had their
columns mis-mapped by an older write path: the ``references`` column held a
bare citation-count int (e.g. ``'35'`` -> ``json.loads`` -> ``35``) and the
``citations`` column held a source-name string (e.g. ``'openalex'``). The
load path passed these through unvalidated, so ``paper.references`` became an
``int`` and ``paper.citations`` became a ``str`` — the reader's
``format_paper_reader_block`` then crashed with
``TypeError: 'int' object is not subscriptable`` on ``refs[:120]``.

The deserialization boundary (``_row_to_paper_fast``) must guarantee the
Paper model's type contract regardless of what is stored in the DB.
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


def _insert(db, *, pid=1, references=None, citations=None, keywords=None, tags=None):
    db._query(
        "INSERT INTO papers(id, title, user_id, keywords, mesh_terms, \"references\", "
        "citations, source, tags) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid,
            f"Paper {pid}",
            1,
            keywords if keywords is not None else "[]",
            "[]",
            references if references is not None else "[]",
            citations if citations is not None else 0,
            "openalex",
            tags if tags is not None else "[]",
        ),
    )


# ---------------------------------------------------------------------------
# references: must always be a list, even if the column holds a bare int.
# ---------------------------------------------------------------------------


def test_load_handles_corrupt_references_as_int(db):
    """references column = '35' (a leaked citation count) -> paper.references == []"""
    _insert(db, pid=10, references="35")
    p = db.get_paper_by_id(10, user_id=1)
    assert p is not None
    assert p.references == []  # not 35


def test_load_handles_corrupt_references_as_non_json(db):
    _insert(db, pid=11, references="not even json")
    p = db.get_paper_by_id(11, user_id=1)
    assert p.references == []


def test_load_preserves_valid_references(db):
    _insert(db, pid=12, references='["Smith 2020", "Jones 2021"]')
    p = db.get_paper_by_id(12, user_id=1)
    assert p.references == ["Smith 2020", "Jones 2021"]


# ---------------------------------------------------------------------------
# citations: must always be an int, even if the column holds a source name.
# ---------------------------------------------------------------------------


def test_load_handles_corrupt_citations_as_string(db):
    """citations column = 'openalex' (a leaked source name) -> paper.citations == 0"""
    _insert(db, pid=20, citations="openalex")
    p = db.get_paper_by_id(20, user_id=1)
    assert p.citations == 0
    assert isinstance(p.citations, int)


def test_load_handles_corrupt_citations_as_garbage(db):
    _insert(db, pid=21, citations="not-a-number")
    p = db.get_paper_by_id(21, user_id=1)
    assert p.citations == 0


def test_load_preserves_valid_citations(db):
    _insert(db, pid=22, citations=42)
    p = db.get_paper_by_id(22, user_id=1)
    assert p.citations == 42


def test_load_preserves_citations_as_numeric_string(db):
    """A numeric string like '42' should coerce to int 42 (SQLite may store text)."""
    _insert(db, pid=23, citations="42")
    p = db.get_paper_by_id(23, user_id=1)
    assert p.citations == 42


# ---------------------------------------------------------------------------
# Sibling JSON-list fields share the same failure mode — harden them too.
# ---------------------------------------------------------------------------


def test_load_handles_corrupt_keywords_as_int(db):
    _insert(db, pid=30, keywords="35")
    p = db.get_paper_by_id(30, user_id=1)
    assert p.keywords == []


def test_load_handles_corrupt_tags_as_non_list(db):
    _insert(db, pid=31, tags='"a string not a list"')
    p = db.get_paper_by_id(31, user_id=1)
    assert p.tags == []


def test_load_handles_null_tags(db):
    """tags = NULL (real data has this) -> [] not None."""
    _insert(db, pid=32, tags=None)
    p = db.get_paper_by_id(32, user_id=1)
    assert p.tags == []


# ---------------------------------------------------------------------------
# End-to-end: the reader block formatter must not crash on corrupt data.
# ---------------------------------------------------------------------------


def test_format_reader_block_does_not_crash_on_corrupt_paper(db):
    """The original crash: refs was an int, refs[:120] raised TypeError."""
    _insert(db, pid=40, references="35", citations="openalex")
    p = db.get_paper_by_id(40, user_id=1)
    from app.services.reader.paper_reader_context import format_paper_reader_block

    block = format_paper_reader_block(p, pdf_excerpt="excerpt", references_section_raw="", reader_artifact_block="")
    assert isinstance(block, str)
    assert "Paper 40" in block  # title rendered
    # references section renders the honest "no structured references" fallback
    assert "库表未保存结构化 references" in block or "【参考文献】" in block
