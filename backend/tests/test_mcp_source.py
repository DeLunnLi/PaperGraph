"""Tests for the MCP arxiv search source adapter.

Covers the pure conversion logic (_mcp_paper_to_paper) and the gating flag
without spawning the arxiv-mcp-server subprocess. The end-to-end spawn path is
exercised by tests/probe_mcp_arxiv.py (manual).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.search.sources.mcp import (
    _clean_arxiv_id,
    _mcp_enabled,
    _mcp_paper_to_paper,
    _parse_year,
)
from app.models.schemas import PaperSource


class _FakeSearcher:
    """Minimal stand-in exposing _make_paper like PaperSearcher.
    Uses core.paper.Paper (dataclass) — the same type PaperSearcher builds."""
    def _make_paper(self, *, title, authors=None, abstract=None, doi=None, arxiv_id=None,
                    journal=None, year=None, pdf_url=None, source_url=None, citations=0,
                    source="unknown", **extra):
        from app.core.paper import Paper
        return Paper(title=title, authors=authors or [], abstract=abstract, doi=doi,
                     arxiv_id=arxiv_id, journal=journal or source, year=year, pdf_url=pdf_url,
                     source_url=source_url, citations=citations, source=source, **extra)


def test_clean_arxiv_id_strips_version():
    assert _clean_arxiv_id("2412.16738v1") == "2412.16738"
    assert _clean_arxiv_id("2412.16738v3") == "2412.16738"
    assert _clean_arxiv_id("2412.16738") == "2412.16738"
    assert _clean_arxiv_id(None) is None
    assert _clean_arxiv_id("") is None


def test_parse_year():
    assert _parse_year("2024-12-21T19:01:38+00:00") == 2024
    assert _parse_year("2020") == 2020
    assert _parse_year(None) is None
    assert _parse_year("not-a-date") is None


def test_mcp_paper_to_paper_basic():
    searcher = _FakeSearcher()
    raw = {
        "id": "2412.16738v1",
        "title": "KKANs: Test Paper",
        "authors": ["Alice", "Bob"],
        "abstract": "[EXTERNAL CONTENT] This is the abstract.",
        "categories": ["cs.LG", "stat.ML"],
        "published": "2024-12-21T19:01:38+00:00",
        "url": "https://arxiv.org/pdf/2412.16738v1",
        "resource_uri": "arxiv://2412.16738v1",
    }
    p = _mcp_paper_to_paper(searcher, raw)
    assert p is not None
    assert p.source == PaperSource.MCP.value
    assert p.arxiv_id == "2412.16738"  # version stripped
    assert p.year == 2024
    assert p.journal == "arXiv:cs.LG"  # primary category
    assert p.abstract == "This is the abstract."  # [EXTERNAL CONTENT] prefix stripped
    assert p.pdf_url == "https://arxiv.org/pdf/2412.16738v1"
    assert p.source_url == "https://arxiv.org/abs/2412.16738"
    assert [a.name for a in p.authors] == ["Alice", "Bob"]


def test_mcp_paper_to_paper_missing_title_returns_none():
    searcher = _FakeSearcher()
    assert _mcp_paper_to_paper(searcher, {"id": "1234.5678"}) is None
    assert _mcp_paper_to_paper(searcher, {"title": "   "}) is None


def test_mcp_paper_to_paper_no_categories():
    searcher = _FakeSearcher()
    raw = {"id": "2401.00001v1", "title": "No Cat Paper", "authors": [], "abstract": "",
           "categories": [], "published": "2023-01-01", "url": ""}
    p = _mcp_paper_to_paper(searcher, raw)
    assert p is not None
    assert p.journal == "arXiv"  # no primary category → bare arXiv
    assert p.year == 2023


def test_mcp_disabled_by_default():
    # Default config has mcp_arxiv_enabled = False
    assert _mcp_enabled() is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
