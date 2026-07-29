"""Tests for papers_converters — locks in the round-5 consolidation that replaced
litpaper_to_api_paper's duplicated except branch with a single model_validate
+ explicit enum/numeric coercions, and removed litpapers_to_api_papers' double
validation.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.paper import Paper as LitPaper
from app.models.schemas import Paper, PaperSource, ReadStatus
from app.services.papers.papers_converters import (
    litpaper_to_api_paper,
    litpapers_to_api_papers,
    _coerce_paper_source,
    _coerce_read_status,
)


def _litpaper(**kw) -> LitPaper:
    base = dict(title="T", authors=[], source="arxiv", read_status="unread")
    base.update(kw)
    return LitPaper.from_dict(base)


def test_litpaper_to_api_paper_basic_fields():
    lp = _litpaper(title="A Method", doi="10.1/x", year=2024, citations=7, source="arxiv")
    api = litpaper_to_api_paper(lp)
    assert isinstance(api, Paper)
    assert api.title == "A Method"
    assert api.doi == "10.1/x"
    assert api.year == 2024
    assert api.citations == 7
    assert api.source == PaperSource.ARXIV


def test_litpaper_to_api_paper_coerces_invalid_source():
    """An invalid source string must fall back to UNKNOWN, not raise (previously
    handled by the duplicated except branch)."""
    lp = _litpaper(source="not-a-real-source")
    api = litpaper_to_api_paper(lp)
    assert api.source == PaperSource.UNKNOWN


def test_litpaper_to_api_paper_coerces_invalid_read_status():
    lp = _litpaper(read_status="bogus")
    api = litpaper_to_api_paper(lp)
    assert api.read_status == ReadStatus.UNREAD


def test_litpaper_to_api_paper_handles_missing_numeric_fields():
    lp = _litpaper()
    lp.citations = None
    lp.relevance_score = None
    api = litpaper_to_api_paper(lp)
    assert api.citations == 0
    assert api.relevance_score == 0


def test_litpapers_to_api_papers_returns_validated_list():
    lps = [_litpaper(title=f"P{i}", source="arxiv") for i in range(3)]
    out = litpapers_to_api_papers(lps)
    assert len(out) == 3
    assert all(isinstance(p, Paper) for p in out)
    assert [p.title for p in out] == ["P0", "P1", "P2"]


def test_coerce_paper_source_helpers():
    assert _coerce_paper_source("arxiv") == PaperSource.ARXIV
    assert _coerce_paper_source(PaperSource.DBLP) == PaperSource.DBLP
    assert _coerce_paper_source("bogus") == PaperSource.UNKNOWN
    assert _coerce_paper_source(None) == PaperSource.UNKNOWN


def test_coerce_read_status_helpers():
    assert _coerce_read_status("read") == ReadStatus.READ
    assert _coerce_read_status(ReadStatus.UNREAD) == ReadStatus.UNREAD
    assert _coerce_read_status("bogus") == ReadStatus.UNREAD
