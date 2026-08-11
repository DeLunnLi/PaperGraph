"""The two paper-identity implementations must stay in agreement.

`PaperSearcher._paper_dedupe_key` (cross-source merge) and
`rrf_fusion._paper_identity` (multi-list fusion) implement the same rule twice —
the latter's docstring even says "same logic as PaperSearcher._paper_dedupe_key".
Nothing enforced that, so a change to one would silently drift from the other
and the same paper would dedupe in one path but appear twice in the other.

These tests pin the shared contract. If you intentionally change the identity
rule, change both and update this file.
"""
from __future__ import annotations

import pytest

from app.core.author import Author
from app.core.paper import Paper
from app.core.search.paper_searcher import PaperSearcher
from app.services.retrieval.rrf_fusion import _paper_identity


def _paper(**kwargs) -> Paper:
    base = {"title": "A Title", "authors": [Author(name="A. Author")], "source": "arxiv"}
    base.update(kwargs)
    return Paper(**base)


@pytest.fixture(scope="module")
def searcher() -> PaperSearcher:
    return PaperSearcher()


@pytest.mark.parametrize(
    "paper_kwargs",
    [
        {"arxiv_id": "1706.03762v5"},
        {"arxiv_id": "1706.03762"},
        {"doi": "10.48550/arXiv.1706.03762"},
        {"doi": "10.1145/3292500.3330989"},
        {"pmid": "12345678"},
        {"pmc_id": "pmc9876543"},
        {"title": "Attention Is All You Need"},
        {"title": "attention is all you need."},
    ],
    ids=lambda kw: next(iter(kw)),
)
def test_both_identity_impls_agree(searcher: PaperSearcher, paper_kwargs: dict) -> None:
    paper = _paper(**paper_kwargs)
    assert searcher._paper_dedupe_key(paper) == _paper_identity(paper)


def test_untitled_papers_stay_distinct_in_both(searcher: PaperSearcher) -> None:
    """No identifier and no title must never collapse two different papers."""
    a, b = _paper(title=""), _paper(title="")
    assert searcher._paper_dedupe_key(a) != searcher._paper_dedupe_key(b)
    assert _paper_identity(a) != _paper_identity(b)


@pytest.mark.parametrize(
    "left,right",
    [
        ({"arxiv_id": "1706.03762v5"}, {"arxiv_id": "1706.03762"}),
        ({"arxiv_id": "1706.03762"}, {"doi": "10.48550/arXiv.1706.03762"}),
        ({"title": "Attention Is All You Need"}, {"title": "attention is all you need."}),
    ],
)
def test_equivalent_papers_share_identity(
    searcher: PaperSearcher, left: dict, right: dict
) -> None:
    """Version suffixes, arXiv DOIs and title casing must not create duplicates."""
    lp, rp = _paper(**left), _paper(**right)
    assert searcher._paper_dedupe_key(lp) == searcher._paper_dedupe_key(rp)
    assert _paper_identity(lp) == _paper_identity(rp)


def test_cross_source_duplicates_collapse(searcher: PaperSearcher) -> None:
    """End-to-end: the same paper from three sources dedupes to one."""
    papers = [
        _paper(arxiv_id="1706.03762v5", source="arxiv", abstract="x" * 100),
        _paper(arxiv_id="1706.03762", source="dblp"),
        _paper(doi="10.48550/arXiv.1706.03762", source="crossref"),
    ]
    merged = searcher._smart_deduplicate(papers)
    assert len(merged) == 1
    # The richest record (has an abstract) must win.
    assert merged[0].abstract
