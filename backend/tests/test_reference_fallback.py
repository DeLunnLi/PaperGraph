"""Tests for _reference_fallback_resolve — locks in the round-2 fix that removed
the unsupported `user_hint=` kwarg from resolve_references_via_openalex call
sites. Before the fix, every call raised TypeError (swallowed by the caller's
broad except), so bibliography recommendations were ALWAYS empty for the
no-tool-hit reference path.
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


def _make_agent() -> PaperAnalysisAgent:
    from cachetools import TTLCache
    import threading
    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    agent._reader_reco_ref_offset = {}
    agent._reco_offset_max_papers = 200
    agent._venue_type_cache = {}
    agent._classify_cache = TTLCache(maxsize=64, ttl=3600)
    agent._classify_cache_lock = threading.Lock()
    return agent


def test_reference_fallback_returns_non_empty(monkeypatch):
    """With user_hint removed, resolve_references_via_openalex must actually be
    invoked (no TypeError) and its result passed through. A regression that
    re-adds an unsupported kwarg makes the call raise → swallowed → empty."""
    agent = _make_agent()
    agent.llm = SimpleNamespace(model="stub")

    canned_paper = SimpleNamespace(title="Some Paper Title")
    calls = {"n": 0, "kwargs": None}

    def _fake_resolve(snap, *, max_results=5):
        calls["n"] += 1
        calls["kwargs"] = {"max_results": max_results}
        return [canned_paper]

    import app.agents.paper_analysis_agent as paa
    monkeypatch.setattr(paa, "resolve_references_via_openalex", _fake_resolve)

    ctx = ReaderCtx(snap={"references": ["Test Paper Title 2024"], "title": "Cur Paper"})
    extra = agent._reference_fallback_resolve(
        ctx, um="推荐相关论文", want_reco=True, reco_max=3, reco_pid=None,
    )
    assert calls["n"] == 1, "resolve_references_via_openalex must be called (no TypeError)"
    assert len(extra) >= 1
    assert extra[0] is canned_paper


def test_reference_fallback_skips_when_message_has_no_reference_intent():
    agent = _make_agent()
    ctx = ReaderCtx(snap={"references": ["Some Ref"]})
    # "今天天气如何" has no reference/related intent keyword.
    extra = agent._reference_fallback_resolve(
        ctx, um="今天天气如何", want_reco=False, reco_max=3, reco_pid=None,
    )
    assert extra == []
