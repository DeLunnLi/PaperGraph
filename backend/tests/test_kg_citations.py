"""Tests for S2 real-citation edges in the knowledge graph."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.paper import Paper
from app.core.storage import PaperDatabase
from app.services.graph import kg_citations
from app.services.graph.kg_relations import ensure_tables


@pytest.fixture()
def db(tmp_path):
    db_path = str(tmp_path / "papers.db")
    ensure_tables(db_path)
    return PaperDatabase(db_path)


def _edges(db_path: str) -> list[tuple]:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT source_paper_id, target_paper_id, relation, score, evidence FROM paper_relations"
        ).fetchall()
    finally:
        conn.close()


def test_citation_edge_via_arxiv_match(db, monkeypatch):
    target_id, _ = db.add_paper(Paper(title="目标论文 B", arxiv_id="2401.00001", user_id=1))
    src_id, _ = db.add_paper(Paper(title="新论文 A", arxiv_id="2401.00002", user_id=1))

    monkeypatch.setattr(
        kg_citations, "_fetch_reference_ids",
        lambda s2_id: [{"title": "完全不同的标题", "externalIds": {"ArXiv": "2401.00001v2"}}],
    )
    n = kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1)
    assert n == 1
    edges = _edges(db.db_path)
    assert edges == [(src_id, target_id, "cites", 0.95, "Semantic Scholar 真实引用")]


def test_citation_edge_via_title_fallback(db, monkeypatch):
    target_id, _ = db.add_paper(Paper(title="Attention Is All You Need", user_id=1))
    src_id, _ = db.add_paper(Paper(title="某新工作", arxiv_id="2401.00003", user_id=1))

    monkeypatch.setattr(
        kg_citations, "_fetch_reference_ids",
        lambda s2_id: [{"title": "Attention is all you need!", "externalIds": {}}],
    )
    n = kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1)
    assert n == 1
    assert _edges(db.db_path)[0][1] == target_id


def test_no_external_id_returns_zero(db, monkeypatch):
    src_id, _ = db.add_paper(Paper(title="无外部标识的论文", user_id=1))
    monkeypatch.setattr(kg_citations, "_fetch_reference_ids", lambda s2_id: [])
    assert kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1) == 0


def test_s2_failure_fails_soft(db, monkeypatch):
    src_id, _ = db.add_paper(Paper(title="新论文", arxiv_id="2401.00004", user_id=1))

    def _boom(_):
        raise RuntimeError("network down")

    monkeypatch.setattr(kg_citations, "_fetch_reference_ids", _boom)
    assert kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1) == 0


def test_self_citation_and_upsert(db, monkeypatch):
    src_id, _ = db.add_paper(Paper(title="自引论文", arxiv_id="2401.00005", user_id=1))
    monkeypatch.setattr(
        kg_citations, "_fetch_reference_ids",
        lambda s2_id: [{"title": "自引论文", "externalIds": {"ArXiv": "2401.00005"}}],
    )
    assert kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1) == 0
    # second run with a real target writes once; repeat run upserts (no dup row)
    target_id, _ = db.add_paper(Paper(title="另一篇", arxiv_id="2401.00006", user_id=1))
    monkeypatch.setattr(
        kg_citations, "_fetch_reference_ids",
        lambda s2_id: [{"title": "另一篇", "externalIds": {"ArXiv": "2401.00006"}}],
    )
    kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1)
    kg_citations.build_citation_edges_for_paper(db.db_path, src_id, user_id=1)
    edges = _edges(db.db_path)
    assert len(edges) == 1 and edges[0][1] == target_id
