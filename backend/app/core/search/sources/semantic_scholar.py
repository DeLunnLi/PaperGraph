"""Semantic Scholar exact-title fallback source.

This source is intentionally opt-in through the TITLE recall recipe. It uses the
public match endpoint and only accepts candidates whose normalized title closely
matches a requested target title, preventing a web fallback from broadening a
precise lookup into a noisy topic search.
"""
from __future__ import annotations

import logging
from typing import Any

from ...author import Author
from ...paper import Paper
from ..normalize import strip_arxiv_version, titles_match_strict
from .base import register_source
from .source_common import json_api_headers, safe_int

logger = logging.getLogger(__name__)

_FIELDS = (
    "title,authors,year,citationCount,externalIds,url,venue,abstract,openAccessPdf"
)


def _paper_from_item(searcher: Any, item: dict[str, Any]) -> Paper | None:
    title = str(item.get("title") or "").strip()
    if not title:
        return None
    external = item.get("externalIds") or {}
    arxiv_id = str(external.get("ArXiv") or "").strip() or None
    doi = str(external.get("DOI") or "").strip() or None
    pdf = item.get("openAccessPdf") or {}
    year = safe_int(item.get("year"))
    citations = max(0, safe_int(item.get("citationCount"), default=0) or 0)
    return searcher._make_paper(
        title=title,
        authors=[
            Author(name=str(author.get("name") or "").strip())
            for author in (item.get("authors") or [])
            if str(author.get("name") or "").strip()
        ],
        abstract=str(item.get("abstract") or "").strip() or None,
        doi=doi,
        arxiv_id=strip_arxiv_version(arxiv_id) or None if arxiv_id else None,
        journal=str(item.get("venue") or "").strip() or None,
        year=year,
        citations=citations,
        pdf_url=str(pdf.get("url") or "").strip() or None,
        source_url=str(item.get("url") or "").strip() or None,
        source="semantic_scholar",
    )


@register_source("semantic_scholar")
async def search_semantic_scholar(
    searcher: Any, query: str, max_results: int = 10, **kwargs: Any
) -> list[Paper]:
    targets = [
        str(title).strip()
        for title in (kwargs.get("target_titles") or [])
        if str(title).strip()
    ]
    target = targets[0] if targets else (query or "").strip()
    if len(target) < 8:
        return []

    await searcher._ensure_async_client()
    headers = json_api_headers(searcher)
    try:
        response = await searcher._async_http_get_with_retry(
            "https://api.semanticscholar.org/graph/v1/paper/search/match",
            params={"query": target[:500], "fields": _FIELDS},
            headers=headers,
            timeout=float(kwargs.get("http_timeout_sec") or 10.0),
            max_attempts=1,
        )
        if response.status_code == 429:
            return []
        response.raise_for_status()
        items = list((response.json() or {}).get("data") or [])
    except Exception as exc:
        logger.info("semantic_scholar_title_fallback_failed: %s", type(exc).__name__)
        return []

    papers = [paper for item in items if (paper := _paper_from_item(searcher, item))]
    verified = [paper for paper in papers if titles_match_strict(paper.title, target)]
    return verified[: max(1, min(int(max_results), 5))]
