"""Tests for export._export_papers author chunking — locks in the round-1 N+1
fix that batched per-paper author queries into a chunked IN-list (SQLite's
999-variable limit requires chunking >900 papers).
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes.export import _export_papers


def _make_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE papers (
            id INTEGER PRIMARY KEY, title TEXT, doi TEXT, arxiv_id TEXT,
            abstract TEXT, journal TEXT, venue_type TEXT, year INTEGER,
            pdf_url TEXT, source_url TEXT, source TEXT, keywords TEXT,
            category TEXT, tags TEXT, rating INTEGER, read_status TEXT,
            importance TEXT, notes TEXT, citations INTEGER,
            created_at INTEGER, updated_at INTEGER, user_id INTEGER
        );
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE paper_authors (
            paper_id INTEGER NOT NULL, author_id INTEGER NOT NULL,
            author_order INTEGER DEFAULT 0
        );
    """)


def test_export_many_papers_chunks_author_query_without_error(tmp_path):
    """>900 papers must not hit SQLite's 999-variable limit; all authors survive."""
    p = str(tmp_path / "export.db")
    conn = sqlite3.connect(p)
    _make_schema(conn)
    n = 950
    for i in range(1, n + 1):
        conn.execute("INSERT INTO papers(id, title, user_id) VALUES(?, ?, 7)", (i, f"Paper {i}"))
        # two authors per paper, ordered
        conn.execute("INSERT INTO authors(id, name) VALUES(?, ?)", (2 * i - 1, f"First{i}"))
        conn.execute("INSERT INTO authors(id, name) VALUES(?, ?)", (2 * i, f"Second{i}"))
        conn.execute(
            "INSERT INTO paper_authors(paper_id, author_id, author_order) VALUES(?, ?, ?), (?, ?, ?)",
            (i, 2 * i - 1, 0, i, 2 * i, 1),
        )
    conn.commit()

    papers = _export_papers(conn, user_id=7)
    conn.close()

    assert len(papers) == n
    # Spot-check first and last paper preserve both authors in order.
    assert papers[0]["authors"] == ["First1", "Second1"]
    assert papers[-1]["authors"] == [f"First{n}", f"Second{n}"]
    # Every paper got its authors (no chunk dropped silently).
    assert all(len(p["authors"]) == 2 for p in papers)
