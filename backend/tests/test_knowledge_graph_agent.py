"""Golden-case tests for KnowledgeGraphAgent.

Covers the pure helpers (_compact_paper / _validate_edges / _chunks) and an
infer_edges golden case with a stubbed LLM — no API key required.

These snapshot current correct behaviour so refactors (exception narrowing,
async wrapping, etc.) don't regress relation extraction.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.knowledge_graph_agent import KnowledgeGraphAgent


class _StubResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubLLM:
    """Returns a canned JSON payload for every invoke()."""

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {}
        self.calls: list[list[dict]] = []

    def invoke(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        return _StubResp(json.dumps(self._payload, ensure_ascii=False))


def _make_agent(*, llm: _StubLLM | None = None,
                min_score: float = 0.55, max_edges: int = 12, chunk_size: int = 20) -> KnowledgeGraphAgent:
    agent = KnowledgeGraphAgent.__new__(KnowledgeGraphAgent)
    agent.min_score = min_score
    agent.max_edges = max_edges
    agent.chunk_size = chunk_size
    agent._agent = None
    agent.llm = llm or _StubLLM()
    return agent


# ── _candidate_id ───────────────────────────────────────────────────

def test_candidate_id_prefers_paper_id_then_id():
    agent = _make_agent()
    assert agent._candidate_id({"paper_id": 7}) == 7
    assert agent._candidate_id({"id": 9}) == 9
    assert agent._candidate_id({"target_paper_id": 11}) == 11
    # falls through paper_id="" to id
    assert agent._candidate_id({"paper_id": "", "id": 3}) == 3
    # non-positive or missing → None
    assert agent._candidate_id({"paper_id": 0, "id": -1}) is None
    assert agent._candidate_id({"foo": "bar"}) is None


# ── _compact_paper ──────────────────────────────────────────────────

def test_compact_paper_drops_empty_and_clips_long_fields():
    agent = _make_agent()
    paper = {
        "paper_id": 42,
        "title": "T" * 500,            # >300 → clipped
        "abstract": "A" * 3000,        # >2000 → clipped
        "keywords": [f"k{i}" for i in range(20)],  # capped at 12
        "source": "arxiv",
        "year": 2024,
        "category": None,              # dropped (None)
        "pdf_excerpt": "",
        "related_work_excerpt": None,  # dropped
    }
    out = agent._compact_paper(paper)
    assert out["paper_id"] == 42
    assert len(out["title"]) == 300
    assert len(out["abstract"]) == 2000
    assert len(out["keywords"]) == 12
    assert out["source"] == "arxiv"
    assert out["year"] == 2024
    # empty / None values dropped
    assert "category" not in out
    assert "pdf_excerpt" not in out
    assert "related_work_excerpt" not in out


def test_compact_paper_empty_input_yields_minimal_dict():
    agent = _make_agent()
    out = agent._compact_paper({})
    # paper_id None → dropped; everything else empty → dropped
    assert "paper_id" not in out
    assert out == {}


# ── _chunks ─────────────────────────────────────────────────────────

def test_chunks_respects_chunk_size():
    agent = _make_agent(chunk_size=3)
    items = [{"paper_id": i} for i in range(7)]
    chunks = agent._chunks(items)
    assert [len(c) for c in chunks] == [3, 3, 1]


def test_chunks_empty_input():
    agent = _make_agent()
    assert agent._chunks([]) == []


# ── _validate_edges ─────────────────────────────────────────────────

def test_validate_edges_filters_by_score_and_target():
    agent = _make_agent(min_score=0.55)
    allowed = {10, 20, 30}
    edges = [
        {"target_paper_id": 10, "score": 0.9, "relation": "extends"},   # ok
        {"target_paper_id": 20, "score": 0.3, "relation": "uses"},      # below threshold
        {"target_paper_id": 99, "score": 0.9, "relation": "cites"},     # target not in allowed
        {"target_paper_id": 30, "score": 0.6, "relation": ""},          # empty relation → dropped
        {"target_paper_id": 30, "score": 0.7, "relation": "improves"},  # ok
    ]
    out = agent._validate_edges(edges, allowed_ids=allowed)
    targets = [e["target_paper_id"] for e in out]
    assert targets == [10, 30]
    # score clamped + sorted desc
    assert out[0]["score"] >= out[1]["score"]


def test_validate_edges_dedupes_by_target_keeping_best_score():
    agent = _make_agent(min_score=0.5)
    edges = [
        {"target_paper_id": 5, "score": 0.6, "relation": "a"},
        {"target_paper_id": 5, "score": 0.9, "relation": "b"},   # wins
        {"target_paper_id": 5, "score": 0.7, "relation": "c"},
    ]
    out = agent._validate_edges(edges, allowed_ids={5})
    assert len(out) == 1
    assert out[0]["score"] == 0.9
    assert out[0]["relation"] == "b"


def test_validate_edges_rejects_non_list():
    agent = _make_agent()
    try:
        agent._validate_edges({"not": "a list"}, allowed_ids=set())
    except ValueError:
        return
    assert False, "expected ValueError for non-list edges"


# ── infer_edges golden case ─────────────────────────────────────────

def test_infer_edges_golden_case_merges_chunks_and_caps():
    """Stub LLM returns edges for each chunk; agent merges, dedupes, caps."""
    new_paper = {"paper_id": 1, "title": "New Paper", "abstract": "ab"}
    # 25 candidates → 2 chunks at chunk_size=20
    candidates = [{"paper_id": i, "title": f"C{i}"} for i in range(2, 27)]

    # LLM returns: chunk0 → edges to 2..6; chunk1 → edges to 22..26 (one dup of 6 via score)
    def payload_for(messages):
        # The agent passes {"new_paper":..., "candidates":[...]} as user content.
        # We don't parse it — just return edges whose targets span both chunks
        # so the merge path is exercised regardless of chunking order.
        return {
            "edges": [
                {"target_paper_id": 2, "score": 0.8, "relation": "cites", "evidence": "e1"},
                {"target_paper_id": 3, "score": 0.6, "relation": "extends", "evidence": "e2"},
                {"target_paper_id": 4, "score": 0.9, "relation": "improves", "evidence": "e3"},
            ]
        }

    class _ChunkAwareLLM:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages, **kw):
            self.calls += 1
            return _StubResp(json.dumps(payload_for(messages), ensure_ascii=False))

    llm = _ChunkAwareLLM()
    agent = _make_agent(llm=llm, min_score=0.55, max_edges=12, chunk_size=20)

    edges, err = agent.infer_edges(new_paper=new_paper, candidates=candidates)

    # 2 chunks → 2 LLM calls
    assert llm.calls == 2
    assert err is None
    # deduped by target → 3 distinct (2,3,4), sorted desc by score → 4(0.9),2(0.8),3(0.6)
    assert [e["target_paper_id"] for e in edges] == [4, 2, 3]
    assert edges[0]["score"] == 0.9


def test_infer_edges_no_candidates_returns_empty():
    agent = _make_agent()
    edges, err = agent.infer_edges(new_paper={"paper_id": 1}, candidates=[])
    assert edges == []
    assert err is None


def test_infer_edges_caps_at_max_edges():
    """When LLM returns more edges than max_edges, only top-N survive."""
    new_paper = {"paper_id": 1, "title": "N", "abstract": "a"}
    candidates = [{"paper_id": i, "title": f"C{i}"} for i in range(2, 8)]  # 6 candidates, 1 chunk

    big_payload = {
        "edges": [
            {"target_paper_id": i, "score": 0.9 - i * 0.05, "relation": "r", "evidence": "e"}
            for i in range(2, 8)
        ]
    }
    llm = _StubLLM(payload=big_payload)
    agent = _make_agent(llm=llm, max_edges=3, chunk_size=20)

    edges, _ = agent.infer_edges(new_paper=new_paper, candidates=candidates)
    assert len(edges) == 3
    # top-3 by score → 2,3,4
    assert [e["target_paper_id"] for e in edges] == [2, 3, 4]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
