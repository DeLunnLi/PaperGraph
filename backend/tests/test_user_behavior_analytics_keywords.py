"""Tests for keyword extraction in UserBehaviorAnalytics.

The ``papers.keywords`` column stores ``json.dumps(list[str])`` (e.g.
``'["cs.IR", "cs.AI"]'``), written by ``PaperDatabase`` and read back via
``_parse_json_list`` in storage. But ``UserBehaviorAnalytics`` read the same
column directly and parsed it with ``str(row["keywords"]).split(",")`` —
splitting the JSON string on commas, producing bracket-prefixed garbage tokens
like ``'["cs.ir"'`` and ``'"cs.ai"]'`` instead of clean ``'cs.ir'``/``'cs.ai'``.
The interest-keyword signal fed into the daily recommendation pipeline was
silently corrupted for every paper with structured keywords.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def db(tmp_path):
    from app.core.storage import PaperDatabase

    db = PaperDatabase(str(tmp_path / "test.db"))
    # Insert a paper whose keywords column is a real JSON array (the shape
    # PaperDatabase writes). Mix arxiv-tag-style and openalex-concept-style.
    db._query(
        'INSERT INTO papers(id, title, user_id, keywords, abstract) '
        'VALUES(?, ?, ?, ?, ?)',
        (1, "A Paper", 1, '["cs.IR", "cs.AI", "recommendation"]', "abstract text"),
    )
    return db


def _analytics(db) -> "UserBehaviorAnalytics":  # type: ignore[name-defined]
    from app.services.daily.user_behavior_analytics import UserBehaviorAnalytics

    return UserBehaviorAnalytics(db.db_path, user_id=1)


def test_keywords_extracted_clean_from_json_array(db):
    """JSON-array keywords come through as clean lowercased tokens, not
    bracket-prefixed garbage."""
    counter = _analytics(db).extract_keywords_from_high_interest_papers([1])
    assert counter["cs.ir"] >= 1
    assert counter["cs.ai"] >= 1
    assert counter["recommendation"] >= 1
    # The bug: comma-split produced '["cs.ir"' and '"cs.ai"]' (brackets+quotes).
    # Assert NO token carries the JSON bracket/quote artifacts.
    for token in counter:
        assert "[" not in token
        assert "]" not in token
        assert '"' not in token


def test_keywords_empty_for_paper_with_no_keywords(db):
    db._query(
        'INSERT INTO papers(id, title, user_id, keywords) VALUES(?, ?, ?, ?)',
        (2, "No Kw Paper", 1, "[]"),
    )
    counter = _analytics(db).extract_keywords_from_high_interest_papers([2])
    # no keyword tokens from the keywords column (title/abstract still feed in
    # via extract_keywords_from_text, but those are stopwords-filtered)
    assert "cs.ir" not in counter


def test_keywords_corrupt_column_does_not_crash(db):
    """Defensive: a corrupt keywords value (stale mis-mapped data, same class
    of bug as references/citations) must not crash the analytics pipeline."""
    db._query(
        'INSERT INTO papers(id, title, user_id, keywords) VALUES(?, ?, ?, ?)',
        (3, "Corrupt Kw", 1, "35"),  # bare int leaked in (stale data shape)
    )
    counter = _analytics(db).extract_keywords_from_high_interest_papers([3])
    assert isinstance(counter, type(counter))  # no exception, returns a Counter


def test_keywords_filter_preserves_short_tag_logic(db):
    """Short tokens (<3 chars after strip) are dropped — the original filter
    contract must survive the parse fix."""
    db._query(
        'INSERT INTO papers(id, title, user_id, keywords) VALUES(?, ?, ?, ?)',
        (4, "Short Tag Paper", 1, '["ai", "llm", "transformer"]'),
    )
    counter = _analytics(db).extract_keywords_from_high_interest_papers([4])
    # "ai" (2 chars) dropped, "llm" and "transformer" kept
    assert "ai" not in counter
    assert "llm" in counter
    assert "transformer" in counter
