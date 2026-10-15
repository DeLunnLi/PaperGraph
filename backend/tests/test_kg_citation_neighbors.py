"""Tests for kg_citations.get_citation_neighbors (P2a: KG → reader injection).

Exercises the citation-graph neighbor query + the cross-DB has_memory signal,
without touching the S2 network. Edges are seeded directly into paper_relations.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import sqlite3  # noqa: E402

from app.services.graph.kg_citations import get_citation_neighbors  # noqa: E402


def _seed_library(db_path: str, *, papers: list[tuple[int, int, str]], edges: list[tuple[int, int, float]]):
    """papers: [(id, user_id, title)]; edges: [(source_id, target_id, score)].
    Creates the papers + paper_relations tables and seeds both."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS papers (
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL DEFAULT 1,
              title TEXT, doi TEXT, arxiv_id TEXT, pmid TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS paper_relations (
              source_paper_id INTEGER NOT NULL, target_paper_id INTEGER NOT NULL,
              relation TEXT NOT NULL, score REAL DEFAULT 0.0, evidence TEXT,
              created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
              PRIMARY KEY (source_paper_id, target_paper_id, relation)
            )"""
        )
        for pid, uid, title in papers:
            conn.execute(
                "INSERT OR REPLACE INTO papers (id, user_id, title) VALUES (?, ?, ?)",
                (pid, uid, title),
            )
        for src, dst, score in edges:
            conn.execute(
                "INSERT OR REPLACE INTO paper_relations "
                "(source_paper_id, target_paper_id, relation, score, created_at, updated_at) "
                "VALUES (?, ?, 'cites', ?, 1, 1)",
                (src, dst, score),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_reader_memory(reader_mem_db: str, paper_ids: list[int]):
    """Create reader_memory.db with a memories table that has paper-scope
    entries for the given paper_ids (so has_memory=True for those)."""
    conn = sqlite3.connect(reader_mem_db)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
              memory_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT,
              memory_type TEXT, importance REAL, properties TEXT, timestamp REAL,
              created_at REAL, updated_at REAL, utility REAL, access_count INTEGER,
              last_used REAL, deleted_at REAL DEFAULT 0
            )"""
        )
        for pid in paper_ids:
            conn.execute(
                "INSERT INTO memories (memory_id, user_id, content, memory_type, timestamp, created_at, updated_at) "
                "VALUES (?, ?, 'note', 'short', 1, 1, 1)",
                (f"m{pid}", f"papergraph:paper:{pid}"),
            )
        conn.commit()
    finally:
        conn.close()


def test_citation_neighbors_cites_and_cited_by(tmp_path):
    db_path = str(tmp_path / "papers.db")
    _seed_library(
        db_path,
        papers=[(1, 7, "Main"), (2, 7, "Cited by main"), (3, 7, "Cites main")],
        edges=[(1, 2, 0.95), (3, 1, 0.9)],  # main(1) cites 2; 3 cites main(1)
    )
    nb = get_citation_neighbors(db_path, 1, user_id=7)
    cites_ids = [c["paper_id"] for c in nb["cites"]]
    cited_by_ids = [c["paper_id"] for c in nb["cited_by"]]
    assert cites_ids == [2], f"main should cite paper 2, got {cites_ids}"
    assert cited_by_ids == [3], f"main should be cited-by paper 3, got {cited_by_ids}"
    # titles carried through
    assert nb["cites"][0]["title"] == "Cited by main"
    assert nb["cited_by"][0]["title"] == "Cites main"


def test_citation_neighbors_user_isolation(tmp_path):
    """Edges to another user's paper don't surface (papers JOIN user_id guard)."""
    db_path = str(tmp_path / "papers.db")
    _seed_library(
        db_path,
        papers=[(1, 7, "main"), (2, 999, "other user's paper"), (3, 7, "mine")],
        edges=[(1, 2, 0.9), (1, 3, 0.9)],  # main cites both 2 (other user) and 3 (mine)
    )
    nb = get_citation_neighbors(db_path, 1, user_id=7)
    cites_ids = [c["paper_id"] for c in nb["cites"]]
    assert cites_ids == [3], f"other-user paper 2 must be filtered by user_id JOIN, got {cites_ids}"


def test_citation_neighbors_has_memory_cross_db(tmp_path):
    """has_memory flags library papers that have paper-scope reader memories
    (cross-DB: papers.db graph vs reader_memory.db)."""
    db_path = str(tmp_path / "papers.db")
    reader_mem_db = str(tmp_path / "reader_memory.db")
    _seed_library(
        db_path,
        papers=[(1, 7, "main"), (2, 7, "has notes"), (3, 7, "no notes")],
        edges=[(1, 2, 0.9), (1, 3, 0.9)],
    )
    _seed_reader_memory(reader_mem_db, [2])  # only paper 2 has reader memories

    # point settings.data_dir at tmp_path so the query finds reader_memory.db
    import app.settings as settings_mod
    orig = settings_mod.get_settings()
    # Monkeypatch the cached settings instance's data_dir
    orig_data_dir = orig.data_dir
    try:
        object.__setattr__(orig, "data_dir", str(tmp_path))
        nb = get_citation_neighbors(db_path, 1, user_id=7)
    finally:
        object.__setattr__(orig, "data_dir", orig_data_dir)

    by_pid = {c["paper_id"]: c for c in nb["cites"]}
    assert by_pid[2]["has_memory"] is True, "paper 2 has reader notes"
    assert by_pid[3]["has_memory"] is False, "paper 3 has no reader notes"


def test_citation_neighbors_empty_for_isolated_paper(tmp_path):
    db_path = str(tmp_path / "papers.db")
    _seed_library(db_path, papers=[(1, 7, "main")], edges=[])
    nb = get_citation_neighbors(db_path, 1, user_id=7)
    assert nb == {"cites": [], "cited_by": []}


def test_citation_neighbors_caps_at_20(tmp_path):
    db_path = str(tmp_path / "papers.db")
    papers = [(1, 7, "main")] + [(i, 7, f"p{i}") for i in range(2, 24)]
    edges = [(1, i, 0.5) for i in range(2, 24)]  # 22 outgoing edges
    _seed_library(db_path, papers=papers, edges=edges)
    nb = get_citation_neighbors(db_path, 1, user_id=7)
    assert len(nb["cites"]) == 20  # capped


def test_citation_neighbors_fails_soft_on_missing_db(tmp_path):
    """A non-existent DB returns empty, not an exception."""
    nb = get_citation_neighbors(str(tmp_path / "nope.db"), 1, user_id=7)
    assert nb == {"cites": [], "cited_by": []}


def test_format_citation_graph_block_renders_cross_signal(tmp_path):
    """The reader-context block marks cited papers the user has notes on."""
    from app.services.reader.paper_reader_context import _format_citation_graph_block

    # empty → no block (reader ctx stays clean)
    assert _format_citation_graph_block({"cites": [], "cited_by": []}) == ""
    assert _format_citation_graph_block({}) == ""

    nb = {
        "cites": [
            {"paper_id": 2, "title": "Attention is All You Need", "has_memory": True},
            {"paper_id": 3, "title": "ResNet", "has_memory": False},
        ],
        "cited_by": [{"paper_id": 4, "title": "Follow-up", "has_memory": False}],
    }
    block = _format_citation_graph_block(nb)
    assert "【引用图谱】" in block
    assert "Attention is All You Need（你有笔记）" in block  # has_memory marked
    assert "ResNet" in block and "ResNet（你有笔记）" not in block  # no-notes not marked
    assert "Follow-up" in block  # cited_by surfaces
