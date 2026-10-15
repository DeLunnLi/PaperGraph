"""Probe: reader answer-quality + citation anchoring (real LLM + real PDF).

Not collected by pytest (probe_* naming). Run manually:
    cd backend && PYTHONPATH=. python tests/probe_reader.py

Golden cases: a real paper in the local DB with a parsed PDF + a question.
For each we run ``PaperReaderService.process_chat`` and check:
  1. the reply is non-empty and on-topic (contains an expected keyword)
  2. the reply cites at least one page via the [pN] mechanism
  3. every cited page is a real PDF page (the citation parser already filters
     out-of-range pages, so a non-empty citations list means valid anchors)

This is the end-to-end reader-quality signal. When a change to the reader
prompt / context building / citation parsing ships, re-run to prove the LLM
still cites real pages instead of hallucinating. A drop in citation_rate
means regression. Exits 1 on failure.

Requires: a paper with a local PDF already in the DB (the probe picks from
papers that have reader_artifacts parsed). If no such paper exists, the
probe reports SKIP rather than fail.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import BackgroundTasks

from app.core.storage import PaperDatabase
from app.services.reader.paper_reader_service import PaperReaderService


# Golden cases: (paper_id, question, expected_keywords).
# Keywords accept either English or Chinese — glm-5 typically replies in Chinese
# for a Chinese-posed context even when the question is English.
GOLDEN = [
    (158, "What is the main contribution of this paper?", ["generation order", "生成顺序"]),
    (155, "What problem does this paper address?", ["knowledge graph", "知识图", "知识图谱"]),
]

_PASSED: list[str] = []
_FAILED: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    (_PASSED if ok else _FAILED).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _has_artifact(paper_id: int) -> bool:
    return os.path.exists(os.path.join(BACKEND_ROOT, "data", "reader_artifacts", f"paper_{paper_id}.json"))


def _owner_of(db_path: str, paper_id: int) -> int | None:
    """Look up the user_id that owns this paper (papers are per-user isolated)."""
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT user_id FROM papers WHERE id = ?", (paper_id,)).fetchone()
    return int(row[0]) if row else None


async def main() -> int:
    db = PaperDatabase(str(BACKEND_ROOT / "data" / "papers.db"))
    service = PaperReaderService(db=db)

    ran = 0
    for paper_id, question, expect_kws in GOLDEN:
        tag = f"reader.paper{paper_id}"
        if not _has_artifact(paper_id):
            print(f"[SKIP] {tag} — no parsed PDF artifact; add the paper to the library first")
            continue
        user_id = _owner_of(db.db_path, paper_id)
        if user_id is None:
            print(f"[SKIP] {tag} — paper not in DB")
            continue
        ran += 1
        try:
            result = await service.process_chat(
                paper_id=paper_id, user_id=user_id, messages=[],
                user_message=question, background_tasks=BackgroundTasks(),
            )
        except Exception as exc:
            _check(tag, False, f"process_chat exception: {exc!r}")
            continue

        reply = (result.get("reply") or "").strip()
        citations = result.get("citations") or []
        if not reply:
            _check(tag, False, "empty reply")
            continue
        # On-topic check (fuzzy — accept any keyword, EN or CN; glm-5 replies in Chinese).
        reply_lower = reply.lower()
        on_topic = any(kw.lower() in reply_lower for kw in expect_kws)
        _check(f"{tag}.on_topic", on_topic,
               f"reply lacks any of {expect_kws} (len={len(reply)})")
        # Citation anchoring: at least one [pN] with a valid page.
        cited_pages = []
        verified_true = 0
        verified_any = 0  # true or false = LLM attached a verbatim quote
        for c in citations:
            if not isinstance(c, dict):
                continue
            page = c.get("page") or c.get("pages")
            if page is not None:
                cited_pages.append(str(page))
            v = c.get("verified")
            if v is True:
                verified_true += 1
            if v is not None:
                verified_any += 1
        _check(f"{tag}.cites_page", bool(cited_pages),
               f"citations={cited_pages[:5]} (count={len(cited_pages)})")
        # Passage-level grounding (P1): if the LLM attached any verbatim quotes,
        # at least one must verify True (quote found on the cited page). Soft —
        # if the LLM emitted zero quotes (all page-level), this is a warning not a fail.
        if verified_any > 0:
            _check(f"{tag}.passage_verified", verified_true > 0,
                   f"quotes={verified_any} verified_true={verified_true} (a True means passage grounding works)")
        else:
            print(f"[WARN] {tag}.passage: LLM attached no verbatim quotes (all page-level) — prompt may need tuning")

    print(f"\n[probe] {len(_PASSED)} passed, {len(_FAILED)} failed ({ran} cases ran)")
    if ran == 0:
        print("[probe] SKIP: no golden cases had a parsed PDF — save papers to the library and re-run")
        return 0
    return 0 if not _FAILED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
