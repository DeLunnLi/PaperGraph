"""Tests for citation [pN] parsing in PaperAnalysisAgent._parse_citations_from_reply.

These exercise the pure parsing logic without touching the LLM or DB. We
construct a minimal PaperAnalysisAgent via __new__ to avoid BaseAgent.__init__
(which wires up a real LLM).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure backend/ is importable as the project root for `app.*` imports.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.paper_analysis_agent import PaperAnalysisAgent
from app.agents.support.reader_ctx import ReaderCtx


def _make_agent() -> PaperAnalysisAgent:
    """Build an agent shell without LLM init — only the parse method is exercised."""
    return PaperAnalysisAgent.__new__(PaperAnalysisAgent)


def _ctx_with_pages(pages: list[int]) -> ReaderCtx:
    snap = {"_pdf_pages": [{"page": n, "text": f"page {n}"} for n in pages]}
    return ReaderCtx(snap=snap)


def test_parses_single_and_multi_page_markers():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4, 5, 6, 7, 8])
    text = "方法见 Sec 3.1 [p4]，数据如表 [p7,p8] 所示。"
    cites = agent._parse_citations_from_reply(text, ctx)
    pages = sorted(c["page"] for c in cites)
    assert pages == [4, 7, 8], f"got pages {pages}"
    # markers reconstructed
    markers = sorted(c["marker"] for c in cites)
    assert markers == ["[p4]", "[p7]", "[p8]"], f"got markers {markers}"
    # snippet captured around the marker
    p4 = next(c for c in cites if c["page"] == 4)
    assert "方法见 Sec 3.1" in p4["snippet"]


def test_no_markers_returns_empty():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3])
    text = "这篇论文提出了一个新方法，没有页码标注。"
    assert agent._parse_citations_from_reply(text, ctx) == []


def test_out_of_range_pages_filtered():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4, 5])  # only 5 pages exist
    text = "见 [p3] 与 [p9] 的讨论。"
    cites = agent._parse_citations_from_reply(text, ctx)
    pages = [c["page"] for c in cites]
    assert pages == [3], f"out-of-range p9 should be filtered, got {pages}"


def test_no_pdf_pages_in_snap_keeps_all_markers():
    """When snap has no _pdf_pages (e.g. no local PDF), don't filter — let chips show."""
    agent = _make_agent()
    ctx = ReaderCtx(snap={})  # no _pdf_pages
    text = "见 [p3] 与 [p9]。"
    cites = agent._parse_citations_from_reply(text, ctx)
    assert sorted(c["page"] for c in cites) == [3, 9]


def test_dedupes_repeated_page():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4])
    text = "[p3] ... 再次提到 [p3]。"
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1
    assert cites[0]["page"] == 3


if __name__ == "__main__":
    test_parses_single_and_multi_page_markers()
    test_no_markers_returns_empty()
    test_out_of_range_pages_filtered()
    test_no_pdf_pages_in_snap_keeps_all_markers()
    test_dedupes_repeated_page()
    print("all citation parse tests passed")
