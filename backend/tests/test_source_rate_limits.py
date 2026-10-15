"""Search sources must throttle their HTTP requests.

Regression: the limiter used a 0.1s fixed delay for arXiv (10 req/s) while
arXiv's API terms require "no more than one request every three seconds"
(https://info.arxiv.org/help/api/tou.html). Exceeding it gets the host 429'd,
and because `_run_one` turns any source error into [], arXiv searches then
silently returned nothing — the daily recommendations page and normal searches
just looked "empty" with no error surfaced to the user.

Offline: asserts on configuration and limiter timing only, no network.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.search.paper_searcher import PaperSearcher, RateLimiter

# arXiv terms of use: one request per three seconds.
ARXIV_MIN_INTERVAL_SEC = 3.0


def test_arxiv_delay_meets_published_minimum() -> None:
    searcher = PaperSearcher()
    delay = searcher._rate._delay_for("arxiv")
    assert delay >= ARXIV_MIN_INTERVAL_SEC, (
        f"arXiv delay {delay}s violates the published 1-request-per-3s limit; "
        "too-fast requests get the host 429'd and searches silently return []"
    )


def test_rate_limiter_actually_spaces_calls() -> None:
    """The limiter must sleep, not just hold a number."""
    limiter = RateLimiter(fixed_delays={"probe": 0.25})

    async def _run() -> float:
        start = time.monotonic()
        await limiter.wait_async("probe")  # first call: no wait
        await limiter.wait_async("probe")  # second: must wait ~0.25s
        return time.monotonic() - start

    elapsed = asyncio.run(_run())
    assert elapsed >= 0.2, f"limiter did not space calls (elapsed {elapsed:.3f}s)"


def test_rate_limiter_serializes_concurrent_requests_per_source() -> None:
    """Concurrent callers must not bypass a source's configured interval.

    Without a source-level lock, every coroutine can read the same old
    timestamp, sleep together, and all update it at once. This violates
    arXiv's one-request-per-three-seconds policy under parallel searches.
    """
    limiter = RateLimiter(fixed_delays={"probe": 0.08})

    async def _run() -> list[float]:
        timestamps: list[float] = []

        async def _one() -> None:
            await limiter.wait_async("probe")
            timestamps.append(time.monotonic())

        await asyncio.gather(*(_one() for _ in range(3)))
        return sorted(timestamps)

    timestamps = asyncio.run(_run())
    gaps = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    assert len(gaps) == 2
    assert min(gaps) >= 0.06, f"concurrent requests bypassed limiter: gaps={gaps}"


def test_zero_delay_source_is_not_throttled() -> None:
    limiter = RateLimiter(fixed_delays={})

    async def _run() -> float:
        start = time.monotonic()
        for _ in range(3):
            await limiter.wait_async("unknown-source")
        return time.monotonic() - start

    assert asyncio.run(_run()) < 0.1


def test_other_sources_keep_their_own_delays() -> None:
    """The arXiv bump must not have disturbed the other sources."""
    rate = PaperSearcher()._rate
    assert rate._delay_for("openalex") == 0.15
    assert rate._delay_for("dblp") == 0.35
    assert rate._delay_for("europe_pmc") == 0.15


def test_semantic_scholar_is_throttled() -> None:
    """The unauthenticated S2 Graph API 429s easily; it had no delay at all and
    returns [] on 429, so failures were invisible."""
    assert PaperSearcher()._rate._delay_for("semantic_scholar") >= 1.0


def test_crossref_is_throttled() -> None:
    """crossref issues concurrent DOI lookups; it was previously unthrottled."""
    assert PaperSearcher()._rate._delay_for("crossref") > 0


@pytest.mark.parametrize(
    "module_name,source_key",
    [
        ("arxiv", "arxiv"),
        ("dblp", "dblp"),
        ("openalex", "openalex"),
        ("europe_pmc", "europe_pmc"),
        ("semantic_scholar", "semantic_scholar"),
        ("crossref", "crossref"),
    ],
)
def test_http_source_invokes_rate_limiter(module_name: str, source_key: str) -> None:
    """A configured delay is inert unless the source awaits the limiter.

    semantic_scholar and crossref both had a delay added but never called
    `_rate_limit_async`, so throttling silently did nothing.
    """
    import importlib
    from pathlib import Path

    mod = importlib.import_module(f"app.core.search.sources.{module_name}")
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert f'_rate_limit_async("{source_key}")' in src, (
        f"{module_name}.py never awaits the rate limiter, so its configured "
        "delay has no effect"
    )
