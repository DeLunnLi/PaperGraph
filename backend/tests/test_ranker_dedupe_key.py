"""Regression tests for LlmPaperRanker._ranked_paper_dedupe_key.

The old implementation used a naive ``str.strip()`` on arxiv_id/doi/title,
which did **not** canonicalize arXiv IDs — it left version suffixes, the
``arxiv:`` prefix, and failed to extract arXiv from DOI like
``10.48550/arXiv.2401.12345``.  This caused the same paper to appear as
duplicate in the final ranking when different sources formatted identifiers
differently.

The fix delegates to ``rrf_fusion._paper_identity`` (the same function used
by ``PaperSearcher._paper_dedupe_key``), ensuring all three dedup paths
agree.
"""
from __future__ import annotations

import pytest

from app.core.paper import Paper
from app.core.search.paper_searcher import PaperSearcher
from app.services.retrieval.paper_ranker import LlmPaperRanker, RankedPaper
from app.services.retrieval.rrf_fusion import _paper_identity


def _mk(arxiv_id=None, doi=None, title="Test Paper", **kw) -> Paper:
    return Paper(title=title, arxiv_id=arxiv_id, doi=doi, **kw)


class TestRankedPaperDedupeKey:
    def test_arxiv_version_suffix_collapsed(self):
        """``2401.12345`` and ``2401.12345v2`` are the same paper."""
        p1 = _mk(arxiv_id="2401.12345")
        p2 = _mk(arxiv_id="2401.12345v2")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p1))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p2))
        assert k1 == k2

    def test_arxiv_prefix_collapsed(self):
        """``2401.12345`` and ``arxiv:2401.12345`` are the same paper."""
        p1 = _mk(arxiv_id="2401.12345")
        p2 = _mk(arxiv_id="arxiv:2401.12345")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p1))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p2))
        assert k1 == k2

    def test_arxiv_extracted_from_doi(self):
        """DOI ``10.48550/arXiv.2401.12345`` resolves to the same arXiv key."""
        p_arxiv = _mk(arxiv_id="2401.12345")
        p_doi = _mk(doi="10.48550/arXiv.2401.12345")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p_arxiv))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p_doi))
        assert k1 == k2

    def test_consistency_with_paper_searcher(self):
        """All three dedup implementations must agree."""
        variants = [
            _mk(arxiv_id="2401.12345"),
            _mk(arxiv_id="arxiv:2401.12345v2"),
            _mk(doi="10.48550/arXiv.2401.12345"),
        ]
        for p in variants:
            k_ranker = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p))
            k_searcher = PaperSearcher._paper_dedupe_key(p)
            k_rrf = _paper_identity(p)
            assert k_ranker == k_searcher, f"ranker != searcher: {k_ranker} vs {k_searcher}"
            assert k_ranker == k_rrf, f"ranker != rrf: {k_ranker} vs {k_rrf}"

    def test_doi_case_normalized(self):
        """DOI keys must be case-insensitive."""
        p1 = _mk(doi="10.1000/ABC")
        p2 = _mk(doi="10.1000/abc")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p1))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p2))
        assert k1 == k2

    def test_title_normalization(self):
        """Titles differing only in punctuation/case should match."""
        p1 = _mk(title="A Survey of Knowledge Graphs!")
        p2 = _mk(title="a survey of knowledge graphs")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p1))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p2))
        assert k1 == k2

    def test_supplement_ranked_no_duplicates(self):
        """The supplement path must not add a paper already in the ranked list."""
        ranker = LlmPaperRanker(recall_max=20, fine_top_k=5)
        p1 = _mk(arxiv_id="2401.12345", citations=100, year=2024, source="arxiv")
        p2 = _mk(arxiv_id="arxiv:2401.12345v2", citations=100, year=2024, source="openalex")
        p3 = _mk(doi="10.48550/arXiv.2401.12345", citations=100, year=2024, source="openalex")
        p_other = _mk(title="Different Paper", arxiv_id="2402.99999", citations=50, year=2023, source="dblp")

        ranked = [RankedPaper(paper=p1, fine_score=0.9, ranking_reason="LLM top")]
        pool = [RankedPaper(paper=p1), RankedPaper(paper=p2), RankedPaper(paper=p3),
                RankedPaper(paper=p_other, fine_score=0.7)]
        result = ranker._supplement_ranked(ranked, pool, top_k=5)

        assert len(result) == 2  # p1 (ranked) + p_other (supplement), NOT p2/p3
        titles = [rp.paper.title for rp in result]
        assert "Test Paper" in titles
        assert "Different Paper" in titles

    def test_title_dedupe_uses_hash_not_prefix(self):
        """Title dedup should match on full normalized title, not a prefix."""
        # Two different papers with similar title prefixes
        p1 = _mk(title="Attention Is All You Need")
        p2 = _mk(title="Attention Is Not All You Need")
        k1 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p1))
        k2 = LlmPaperRanker._ranked_paper_dedupe_key(RankedPaper(paper=p2))
        assert k1 != k2, "Different titles must produce different keys"
