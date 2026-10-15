"""get_all_papers() pagination must work with offset alone.

Regression: the query appended `OFFSET n` only when `offset` was truthy, and
`LIMIT n` only when `limit` was truthy. Since `limit` defaults to None, calling
`get_all_papers(offset=20)` produced

    SELECT * FROM papers ORDER BY created_at DESC OFFSET 20

and SQLite rejects OFFSET without a preceding LIMIT ("near \"OFFSET\": syntax
error"). `LIMIT -1` is SQLite's documented "no limit" sentinel.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.core.author import Author
from app.core.paper import Paper
from app.core.storage import PaperDatabase

TOTAL = 5


@pytest.fixture()
def db() -> PaperDatabase:
    store = PaperDatabase(str(Path(tempfile.mkdtemp()) / "papers.db"))
    for i in range(TOTAL):
        store.add_paper(
            Paper(title=f"Paper {i}", authors=[Author(name="A. Author")], source="arxiv")
        )
    return store


def _titles(store: PaperDatabase, **kwargs) -> list[str]:
    return [p.title for p in store.get_all_papers(order_by="title ASC", **kwargs)]


def test_no_pagination_returns_everything(db: PaperDatabase) -> None:
    assert _titles(db) == [f"Paper {i}" for i in range(TOTAL)]


def test_offset_without_limit_does_not_raise(db: PaperDatabase) -> None:
    """The exact call that used to raise OperationalError."""
    assert _titles(db, offset=2) == ["Paper 2", "Paper 3", "Paper 4"]


def test_limit_with_offset(db: PaperDatabase) -> None:
    assert _titles(db, limit=2, offset=1) == ["Paper 1", "Paper 2"]


def test_limit_without_offset(db: PaperDatabase) -> None:
    assert _titles(db, limit=3) == ["Paper 0", "Paper 1", "Paper 2"]


def test_offset_beyond_end_returns_empty(db: PaperDatabase) -> None:
    assert _titles(db, offset=TOTAL + 10) == []


def test_offset_zero_is_equivalent_to_no_offset(db: PaperDatabase) -> None:
    assert _titles(db, offset=0) == _titles(db)


def test_pages_tile_the_full_set_without_gaps_or_overlap(db: PaperDatabase) -> None:
    page_size = 2
    seen: list[str] = []
    for page in range((TOTAL + page_size - 1) // page_size):
        seen.extend(_titles(db, limit=page_size, offset=page * page_size))
    assert seen == [f"Paper {i}" for i in range(TOTAL)]
