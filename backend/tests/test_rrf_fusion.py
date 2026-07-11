"""Tests for RRF (Reciprocal Rank Fusion) algorithm."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.paper import Paper
from app.services.retrieval.rrf_fusion import rrf_fuse, rrf_fuse_recorded, RecordedCandidate


def _mk(title: str, doi: str = "", arxiv_id: str = "") -> Paper:
    return Paper(title=title, doi=doi or None, arxiv_id=arxiv_id or None)


def test_rrf_basic_two_lists():
    """Paper appearing in 2 lists at rank 1 gets score 2/(60+1)."""
    a = _mk("Paper A", doi="10.1/a")
    b = _mk("Paper B", doi="10.1/b")
    c = _mk("Paper C", doi="10.1/c")

    list1 = [a, b, c]
    list2 = [b, a, c]

    fused = rrf_fuse([list1, list2], k=60)
    scores = {p.title: s for p, s in fused}

    # A: rank 1 in list1 + rank 2 in list2 = 1/61 + 1/62
    expected_a = 1 / 61 + 1 / 62
    # B: rank 2 in list1 + rank 1 in list2 = 1/62 + 1/61
    expected_b = 1 / 62 + 1 / 61
    # C: rank 3 in both = 2/63
    expected_c = 2 / 63

    assert abs(scores["Paper A"] - expected_a) < 1e-9
    assert abs(scores["Paper B"] - expected_b) < 1e-9
    assert abs(scores["Paper C"] - expected_c) < 1e-9

    # A and B have same score (both appear at rank 1 and 2 across lists)
    assert abs(scores["Paper A"] - scores["Paper B"]) < 1e-9
    # C is lowest
    assert scores["Paper C"] < scores["Paper A"]


def test_rrf_single_list():
    """Single list returns same order with RRF scores."""
    a, b, c = _mk("A", doi="1"), _mk("B", doi="2"), _mk("C", doi="3")
    fused = rrf_fuse([[a, b, c]], k=60)
    assert fused[0][0].title == "A"
    assert fused[1][0].title == "B"
    assert fused[2][0].title == "C"
    assert fused[0][1] > fused[1][1] > fused[2][1]


def test_rrf_dedup_same_paper_different_lists():
    """Same paper (by DOI) in multiple lists accumulates score."""
    a1 = _mk("Paper A", doi="10.1/a")
    a2 = _mk("Paper A (duplicate)", doi="10.1/a")  # same DOI = same identity
    b = _mk("Paper B", doi="10.1/b")

    fused = rrf_fuse([[a1, b], [a2, b]], k=60)
    # A appears in both lists (rank 1 both) → score = 2/61
    # B appears in both lists (rank 2 both) → score = 2/62
    scores = {p.title: s for p, s in fused}
    # Only 2 unique papers (A deduped)
    assert len(fused) == 2
    assert fused[0][1] > fused[1][1]  # A > B


def test_rrf_empty_lists():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[], []]) == []


def test_rrf_fuse_recorded():
    """RecordedCandidate fusion groups by (sub_query, round)."""
    a = _mk("A", doi="1")
    b = _mk("B", doi="2")

    candidates = [
        RecordedCandidate(paper=a, sub_query="sq1", round=0, source_rank=1),
        RecordedCandidate(paper=b, sub_query="sq1", round=0, source_rank=2),
        RecordedCandidate(paper=a, sub_query="sq2", round=0, source_rank=1),
        RecordedCandidate(paper=b, sub_query="sq2", round=0, source_rank=2),
    ]

    fused = rrf_fuse_recorded(candidates, k=60)
    assert len(fused) == 2  # 2 unique papers
    # A is rank 1 in both sub-queries → higher score
    assert fused[0][0].title == "A"
    assert fused[1][0].title == "B"


def test_rrf_custom_k():
    """Different k values change score distribution."""
    a, b = _mk("A", doi="1"), _mk("B", doi="2")
    fused_k1 = rrf_fuse([[a, b], [a, b]], k=1)
    fused_k60 = rrf_fuse([[a, b], [a, b]], k=60)

    # With k=1, rank 1 = 1/2, rank 2 = 1/3 → ratio different from k=60
    ratio_k1 = fused_k1[0][1] / fused_k1[1][1]
    ratio_k60 = fused_k60[0][1] / fused_k60[1][1]
    assert ratio_k1 > ratio_k60  # k=1 amplifies top ranks more


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
