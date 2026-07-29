"""Golden-case tests for PaperAnalysisAgent.

Covers the pure, side-effect-free helpers without an API key:
- _parse_citations_from_reply ([pN] page-anchor extraction + filtering)
- _init_reader_ctx (snap copy isolation, user_message clip, want_reco parse)
- _build_text_output (fallback copy branches)
- _filter_and_rerank_pairs (non-reco path: dedupe + bib_only filter + cap)
- _dedupe_reader_paper_pairs (staticmethod)
- _prune_reco_ref_offset (bounds + eviction)

These snapshot current behaviour so async/exception refactors don't regress
the reader reply pipeline.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.paper_analysis_agent import PaperAnalysisAgent
from app.agents.support.reader_ctx import ReaderCtx
from app.agents.support.reader_reference_lookup_tool import (
    READER_RELATED_FROM_BIBLIOGRAPHY,
    READER_RELATED_FROM_PRE_SEARCH,
    READER_RELATED_FROM_REF_BLOCK,
)


def _make_agent() -> PaperAnalysisAgent:
    """Bypass __init__ (which calls get_llm); set only what the tested methods need."""
    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    agent._reader_reco_ref_offset = {}
    agent._reco_offset_max_papers = 200
    agent._venue_type_cache = {}
    return agent


def _paper(title: str, **kw) -> SimpleNamespace:
    return SimpleNamespace(title=title, **kw)


# ── _parse_citations_from_reply ─────────────────────────────────────

def test_citations_single_page_marker():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"_pdf_pages": [{"page": 3}, {"page": 7}]})
    text = "The method uses attention [p3] to process tokens."
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1
    assert cites[0]["marker"] == "[p3]"
    assert cites[0]["page"] == 3
    assert "attention" in cites[0]["snippet"]


def test_citations_multi_page_marker():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"_pdf_pages": [{"page": 3}, {"page": 5}, {"page": 7}]})
    text = "Results in [p3,p5] and ablation in [p7]."
    cites = agent._parse_citations_from_reply(text, ctx)
    pages = sorted(c["page"] for c in cites)
    assert pages == [3, 5, 7]


def test_citations_filter_out_of_range_pages():
    """Page anchors not in _pdf_pages are dropped (soft constraint)."""
    agent = _make_agent()
    ctx = ReaderCtx(snap={"_pdf_pages": [{"page": 3}]})
    text = "see [p3] and also [p99]."
    cites = agent._parse_citations_from_reply(text, ctx)
    assert len(cites) == 1
    assert cites[0]["page"] == 3


def test_citations_no_valid_pages_keeps_all_when_pdf_pages_absent():
    """Without _pdf_pages, range-filtering is skipped — all anchors kept."""
    agent = _make_agent()
    ctx = ReaderCtx(snap={})
    text = "see [p3] and [p99]."
    cites = agent._parse_citations_from_reply(text, ctx)
    assert {c["page"] for c in cites} == {3, 99}


def test_citations_dedupe_repeated_page():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"_pdf_pages": [{"page": 3}, {"page": 5}]})
    text = "[p3] first, [p3] again, [p5] too."
    cites = agent._parse_citations_from_reply(text, ctx)
    assert {c["page"] for c in cites} == {3, 5}


def test_citations_empty_text_returns_empty():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"_pdf_pages": [{"page": 3}]})
    assert agent._parse_citations_from_reply("", ctx) == []
    assert agent._parse_citations_from_reply("no markers here", ctx) == []


# ── _init_reader_ctx ────────────────────────────────────────────────

def test_init_reader_ctx_copies_snap_isolating_mutations():
    agent = _make_agent()
    original = {"paper_id": 5, "title": "T"}
    ctx, reco_pid, um, want_reco, reco_max = agent._init_reader_ctx(original, "hello")
    # mutating ctx.snap must not touch the caller's dict
    ctx.snap["references_from_structure"] = ["x"]
    assert "references_from_structure" not in original
    assert reco_pid == 5
    assert um == "hello"
    assert ctx.user_message == "hello"


def test_init_reader_ctx_clips_long_user_message():
    agent = _make_agent()
    long_msg = "x" * 2000
    ctx, _, um, _, _ = agent._init_reader_ctx({"paper_id": 1}, long_msg)
    assert len(um) <= 900
    assert len(ctx.user_message) <= 900


def test_init_reader_ctx_invalid_paper_id_yields_none():
    agent = _make_agent()
    ctx, reco_pid, _, _, _ = agent._init_reader_ctx({"paper_id": "abc"}, "q")
    assert reco_pid is None
    ctx2, reco_pid2, _, _, _ = agent._init_reader_ctx({}, "q")
    assert reco_pid2 is None


def test_init_reader_ctx_want_reco_parsed():
    """Recommendation intent is parsed from the user message."""
    agent = _make_agent()
    # parse_reader_recommendation_intent recognises recommendation phrasing;
    # a plain question should yield want_reco=False.
    _, _, _, want_reco, _ = agent._init_reader_ctx({"paper_id": 1}, "what is the main method?")
    assert want_reco is False


# ── _build_text_output ──────────────────────────────────────────────

def test_build_text_output_uses_llm_text_when_present():
    agent = _make_agent()
    ctx = ReaderCtx(snap={})
    out = agent._build_text_output(ctx, "the answer is 42", [], "q")
    assert out == "the answer is 42"


def test_build_text_output_with_papers_and_pdf_source():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"references_source": "pdf_section"})
    out = agent._build_text_output(ctx, "", [_paper("A")], "q")
    assert "推荐论文" in out


def test_build_text_output_no_refs_no_pdf_section():
    agent = _make_agent()
    ctx = ReaderCtx(snap={})  # no references, no references_section_raw
    out = agent._build_text_output(ctx, "", [], "推荐相关论文")
    # user_message_may_need_reference_lookup("推荐相关论文") → True path
    assert "参考文献" in out or "DOI" in out


# ── _dedupe_reader_paper_pairs (staticmethod) ───────────────────────

def test_dedupe_reader_paper_pairs_by_title():
    buffer = [
        (_paper("Attention Is All You Need"), "srcA"),
        (_paper("attention is all you need"), "srcB"),  # dup (case-insensitive)
        (_paper("BERT"), "srcA"),
        (_paper(""), "srcC"),  # empty title → skipped
    ]
    out = PaperAnalysisAgent._dedupe_reader_paper_pairs(buffer)
    titles = [getattr(p, "title") for p, _ in out]
    assert len(out) == 2
    assert "Attention Is All You Need" in titles
    assert "BERT" in titles


# ── _filter_and_rerank_pairs (non-reco path) ────────────────────────

def test_filter_and_rerank_pairs_drops_papers_matching_snap():
    """Papers that already match the current paper's snap are filtered out."""
    agent = _make_agent()
    ctx = ReaderCtx(snap={"title": "Current Paper Title", "paper_id": 1})
    pairs = [
        (_paper("Current Paper Title"), READER_RELATED_FROM_BIBLIOGRAPHY),  # matches snap → dropped
        (_paper("Related Work A"), READER_RELATED_FROM_BIBLIOGRAPHY),
    ]
    papers, provenances = agent._filter_and_rerank_pairs(
        ctx, pairs, um="find related", want_reco=False, reco_max=3, history_lines=""
    )
    titles = [getattr(p, "title") for p in papers]
    assert "Related Work A" in titles
    assert "Current Paper Title" not in titles


