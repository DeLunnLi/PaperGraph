from __future__ import annotations

import logging
from typing import Any, Iterable

from pydantic import TypeAdapter

from app.core.paper import Paper as LitPaper
from app.core.search.paper_searcher import abbreviate_journal as _abbrev

from ...models.schemas import Paper, PaperSource, ReadStatus

logger = logging.getLogger(__name__)
_paper_list_adapter = TypeAdapter(list[Paper])


def _coerce_paper_source(val: Any) -> PaperSource:
    if isinstance(val, PaperSource):
        return val
    try:
        return PaperSource(str(val or "unknown").lower().strip())
    except ValueError:
        return PaperSource.UNKNOWN


def _coerce_read_status(val: Any) -> ReadStatus:
    if isinstance(val, ReadStatus):
        return val
    try:
        return ReadStatus(str(val or "unread").lower().strip())
    except ValueError:
        return ReadStatus.UNREAD


def _normalize_author_entries(authors_in: list[Any]) -> list[dict[str, Any]]:
    norm: list[dict[str, Any]] = []
    for a in authors_in:
        if isinstance(a, str):
            norm.append({"name": a})
        elif isinstance(a, dict):
            norm.append(a)
        else:
            norm.append({"name": getattr(a, "name", "") or ""})
    return norm


def litpaper_to_api_paper(p: LitPaper) -> Paper:
    d = p.to_dict()
    d["journal"] = _abbrev(d.get("journal"))
    # Coerce the enum + numeric fields that can make model_validate raise, so a
    # single validate call covers both paths (previously the except branch
    # hand-re-mapped ~30 fields, duplicating LitPaper.to_dict / the Paper model).
    d["source"] = _coerce_paper_source(d.get("source"))
    d["read_status"] = _coerce_read_status(d.get("read_status"))
    d["relevance_score"] = d.get("relevance_score") or 0
    d["citations"] = d.get("citations") or 0
    return Paper.model_validate(d)


def api_paper_to_litpaper(p: Paper) -> LitPaper:
    d = p.model_dump(mode="json", exclude_none=False, exclude_unset=False)
    d.pop("id", None)
    d.pop("local_pdf_path", None)
    d.pop("category", None)
    d.pop("created_at", None)
    d.pop("updated_at", None)
    d["source"] = p.source.value
    d["read_status"] = p.read_status.value
    return LitPaper.from_dict(d)


def normalize_papers_for_api(papers: Iterable[Any] | None) -> list[Paper]:
    """统一 API 层 Paper 列表：接受 Paper / LitPaper / dict，返回校验后的 list[Paper]。"""
    if not papers:
        return []
    blobs: list[Any] = []
    for raw in papers:
        if isinstance(raw, Paper):
            blobs.append(raw)
            continue
        if isinstance(raw, LitPaper):
            blobs.append(litpaper_to_api_paper(raw))
            continue
        d = raw.model_dump() if hasattr(raw, "model_dump") else (dict(raw) if isinstance(raw, dict) else None)
        if not d or not str(d.get("title") or "").strip():
            continue
        d["authors"] = _normalize_author_entries(d.get("authors") or [])
        d["source"] = _coerce_paper_source(d.get("source"))
        if "journal" not in d and d.get("venue") is not None:
            d["journal"] = d.get("venue")
        if "source_url" not in d and d.get("url") is not None:
            d["source_url"] = d.get("url")
        blobs.append(d)
    try:
        return _paper_list_adapter.validate_python(blobs, strict=False)
    except Exception as ex:
        logger.exception("paper normalization failed")
        raise ValueError("paper_normalization_failed") from ex


def litpapers_to_api_papers(papers: Iterable[LitPaper]) -> list[Paper]:
    # normalize_papers_for_api already converts LitPaper via litpaper_to_api_paper
    # (its LitPaper branch); mapping twice re-validated every paper for no benefit.
    return normalize_papers_for_api(papers)
