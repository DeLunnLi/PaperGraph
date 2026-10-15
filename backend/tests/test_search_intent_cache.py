"""Search intent cache regressions with a fake parser and no model calls."""
from types import SimpleNamespace

import pytest

from app.agents.search_agent import (
    IntentParser,
    SearchAgent,
    _INTENT_CACHE,
    _INTENT_CACHE_LOCK,
)
from app.agents.support.search_models import SearchIntent


@pytest.fixture
def parser(monkeypatch):
    with _INTENT_CACHE_LOCK:
        _INTENT_CACHE.clear()
    calls = []
    parser = IntentParser(SimpleNamespace(_normalize_profile=SearchAgent._normalize_profile))

    def parse_fake(message, profile):
        calls.append((message, profile))
        return SearchIntent(
            query=message,
            keywords=["original"],
            sources=["arxiv"],
            year_from=int(message[-4:]) if message[-4:].isdigit() else None,
        )

    monkeypatch.setattr(parser, "_parse_with_retry", parse_fake)
    yield parser, calls
    with _INTENT_CACHE_LOCK:
        _INTENT_CACHE.clear()


def test_long_queries_with_different_years_do_not_share_cached_intents(parser):
    parser, calls = parser
    prefix = "Find research about graph representation learning " * 6
    first = parser.parse(prefix + "published in 2023")
    second = parser.parse(prefix + "published in 2025")
    assert first.year_from == 2023
    assert second.year_from == 2025
    assert len(calls) == 2


def test_mutating_fresh_and_cached_plans_does_not_pollute_future_hits(parser):
    parser, calls = parser
    first = parser.parse("graph research")
    first.keywords.append("changed by reranking")
    first.sources.clear()
    first.query = "changed"

    second = parser.parse("graph research")
    assert second.keywords == ["original"]
    assert second.sources == ["arxiv"]
    assert second.query == "graph research"
    second.keywords.clear()
    assert parser.parse("graph research").keywords == ["original"]
    assert len(calls) == 1


def test_equivalent_inputs_share_cache_but_search_profiles_stay_distinct(parser):
    parser, calls = parser
    parser.parse("  Graph Research  ", profile="unknown")
    parser.parse("graph research", profile=" ACCURACY ")
    parser.parse("graph research", profile="novelty")
    assert len(calls) == 2
    assert [profile for _, profile in calls] == ["accuracy", "novelty"]