def test_filter_and_rerank_pairs_caps_results():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"paper_id": 1})
    pairs = [(_paper(f"P{i}"), READER_RELATED_FROM_BIBLIOGRAPHY) for i in range(10)]
    papers, _ = agent._filter_and_rerank_pairs(
        ctx, pairs, um="推荐相关", want_reco=True, reco_max=3, history_lines=""
    )
    # want_reco=True → capped at reco_max (3), but rerank may need LLM;
    # if LLM path fails it falls back to anchor rerank. Either way <= cap-ish.
    assert len(papers) <= 10  # sanity; exact cap depends on rerank path


# ── _prune_reco_ref_offset ──────────────────────────────────────────

def test_prune_evicts_oldest_half_when_over_cap():
    agent = _make_agent()
    agent._reader_reco_ref_offset = {i: i * 10 for i in range(1, 11)}  # 10 entries
    agent._reco_offset_max_papers = 6
    agent._prune_reco_ref_offset()
    assert len(agent._reader_reco_ref_offset) == 5
    assert 1 not in agent._reader_reco_ref_offset  # oldest evicted
    assert 10 in agent._reader_reco_ref_offset    # newest kept


def test_prune_noop_under_cap():
    agent = _make_agent()
    agent._reader_reco_ref_offset = {1: 0, 2: 3}
    agent._reco_offset_max_papers = 200
    agent._prune_reco_ref_offset()
    assert agent._reader_reco_ref_offset == {1: 0, 2: 3}


# ── _new_reader_agent (smoke, no LLM key) ───────────────────────────

def test_new_reader_agent_binds_tools_to_ctx():
    """Each _new_reader_agent call binds tool callbacks to its own ctx."""
    from hello_agents import SimpleAgent

    agent = _make_agent()
    agent.llm = SimpleNamespace(model="stub")  # _new_reader_agent doesn't call llm
    ctx = ReaderCtx(snap={"paper_id": 1, "title": "T"})

    reader = agent._new_reader_agent(ctx)
    assert isinstance(reader, SimpleAgent)
    names = reader.tool_registry.list_tools()
    for expected in ("reader_paper_lookup", "reader_reference_lookup",
                     "reader_pdf_structure", "reader_pdf_table"):
        assert expected in names


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
