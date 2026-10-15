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


def test_parses_dash_range_markers():
    """[pN-M] / [pN–M] (en/em dash) expand to the inclusive page range."""
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4, 5, 6, 7, 8])
    for dash in ("-", "–", "—"):
        cites = agent._parse_citations_from_reply(f"见 [p3{dash}p5] 的讨论。", ctx)
        pages = sorted(c["page"] for c in cites)
        assert pages == [3, 4, 5], f"dash {dash!r} got {pages}"


def test_parses_dash_range_without_p_prefix_on_second():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4, 5, 6])
    cites = agent._parse_citations_from_reply("见 [p3-5]。", ctx)
    assert sorted(c["page"] for c in cites) == [3, 4, 5]


def test_dash_range_filtered_by_valid_pages():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3])  # only 3 pages
    cites = agent._parse_citations_from_reply("见 [p2-6]。", ctx)
    assert sorted(c["page"] for c in cites) == [2, 3]


# ── passage-level excerpt verification ([pN:"quote"]) ──

def _ctx_with_page_texts(page_texts: dict[int, str]) -> ReaderCtx:
    """snap with real per-page text so quote verification can be exercised."""
    snap = {"_pdf_pages": [{"page": n, "text": t} for n, t in page_texts.items()]}
    return ReaderCtx(snap=snap)


def test_quote_marker_parses_and_verifies_true():
    agent = _make_agent()
    ctx = _ctx_with_page_texts({4: "we apply layer normalization before the attention mechanism"})
    text = '模型用 LayerNorm [p4:"we apply layer normalization before the attention"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1
    c = cites[0]
    assert c["page"] == 4
    assert c["quote"] == "we apply layer normalization before the attention"
    assert c["verified"] is True  # quote is verbatim on page 4


def test_quote_not_on_page_verifies_false():
    agent = _make_agent()
    ctx = _ctx_with_page_texts({4: "we use batch normalization"})
    text = '模型用 LayerNorm [p4:"we apply layer normalization"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert cites[0]["verified"] is False  # hallucination signal — quote not on page


def test_no_quote_returns_none_verified():
    agent = _make_agent()
    ctx = _ctx_with_pages([1, 2, 3, 4])
    cites = agent._parse_citations_from_reply("方法见 [p4]。", ctx)
    assert cites[0]["quote"] is None
    assert cites[0]["verified"] is None


def test_quote_with_comma_inside_stays_attached():
    agent = _make_agent()
    ctx = _ctx_with_page_texts({3: "the loss is computed as mean squared error, then backpropagated"})
    text = '损失函数 [p3:"mean squared error, then backpropagated"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1
    assert cites[0]["page"] == 3
    assert "mean squared error, then backpropagated" == cites[0]["quote"]
    assert cites[0]["verified"] is True  # comma inside quote didn't break parsing


def test_per_page_quotes_in_one_marker():
    agent = _make_agent()
    ctx = _ctx_with_page_texts({
        3: "we propose a contrastive objective",
        5: "experiments on imagenet show gains",
    })
    text = '方法 [p3:"contrastive objective"] 与实验 [p5:"imagenet show gains"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    by_page = {c["page"]: c for c in cites}
    assert set(by_page) == {3, 5}
    assert by_page[3]["verified"] is True
    assert by_page[5]["verified"] is True


def test_short_quote_not_verified_too_unreliable():
    """A 2-char quote would match many pages — we refuse to verify it (None)."""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({4: "the model uses attention"})
    text = '见 [p4:"the"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert cites[0]["quote"] == "the"
    assert cites[0]["verified"] is None  # below _MIN_QUOTE_LEN, unreliable


def test_no_page_text_returns_none_not_false():
    """A page with no extractable text (scanned w/o OCR) → can't verify → None."""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({4: ""})  # empty page text
    text = '见 [p4:"some verbatim quote here"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert cites[0]["verified"] is None


def test_quote_containing_bracket_does_not_truncate():
    """A verbatim quote that legitimately contains a ] (e.g. a reference to
    eq [3]) must not terminate the outer match early — the whole quote survives.
    (Regression for the non-quote-aware outer regex bug.)"""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({
        4: "as shown in eq [3] the model converges",
        7: "page seven content here",
    })
    text = '模型见 [p4:"as shown in eq [3] the model converges"] 及 [p7]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    by_page = {c["page"]: c for c in cites}
    assert 4 in by_page and 7 in by_page, f"both citations must survive, got {list(by_page)}"
    assert by_page[4]["quote"] == "as shown in eq [3] the model converges"
    assert by_page[4]["verified"] is True  # full quote verified on page 4


def test_smart_quotes_normalized_to_ascii_before_parse():
    """glm-5 emits typographic quotes (“ ”); without normalization the entire
    citation marker vanishes. Verify smart-quote input parses + verifies."""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({3: "we use a contrastive objective here"})
    # [p3: + U+201C + verbatim + U+201D + ] — smart quotes as the excerpt delimiters.
    text = "该方法用对比学习 [p3:“we use a contrastive objective here”]。"
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1, f"smart-quote citation must not vanish, got {len(cites)}"
    assert cites[0]["page"] == 3
    assert cites[0]["verified"] is True


def test_short_token_quote_now_verifies():
    """_MIN_QUOTE_LEN=5 (lowered from 8) so legit short technical tokens like
    'ResNet' (6 chars) and short CJK excerpts verify instead of silently None."""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({5: "the ResNet architecture is deep"})
    text = '架构见 [p5:"ResNet"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert cites[0]["quote"] == "ResNet"
    assert cites[0]["verified"] is True  # 6 chars ≥ 5 → verified


def test_too_short_quote_still_none():
    """Below _MIN_QUOTE_LEN (5), verification stays None — 'the' (3) would
    match too many pages to trust."""
    agent = _make_agent()
    ctx = _ctx_with_page_texts({5: "the model uses attention"})
    text = '见 [p5:"the"]。'
    cites = agent._parse_citations_from_reply(text, ctx)
    assert cites[0]["verified"] is None


if __name__ == "__main__":
    test_parses_single_and_multi_page_markers()
    test_no_markers_returns_empty()
    test_out_of_range_pages_filtered()
    test_no_pdf_pages_in_snap_keeps_all_markers()
    test_dedupes_repeated_page()
    print("all citation parse tests passed")
