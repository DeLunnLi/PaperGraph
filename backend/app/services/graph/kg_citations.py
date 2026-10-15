"""Real citation edges for the library knowledge graph via Semantic Scholar.

LLM-inferred relations (kg_relations.infer_edges) guess relatedness from
abstracts; this module adds ground-truth ``cites`` edges from the S2 API,
matched into the library by arXiv-id first, normalized-title second. Both
endpoints must be library papers — edges reference local paper ids only.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from typing import Any

import httpx

from ...core.search.normalize import strip_arxiv_version

logger = logging.getLogger(__name__)

_S2_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = 20.0
_MAX_REFS = 100


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9一-鿿]+", "", (t or "").lower())


def _s2_paper_id(paper: Any) -> str | None:
    """Best S2 lookup id for a library paper (DOI preferred, then arXiv)."""
    doi = (getattr(paper, "doi", None) or "").strip()
    if doi:
        return f"DOI:{doi}"
    aid = strip_arxiv_version(getattr(paper, "arxiv_id", None) or "")
    if aid:
        return f"ARXIV:{aid}"
    return None


def _fetch_reference_ids(s2_id: str) -> list[dict[str, Any]]:
    """Fetch the paper's reference list from S2 (cited works)."""
    resp = httpx.get(
        f"{_S2_BASE}/paper/{s2_id}/references",
        params={"fields": "title,externalIds", "limit": _MAX_REFS},
        headers={"User-Agent": "papergraph/1.0"},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        logger.debug("kg_citations: S2 references %s → HTTP %s", s2_id, resp.status_code)
        return []
    data = resp.json() or {}
    out: list[dict[str, Any]] = []
    for row in data.get("data") or []:
        cited = (row or {}).get("citedPaper") or {}
        title = str(cited.get("title") or "").strip()
        if title:
            out.append({"title": title, "externalIds": cited.get("externalIds") or {}})
    return out


def _library_lookup(db_path: str, user_id: int) -> tuple[dict[str, int], dict[str, int]]:
    """arxiv_id → paper_id and normalized-title → paper_id maps for the library."""
    by_arxiv: dict[str, int] = {}
    by_title: dict[str, int] = {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title, arxiv_id FROM papers WHERE user_id = ?",
            (int(user_id),),
        ).fetchall()
    finally:
        conn.close()
    for pid, title, arxiv_id in rows:
        aid = strip_arxiv_version(arxiv_id or "")
        if aid:
            by_arxiv.setdefault(aid.lower(), int(pid))
        nt = _norm_title(str(title or ""))
        if nt:
            by_title.setdefault(nt, int(pid))
    return by_arxiv, by_title


def build_citation_edges_for_paper(db_path: str, paper_id: int, *, user_id: int) -> int:
    """Write ``cites`` edges from this paper to library papers it references.

    Returns the number of edges written. Fails soft (0) on any S2/network issue.
    """
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT title, arxiv_id, doi FROM papers WHERE id = ? AND user_id = ?",
            (int(paper_id), int(user_id)),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return 0
    title, arxiv_id, doi = row
    paper = type("P", (), {"title": title, "arxiv_id": arxiv_id, "doi": doi})()
    s2_id = _s2_paper_id(paper)
    if not s2_id:
        return 0

    try:
        refs = _fetch_reference_ids(s2_id)
    except Exception:
        logger.debug("kg_citations: fetch failed for paper %s", paper_id, exc_info=True)
        return 0
    if not refs:
        return 0

    by_arxiv, by_title = _library_lookup(db_path, user_id)
    now = time.time()
    written = 0
    conn = sqlite3.connect(db_path)
    try:
        for ref in refs:
            target_id = None
            ext = ref.get("externalIds") or {}
            aid = strip_arxiv_version(str(ext.get("ArXiv") or ""))
            if aid:
                target_id = by_arxiv.get(aid.lower())
            if target_id is None:
                target_id = by_title.get(_norm_title(ref["title"]))
            if target_id is None or int(target_id) == int(paper_id):
                continue
            conn.execute(
                """
                INSERT INTO paper_relations(source_paper_id, target_paper_id, relation, score, evidence, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_paper_id, target_paper_id, relation) DO UPDATE SET
                  score=excluded.score,
                  evidence=excluded.evidence,
                  updated_at=excluded.updated_at
                """,
                (int(paper_id), int(target_id), "cites", 0.95,
                 "Semantic Scholar 真实引用", now, now),
            )
            written += 1
        conn.commit()
    finally:
        conn.close()
    if written:
        logger.info("kg_citations: paper %s → %d 真实引用边", paper_id, written)
    return written


def _has_reader_memory(reader_mem_db: str, paper_id: int) -> bool:
    """Best-effort: does the user have paper-scope reader memories for this paper?
    Cross-DB query against reader_memory.db (papers.db holds the graph edges).
    Fail-soft False so a missing/locked reader DB never blocks reader injection.
    """
    try:
        if not os.path.exists(reader_mem_db):
            return False
        conn = sqlite3.connect(reader_mem_db)
        try:
            row = conn.execute(
                "SELECT 1 FROM memories WHERE user_id = ? AND deleted_at = 0 LIMIT 1",
                (f"papergraph:paper:{int(paper_id)}",),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def get_citation_neighbors(db_path: str, paper_id: int, *, user_id: int) -> dict:
    """Citation-graph neighbors of a library paper, for reader injection.

    Returns ``{"cites": [...], "cited_by": [...]}`` where each entry is
    ``{"paper_id", "title", "has_memory"}``. ``cites`` = papers THIS paper
    references (outgoing); ``cited_by`` = papers that reference THIS paper
    (incoming). ``has_memory`` flags library papers the user has reader
    memories for — the citation-graph × personal-memory cross signal
    ("本文引用的 X 你上周读过").

    Edges come from ``paper_relations`` (relation='cites', written by
    :func:`build_citation_edges_for_paper`). The papers JOIN enforces
    user_id isolation as defense-in-depth (edges already reference library
    papers only). Caps each direction at 20 to keep reader ctx bounded.
    Fails soft (empty dict) on any DB error.
    """
    out: dict = {"cites": [], "cited_by": []}
    try:
        conn = sqlite3.connect(db_path)
        try:
            cites_rows = conn.execute(
                "SELECT r.target_paper_id, p.title "
                "FROM paper_relations r JOIN papers p "
                "  ON p.id = r.target_paper_id AND p.user_id = ? "
                "WHERE r.source_paper_id = ? AND r.relation = 'cites' "
                "ORDER BY r.score DESC LIMIT 20",
                (int(user_id), int(paper_id)),
            ).fetchall()
            cited_rows = conn.execute(
                "SELECT r.source_paper_id, p.title "
                "FROM paper_relations r JOIN papers p "
                "  ON p.id = r.source_paper_id AND p.user_id = ? "
                "WHERE r.target_paper_id = ? AND r.relation = 'cites' "
                "ORDER BY r.score DESC LIMIT 20",
                (int(user_id), int(paper_id)),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        logger.debug("kg_citations: get_citation_neighbors failed for %s", paper_id, exc_info=True)
        return out

    from ...settings import get_settings
    from ..memory.db_paths import READER_MEMORY_DB_NAME

    reader_mem_db = os.path.join(get_settings().data_dir, READER_MEMORY_DB_NAME)

    def _build(rows) -> list[dict]:
        items: list[dict] = []
        for pid, title in rows:
            items.append({
                "paper_id": int(pid),
                "title": str(title or "")[:200],
                "has_memory": _has_reader_memory(reader_mem_db, int(pid)),
            })
        return items

    out["cites"] = _build(cites_rows)
    out["cited_by"] = _build(cited_rows)
    return out
