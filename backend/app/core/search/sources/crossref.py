"""Crossref DOI metadata source.

Crossref is used only for DOI lookups. Broad title search is deliberately not
implemented because version records and similarly named deposits can outrank the
canonical work. Callers must provide verified DOI values via ``dois``.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from ...author import Author
from ...paper import Paper
from .base import register_source
from .source_common import json_api_headers, safe_int

logger = logging.getLogger(__name__)


def _first_year(message: dict[str, Any]) -> int | None:
    for key in ("published-print", "published-online", "published", "issued", "created"):
        parts = ((message.get(key) or {}).get("date-parts") or [])
        try:
            year = safe_int(parts[0][0])
        except (IndexError, TypeError):
            continue
        if year is not None and 1600 <= year <= 2100:
            return year
    return None


def _message_to_paper(searcher: Any, message: dict[str, Any]) -> Paper | None:
    titles = message.get("title") or []
    title = str(titles[0] if titles else "").strip()
    doi = str(message.get("DOI") or "").strip().lower()
    if not title or not doi:
        return None
    containers = message.get("container-title") or []
    authors = []
    for raw in (message.get("author") or [])[:100]:
        name = " ".join(
            part for part in (str(raw.get("given") or "").strip(), str(raw.get("family") or "").strip()) if part
        )
        if name:
            authors.append(Author(name=name, orcid=str(raw.get("ORCID") or "").strip() or None))
    citations = max(0, safe_int(message.get("is-referenced-by-count"), default=0) or 0)
    return searcher._make_paper(
        title=title,
        authors=authors,
        abstract=str(message.get("abstract") or "").strip() or None,
        doi=doi,
        journal=str(containers[0] if containers else "").strip() or None,
        year=_first_year(message),
        volume=str(message.get("volume") or "").strip() or None,
        issue=str(message.get("issue") or "").strip() or None,
        pages=str(message.get("page") or message.get("article-number") or "").strip() or None,
        publisher=str(message.get("publisher") or "").strip() or None,
        source_url=f"https://doi.org/{doi}",
        citations=citations,
        source="crossref",
    )


@register_source("crossref")
async def search_crossref(
    searcher: Any, query: str, max_results: int = 10, **kwargs: Any
) -> list[Paper]:
    dois = [str(value).strip().lower() for value in (kwargs.get("dois") or []) if str(value).strip()]
    if not dois:
        return []
    await searcher._ensure_async_client()
    papers: list[Paper] = []
    for doi in dict.fromkeys(dois[: max(1, min(int(max_results), 5))]):
        try:
            response = await searcher._async_http_get_with_retry(
                f"https://api.crossref.org/works/{quote(doi, safe='')}",
                params={"mailto": searcher.email} if searcher.email else {},
                headers=json_api_headers(searcher),
                timeout=float(kwargs.get("http_timeout_sec") or 10.0),
                max_attempts=1,
            )
            message = (response.json() or {}).get("message") or {}
            paper = _message_to_paper(searcher, message)
            if paper and paper.doi == doi:
                papers.append(paper)
        except Exception as exc:
            logger.info("crossref_doi_lookup_failed: %s", type(exc).__name__)
    return papers
