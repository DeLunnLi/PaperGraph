"""arXiv query builder must treat a boolean OR as a phrase separator.

Regression: split_query_phrases() only splits on , ; and newlines, so a query
like '"a" OR "b"' collapsed into a single literal phrase and arXiv was asked for
all:"a  OR  b". That still returns 200 OK with entries, but they are poor,
stale matches — so the days_back filter dropped every one of them and the daily
recommendations page rendered empty.
"""
from __future__ import annotations

import pytest

from app.core.search.sources.arxiv import _arxiv_build_search_query, _arxiv_query_part


class _Searcher:
    """_arxiv_query_part only needs an object handle; nothing is read off it."""


@pytest.fixture()
def searcher() -> _Searcher:
    return _Searcher()


def test_or_query_becomes_grouped_or_not_literal_phrase(searcher: _Searcher) -> None:
    part = _arxiv_query_part(searcher, '"knowledge graph" OR "retrieval augmented generation"', "ti_abs")
    assert part == '(all:"knowledge graph"+OR+all:"retrieval augmented generation")'
    # The bug signature: both terms fused into one quoted phrase.
    assert "knowledge graph  OR  retrieval" not in part


def test_multiple_or_terms_all_split(searcher: _Searcher) -> None:
    part = _arxiv_query_part(searcher, "alpha OR beta OR gamma", "all")
    assert part == '(all:alpha+OR+all:beta+OR+all:gamma)'


def test_lowercase_or_also_splits(searcher: _Searcher) -> None:
    part = _arxiv_query_part(searcher, '"graph neural network" or "knowledge graph"', "ti_abs")
    assert part == '(all:"graph neural network"+OR+all:"knowledge graph")'


@pytest.mark.parametrize(
    "query",
    [
        "sensor networks",          # 'or' inside "sensor"
        "Oregon wildfire",          # 'OR' starting a word
        "reservoir computing",      # 'or' mid-word
    ],
)
def test_or_inside_words_is_not_split(searcher: _Searcher, query: str) -> None:
    """Word boundaries keep real words intact."""
    part = _arxiv_query_part(searcher, query, "ti_abs")
    assert part == f'all:"{query}"'


def test_plain_phrase_is_unchanged(searcher: _Searcher) -> None:
    part = _arxiv_query_part(searcher, "retrieval augmented generation", "ti_abs")
    assert part == 'all:"retrieval augmented generation"'


def test_full_search_expression_keeps_category_and_groups_or(searcher: _Searcher) -> None:
    expr = _arxiv_build_search_query(
        searcher,
        '"knowledge graph" OR "retrieval augmented generation"',
        None,
        arxiv_query_style="ti_abs",
        arxiv_categories=["cs.CL"],
    )
    assert expr == (
        'cat:cs.CL+AND+(all:"knowledge graph"+OR+all:"retrieval augmented generation")'
    )
