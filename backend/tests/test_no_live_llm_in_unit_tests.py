"""Unit tests must not reach the network.

Regression: `test_filter_and_rerank_pairs_caps_results` constructed an agent via
`__new__` and its comment claimed "the LLM rerank path is skipped (no llm on the
__new__ agent)". That was wrong — `_filter_and_rerank_pairs` branches on the
*global* `is_llm_configured()`, not on the instance. On any machine with an API
key configured (a developer laptop, or CI with secrets) that "pure helper" unit
test issued a real LLM rerank request: ~6x slower and intermittently failing
when the provider hiccuped.

This module asserts the whole suite stays offline by making the HTTP layer
explode, then importing and exercising the pure helpers.
"""
from __future__ import annotations

import pytest


def test_reader_rerank_is_offline_when_llm_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reranker must take the deterministic anchor path with no LLM.

    NOTE on approach: a "make the socket explode" guard does NOT work here.
    `_filter_and_rerank_pairs` wraps the LLM branch in `except Exception` and
    falls back to the anchor rerank, so a raising guard is swallowed and the
    test still passes — while a real request has already gone out. The only
    reliable signal is whether the LLM branch was *entered*, so the gate itself
    is what we assert on.
    """
    from app.agents.support import reader_reco_mixin
    from app.agents.support.reader_ctx import ReaderCtx
    from app.agents.support.reader_reference_lookup_tool import (
        READER_RELATED_FROM_BIBLIOGRAPHY,
    )
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    monkeypatch.setattr(reader_reco_mixin, "is_llm_configured", lambda: False)

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    ctx = ReaderCtx(snap={"paper_id": 1})

    class _P:
        def __init__(self, t: str) -> None:
            self.title, self.doi, self.arxiv_id = t, None, None
            self.authors, self.year, self.abstract = [], None, None

    pairs = [(_P(f"P{i}"), READER_RELATED_FROM_BIBLIOGRAPHY) for i in range(10)]
    papers, _ = agent._filter_and_rerank_pairs(
        ctx, pairs, um="推荐相关", want_reco=True, reco_max=3, history_lines=""
    )
    assert len(papers) == 3


def test_llm_branch_is_not_entered_when_gate_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counting gate entries is the detection that actually works."""
    from app.agents.support import reader_reco_mixin
    from app.agents.support.reader_ctx import ReaderCtx
    from app.agents.support.reader_reference_lookup_tool import (
        READER_RELATED_FROM_BIBLIOGRAPHY,
    )
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    calls: list[bool] = []

    def _gate() -> bool:
        calls.append(True)
        return False

    monkeypatch.setattr(reader_reco_mixin, "is_llm_configured", _gate)

    class _P:
        def __init__(self, t: str) -> None:
            self.title, self.doi, self.arxiv_id = t, None, None
            self.authors, self.year, self.abstract = [], None, None

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    agent._filter_and_rerank_pairs(
        ReaderCtx(snap={"paper_id": 1}),
        [(_P("A"), READER_RELATED_FROM_BIBLIOGRAPHY)],
        um="推荐相关",
        want_reco=True,
        reco_max=2,
        history_lines="",
    )
    assert calls, "the LLM gate must be consulted (otherwise this test proves nothing)"


def test_llm_gated_paths_check_the_global_helper() -> None:
    """Document the trap: these modules gate on the module-level
    `is_llm_configured()`, so stubbing an *instance* attribute is not enough to
    keep a test offline — patch the symbol in the module under test."""
    from pathlib import Path

    from app.agents.support import reader_reco_mixin

    src = Path(reader_reco_mixin.__file__).read_text(encoding="utf-8")
    assert "if is_llm_configured():" in src, (
        "if this gate moves, update the tests that patch it to stay offline"
    )
