"""Live connectivity smoke test for every registered search source.

WHY THIS EXISTS
---------------
`paper_searcher._run_one` catches every exception, logs a WARNING, records a
circuit-breaker failure and returns []. A source can therefore be completely
broken while the product merely "returns fewer results" — no error surfaces to
the user and nothing fails in CI. Two real bugs hid exactly this way:

  * arXiv: a boolean OR collapsed into one literal phrase. The API answered
    200 with 30 entries, but they were months old, so the days_back filter
    dropped all of them and daily recommendations rendered empty.
  * OpenAlex: `publication_year:>=2024` is rejected with HTTP 400 ("Value for
    param publication_year must be a number"). Every year-filtered OpenAlex
    search failed outright, including the daily fallback path.

Both were invisible to the unit suite because it never touches the network.

USAGE
-----
Opt-in only — these tests hit real third-party APIs, so they are skipped by
default to keep the normal suite fast, offline and deterministic:

    PG_LIVE_SMOKE=1 pytest tests/test_search_sources_live_smoke.py -v

A failure here means "this source is returning nothing usable right now"; that
can be an upstream outage rather than our bug, so treat it as a signal to
investigate, not as a hard build gate.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

from app.core.search.paper_searcher import PaperSearcher
from app.core.search.sources.base import get_source, list_sources

pytestmark = pytest.mark.skipif(
    not os.environ.get("PG_LIVE_SMOKE"),
    reason="live network smoke test; set PG_LIVE_SMOKE=1 to run",
)

# Sources needing credentials/servers that are not guaranteed in a dev checkout.
_NEEDS_EXTERNAL_SETUP = {"mcp", "tavily"}

# Not keyword-search engines: these are *lookup/enrichment* sources that return
# [] unless given their specific input (crossref needs dois=[...], semantic
# scholar needs target_titles=[...]). They get their own targeted tests below.
_LOOKUP_ONLY = {"crossref", "semantic_scholar"}

QUERY = "knowledge graph"


def _keyword_search_sources() -> list[str]:
    return [
        s
        for s in list_sources()
        if s not in _NEEDS_EXTERNAL_SETUP and s not in _LOOKUP_ONLY
    ]


# Cheapest possible "is the upstream alive?" probe per source. Used to tell a
# third-party outage (skip) apart from our own broken query building (fail) —
# otherwise this suite cries wolf every time e.g. DBLP has a bad afternoon.
_HEALTH_URLS = {
    "arxiv": "https://export.arxiv.org/api/query?search_query=all:test&max_results=1",
    "dblp": "https://dblp.org/search/publ/api?q=test&format=json&h=1",
    "openalex": "https://api.openalex.org/works?search=test&per_page=1",
    "europe_pmc": (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        "?query=test&format=json&pageSize=1"
    ),
}


def _upstream_alive(source: str) -> bool:
    url = _HEALTH_URLS.get(source)
    if not url:
        return True  # no probe defined; assume reachable and let the test judge
    try:
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    except httpx.HTTPError:
        return False
    # 4xx (other than 429) means the service answered — that is our problem.
    return resp.status_code < 500 and resp.status_code != 429


@pytest.mark.parametrize("source", _keyword_search_sources())
def test_keyword_source_returns_results(source: str) -> None:
    """Every keyword-search source must return papers for a broad, common query."""
    if not _upstream_alive(source):
        pytest.skip(f"{source} upstream unreachable/5xx/429 right now — not our bug")

    async def _run() -> list:
        searcher = PaperSearcher()
        return await searcher.search_async(QUERY, sources=[source], max_results=5)

    papers = asyncio.run(_run())
    assert papers, (
        f"source '{source}' returned no results for {QUERY!r} while its API was "
        "reachable, so this is most likely our query/filter construction "
        "(see this module's docstring for two bugs that looked exactly like this)."
    )


def test_crossref_doi_lookup() -> None:
    """crossref resolves DOIs; it is not a keyword search engine."""

    async def _run() -> list:
        searcher = PaperSearcher()
        fn = get_source("crossref")
        return await fn(searcher, "", 5, dois=["10.1145/3292500.3330989"])

    papers = asyncio.run(_run())
    assert papers, "crossref DOI lookup returned nothing"


def test_semantic_scholar_title_lookup() -> None:
    """semantic_scholar resolves exact titles; it is not a keyword search engine."""

    async def _run() -> list:
        searcher = PaperSearcher()
        fn = get_source("semantic_scholar")
        return await fn(searcher, "", 5, target_titles=["Attention Is All You Need"])

    papers = asyncio.run(_run())
    assert papers, "semantic_scholar title lookup returned nothing"


def test_openalex_year_filter_does_not_400() -> None:
    """Regression: a start-year filter used invalid `>=` syntax and 400'd."""

    async def _run() -> list:
        searcher = PaperSearcher()
        return await searcher.search_openalex_async(QUERY, 5, year_from=2024)

    papers = asyncio.run(_run())
    assert papers, "OpenAlex year_from search returned nothing (was HTTP 400)"
    years = [getattr(p, "year", None) for p in papers]
    assert all(y is None or y >= 2024 for y in years), f"year filter not applied: {years}"


def test_arxiv_or_query_returns_recent_papers() -> None:
    """Regression: an OR query became one literal phrase and matched only
    stale papers, which the days_back window then discarded."""

    async def _run() -> list:
        searcher = PaperSearcher()
        return await searcher.search_arxiv_async(
            '"knowledge graph" OR "retrieval augmented generation"',
            30,
            days_back=14,
            arxiv_categories=["cs.CL"],
            arxiv_query_style="ti_abs",
        )

    papers = asyncio.run(_run())
    assert papers, "arXiv OR query returned nothing within the days_back window"
