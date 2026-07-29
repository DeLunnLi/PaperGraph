"""Europe PMC biomedical literature search source."""
from __future__ import annotations

import logging
import re
from typing import Any

from ...author import Author
from ...paper import Paper
from .base import register_source
from .source_common import json_api_headers, safe_int

logger = logging.getLogger(__name__)
_TAG_RE = re.compile(r"<[^>]+>")


def _author_list(item: dict[str, Any]) -> list[Author]:
    raw = (item.get("authorList") or {}).get("author") or []
    authors = [
        Author(
            name=str(author.get("fullName") or author.get("authorString") or "").strip(),
            orcid=str(author.get("authorId") or "").strip() or None
            if str(author.get("authorIdType") or "").upper() == "ORCID"
            else None,
        )
        for author in raw[:100]
        if str(author.get("fullName") or author.get("authorString") or "").strip()
    ]
    if authors:
        return authors
    return [Author(name=name.strip()) for name in str(item.get("authorString") or "").split(",") if name.strip()]


def _item_to_paper(searcher: Any, item: dict[str, Any]) -> Paper | None:
    title = _TAG_RE.sub("", str(item.get("title") or "")).strip()
    if not title:
        return None
    year = safe_int(item.get("pubYear"))
    citations = max(0, safe_int(item.get("citedByCount"), default=0) or 0)
    pmid = str(item.get("pmid") or "").strip() or None
    pmc_id = str(item.get("pmcid") or "").strip() or None
    doi = str(item.get("doi") or "").strip().lower() or None
    source_id = pmc_id or pmid or str(item.get("id") or "").strip()
    mesh_terms = [
        str(term.get("descriptorName") or "").strip()
        for term in ((item.get("meshHeadingList") or {}).get("meshHeading") or [])
        if str(term.get("descriptorName") or "").strip()
    ]
    return searcher._make_paper(
        title=title,
        authors=_author_list(item),
        abstract=_TAG_RE.sub("", str(item.get("abstractText") or "")).strip() or None,
        doi=doi,
        pmid=pmid,
        pmc_id=pmc_id,
        journal=str(item.get("journalTitle") or "").strip() or None,
        year=year,
        volume=str(item.get("journalVolume") or "").strip() or None,
        issue=str(item.get("issue") or "").strip() or None,
        pages=str(item.get("pageInfo") or "").strip() or None,
        publisher=str(item.get("publisherName") or "").strip() or None,
        pdf_url=(f"https://europepmc.org/articles/{pmc_id}?pdf=render" if pmc_id else None),
        source_url=(f"https://europepmc.org/article/MED/{pmid}" if pmid else f"https://europepmc.org/article/{source_id}"),
        mesh_terms=mesh_terms,
        citations=citations,
        source="europe_pmc",
    )


@register_source("europe_pmc")
async def search_europe_pmc(
    searcher: Any, query: str, max_results: int = 10, **kwargs: Any
) -> list[Paper]:
    query = (query or "").strip()
    if not query:
        return []
    await searcher._ensure_async_client()
    await searcher._rate_limit_async("europe_pmc")
    try:
        year_from = int(kwargs.get("year_from") or 1600)
        year_to = int(kwargs.get("year_to") or 2100)
    except (TypeError, ValueError):
        return []
    if year_from > year_to:
        return []
    date_filter = f"FIRST_PDATE:[{year_from}-01-01 TO {year_to}-12-31]"
    api_query = (
        f"({query}) AND {date_filter}"
        if kwargs.get("year_from") or kwargs.get("year_to")
        else query
    )
    params: dict[str, Any] = {
        "query": api_query[:500],
        "format": "json",
        "resultType": "core",
        "pageSize": max(1, min(int(max_results), 25)),
    }
    try:
        response = await searcher._async_http_get_with_retry(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params=params,
            headers=json_api_headers(searcher),
            timeout=float(kwargs.get("http_timeout_sec") or 12.0),
            max_attempts=int(kwargs.get("http_max_attempts") or 1),
        )
        items = ((response.json() or {}).get("resultList") or {}).get("result") or []
    except Exception as exc:
        logger.info("europe_pmc_search_failed: %s", type(exc).__name__)
        return []
    papers = [paper for item in items if (paper := _item_to_paper(searcher, item))]
    return papers[: max(1, min(int(max_results), 25))]
