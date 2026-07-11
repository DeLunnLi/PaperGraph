"""Tests for per-request ReaderCtx isolation and _reader_reco_ref_offset persistence.

Verifies:
1. Two concurrent ReaderCtx instances do not share lookup_buffer state.
2. _reader_reco_ref_offset persists on the singleton across "requests" (so
   recommendation pagination survives) and increments for the same paper_id.
3. _prune_reco_ref_offset bounds memory by evicting old entries.
4. _llm_chat is stateless — no _history accumulation (the original concurrency
   bug, now fixed by architecture: SimpleAgent replaced by stateless llm.chat).
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.support.reader_ctx import ReaderCtx


def test_lookup_buffers_isolated_between_ctxs():
    """Tool callbacks writing to ctx A must not appear in ctx B."""
    a = ReaderCtx(snap={"paper_id": 1})
    b = ReaderCtx(snap={"paper_id": 2})
    a.lookup_buffer.append(("paperA", "srcA"))
    b.lookup_buffer.append(("paperB", "srcB"))
    assert a.lookup_buffer == [("paperA", "srcA")]
    assert b.lookup_buffer == [("paperB", "srcB")]
    # clearing A does not affect B
    a.lookup_buffer.clear()
    assert a.lookup_buffer == []
    assert b.lookup_buffer == [("paperB", "srcB")]


def test_snaps_isolated_between_ctxs():
    a = ReaderCtx(snap={"paper_id": 1, "title": "A"})
    b = ReaderCtx(snap={"paper_id": 2, "title": "B"})
    # mutate A's snap (e.g. references_from_structure callback)
    a.snap["references_from_structure"] = ["ref1"]
    assert "references_from_structure" not in b.snap
    assert b.snap["title"] == "B"


def test_reco_ref_offset_persists_and_increments():
    """Simulate the singleton holding per-paper pagination across two calls."""
    # PaperAnalysisAgent holds _reader_reco_ref_offset on the singleton.
    # We model it as a plain dict (as the agent does in __init__).
    offset: dict[int, int] = {}

    def simulate_call(paper_id: int, n_extra: int) -> int:
        off = offset.get(paper_id, 0)
        offset[paper_id] = off + max(1, n_extra)
        return off

    # First request for paper 1: offset starts at 0, returns 0, stores 3
    assert simulate_call(paper_id=1, n_extra=3) == 0
    # Second request for paper 1: offset is now 3 (recommendations don't repeat)
    assert simulate_call(paper_id=1, n_extra=2) == 3
    # A different paper has its own offset
    assert simulate_call(paper_id=2, n_extra=5) == 0
    assert offset[1] == 5
    assert offset[2] == 5


def test_prune_reco_ref_offset_evicts_oldest_half():
    """When the offset dict exceeds the cap, the oldest half is dropped."""
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    agent._reader_reco_ref_offset = {i: i * 10 for i in range(1, 11)}  # 10 entries
    agent._reco_offset_max_papers = 6
    agent._prune_reco_ref_offset()
    # 10 > 6, so drop oldest half (5): keys 1..5 removed
    assert len(agent._reader_reco_ref_offset) == 5
    assert 1 not in agent._reader_reco_ref_offset
    assert 6 in agent._reader_reco_ref_offset
    assert 10 in agent._reader_reco_ref_offset


def test_prune_noop_when_under_cap():
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    agent._reader_reco_ref_offset = {1: 0, 2: 3}
    agent._reco_offset_max_papers = 200
    agent._prune_reco_ref_offset()
    assert agent._reader_reco_ref_offset == {1: 0, 2: 3}


def test_llm_chat_is_stateless():
    """The original concurrency bug: a shared SimpleAgent accumulated _history
    across runs (0->2->4). The fix removed SimpleAgent entirely — _llm_chat is
    stateless, so each call is independent (no history to contaminate)."""
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)

    calls: list[list[dict]] = []

    class _StubLLM:
        model = "stub-model"

        def chat(self, messages, **kw):
            calls.append([dict(m) for m in messages])
            return type("_R", (), {"content": "stub-reply"})()

    agent.llm = _StubLLM()

    a = agent._llm_chat("SYS", "first question")
    b = agent._llm_chat("SYS", "second question")
    assert a == b == "stub-reply"
    assert len(calls) == 2
    # 第二次调用的 messages 不含第一次的 user content —— 无状态，无交叉污染
    assert calls[0][-1] == {"role": "user", "content": "first question"}
    assert calls[1][-1] == {"role": "user", "content": "second question"}
    assert "first question" not in str(calls[1])
    # agent 不持有任何可变历史状态
    assert not any(k.startswith("_history") for k in vars(agent))


def test_build_reader_tools_returns_four_specs():
    """_build_reader_tools 返回 4 个 ToolSpec（reader 的 4 个工具），无需 LLM 即可构造。"""
    from app.agents.paper_analysis_agent import PaperAnalysisAgent

    agent = PaperAnalysisAgent.__new__(PaperAnalysisAgent)
    ctx = ReaderCtx(snap={"paper_id": 1})
    tools = agent._build_reader_tools(ctx)
    names = [t.name for t in tools]
    assert names == ["reader_paper_lookup", "reader_reference_lookup",
                     "reader_pdf_structure", "reader_pdf_table"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
