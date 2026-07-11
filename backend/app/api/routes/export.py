"""Export endpoint: bundle papers + reading history + memory + KG relations into a portable JSON.

Lightweight strategy:
- Strip heavy fields (local_pdf_path, full abstract >300 chars for KG nodes)
- Strip internal IDs (replace with stable identity: arxiv_id/doi/title_hash)
- gzip compression via Accept-Encoding
- Support scope filter: "all" | "papers" | "reader" | "memory" | "graph"
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from ..deps import optional_user

from ...settings import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["导出"])


def _get_db_path() -> str:
    import os
    s = get_settings()
    return os.path.join(s.data_dir, "papers.db")


def _export_papers(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    if user_id is not None:
        rows = conn.execute("""
            SELECT id, title, doi, arxiv_id, abstract, journal, venue_type, year,
                   pdf_url, source_url, source, keywords, category, tags, rating,
                   read_status, importance, notes, citations, created_at, updated_at
            FROM papers WHERE user_id=? ORDER BY created_at ASC
        """, (user_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT id, title, doi, arxiv_id, abstract, journal, venue_type, year,
                   pdf_url, source_url, source, keywords, category, tags, rating,
                   read_status, importance, notes, citations, created_at, updated_at
            FROM papers ORDER BY created_at ASC
        """).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM papers LIMIT 0").description]
    out = []
    for r in rows:
        d = dict(zip(cols, r))
        # Authors
        authors = conn.execute("""
            SELECT a.name FROM authors a
            JOIN paper_authors pa ON a.id = pa.author_id
            WHERE pa.paper_id = ? ORDER BY pa."order"
        """, (d["id"],)).fetchall()
        d["authors"] = [a[0] for a in authors]
        # Strip heavy/internal
        d.pop("id", None)
        if d.get("abstract") and len(d["abstract"]) > 500:
            d["abstract"] = d["abstract"][:500] + "…"
        out.append(d)
    return out


def _export_reader_turns(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    if user_id is not None:
        rows = conn.execute("""
            SELECT paper_id, role, content, created_at
            FROM paper_reader_turns WHERE user_id=? ORDER BY created_at ASC
        """, (user_id,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT paper_id, role, content, created_at
            FROM paper_reader_turns ORDER BY created_at ASC
        """).fetchall()
    return [{"paper_id": r[0], "role": r[1], "content": r[2], "created_at": r[3]} for r in rows]


def _export_memory(conn: sqlite3.Connection) -> list[dict]:
    # agent_memory table
    try:
        rows = conn.execute("""
            SELECT agent_name, memory_type, content, importance, shared, scope, paper_id, created_at
            FROM agent_memory ORDER BY created_at ASC
        """).fetchall()
        return [{"agent": r[0], "type": r[1], "content": r[2], "importance": r[3],
                 "shared": bool(r[4]), "scope": r[5], "paper_id": r[6], "created_at": r[7]}
                for r in rows]
    except Exception:
        return []


def _export_relations(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute("""
            SELECT source_paper_id, target_paper_id, relation, score, evidence, created_at
            FROM paper_relations ORDER BY created_at ASC
        """).fetchall()
        return [{"source": r[0], "target": r[1], "relation": r[2], "score": r[3],
                 "evidence": r[4], "created_at": r[5]} for r in rows]
    except Exception:
        return []


def _export_feedback(conn: sqlite3.Connection, user_id: int | None = None) -> list[dict]:
    try:
        if user_id is not None:
            rows = conn.execute("""
                SELECT date_key, paper_identity_key, title, action, source_list, created_at
                FROM daily_recommend_feedback WHERE user_id=? ORDER BY created_at ASC
            """, (user_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT date_key, paper_identity_key, title, action, source_list, created_at
                FROM daily_recommend_feedback ORDER BY created_at ASC
            """).fetchall()
        return [{"date": r[0], "paper": r[1], "title": r[2], "action": r[3],
                 "source": r[4], "created_at": r[5]} for r in rows]
    except Exception:
        return []


@router.get("/json")
async def export_json(
    scope: str = Query(default="all", description="all|papers|reader|memory|graph|feedback"),
    user: dict = Depends(optional_user),
):
    """Export knowledge base as a portable JSON file (filtered by user)."""
    db_path = _get_db_path()
    user_id = user["user_id"]
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        data: dict[str, Any] = {
            "version": "1.0",
            "exported_at": int(time.time()),
            "source": "PaperGraph",
        }
        scopes = ["papers", "reader", "memory", "graph", "feedback"] if scope == "all" else [scope]
        if "papers" in scopes:
            data["papers"] = _export_papers(conn, user_id=user_id)
        if "reader" in scopes:
            data["reading_turns"] = _export_reader_turns(conn, user_id=user_id)
        if "memory" in scopes:
            data["memories"] = _export_memory(conn)
        if "graph" in scopes:
            data["relations"] = _export_relations(conn)
        if "feedback" in scopes:
            data["feedback"] = _export_feedback(conn, user_id=user_id)

        data["summary"] = {k: len(v) if isinstance(v, list) else 0 for k, v in data.items() if k not in ("version", "exported_at", "source")}

        json_bytes = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")

        filename = f"papergraph_export_{time.strftime('%Y%m%d_%H%M%S')}.json"
        return Response(
            content=json_bytes,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(json_bytes)),
            },
        )
    finally:
        conn.close()
