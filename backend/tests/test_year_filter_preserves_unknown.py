"""Regression test: year filters must not silently drop papers with unknown years.

The original code used ``p.year or 0`` for year_from and ``p.year or 9999`` for
year_to, which meant any paper with ``year=None`` (common from DBLP and some
OpenAlex results) was treated as year 0 or 9999 respectively — and thus
**always** filtered out by any year constraint, silently destroying recall.
"""
from __future__ import annotations

from app.core.paper import Paper
from app.core.search.paper_searcher import PaperSearcher


def _mk_papers() -> list[Paper]:
    return [
        Paper(title="Ancient", year=2018, source="arxiv", citations=0),
        Paper(title="Recent", year=2024, source="arxiv", citations=0),
        Paper(title="Unknown Year", year=None, source="dblp", citations=0),
    ]


class TestYearFilterPreservesUnknown:
    def test_year_from_keeps_unknown_year(self):
        """year_from=2020 must not drop papers with year=None."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10, year_from=2020,
        )
        titles = [p.title for p in result]
        assert "Unknown Year" in titles, "Paper with unknown year was dropped by year_from"
        assert "Recent" in titles
        assert "Ancient" not in titles

    def test_year_to_keeps_unknown_year(self):
        """year_to=2020 must not drop papers with year=None."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10, year_to=2020,
        )
        titles = [p.title for p in result]
        assert "Unknown Year" in titles, "Paper with unknown year was dropped by year_to"
        assert "Ancient" in titles
        assert "Recent" not in titles

    def test_year_range_keeps_unknown_year(self):
        """year_from + year_to together must not drop papers with year=None."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10, year_from=2020, year_to=2025,
        )
        titles = [p.title for p in result]
        assert "Unknown Year" in titles
        assert "Recent" in titles
        assert "Ancient" not in titles

    def test_no_year_filter_does_not_filter(self):
        """Without year filters, all papers pass through."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10,
        )
        assert len(result) == 3

    def test_year_from_filters_correctly(self):
        """Papers with known years are still filtered correctly."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10, year_from=2020,
        )
        known_years = [p.year for p in result if p.year is not None]
        assert all(y >= 2020 for y in known_years)

    def test_year_to_filters_correctly(self):
        """Papers with known years are still filtered correctly."""
        searcher = PaperSearcher.__new__(PaperSearcher)
        papers = _mk_papers()
        result = searcher._post_process_results(
            papers, "test", max_results=10, year_to=2020,
        )
        known_years = [p.year for p in result if p.year is not None]
        assert all(y <= 2020 for y in known_years)
