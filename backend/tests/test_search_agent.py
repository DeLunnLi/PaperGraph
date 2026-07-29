"""Golden-case tests for SearchAgent intent parsing.

Stubs the LLM to return canned JSON — no API key required. Covers:
- understand_intent happy path (query/keywords/authors/venues extracted)
- TTL cache hit (second call does not invoke LLM)
- invalid JSON → ValueError surfaced through retry → RuntimeError
- profile normalization (accuracy / novelty / bogus → accuracy)

These snapshot current behaviour so async-wrapping and exception-narrowing
refactors don't regress intent parsing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents import search_agent as search_agent_module
from app.agents.search_agent import SearchAgent
from app.core.paper import Paper
from app.core.search.normalize import plain_query_for_text_apis
from app.core.search.paper_searcher import PaperSearcher
from app.services.retrieval.paper_filters import has_strong_main_conference_venue_signal
from app.services.retrieval.paper_ranker import LlmPaperRanker, RankedPaper
from app.services.retrieval.semantic_scoring import rank_by_semantic_relevance
from app.services.retrieval.search_pipeline import (
    diversify_broad_ranked_results,
    rank_venue_browse_deterministic,
    should_use_llm_rank,
)
from app.services.retrieval.pipeline_runtime import SearchRuntimeConfig
from app.settings.config import Settings
from app.services.retrieval.recall_context import build_recall_context
from app.services.retrieval.recall_jobs import RecallJob, build_recall_jobs, should_run_job
from app.services.retrieval.search_plan import ResolvedSearchPlan
from app.services.retrieval.search_recipe import SearchRecipe, finalize_plan_recipe


class _StubResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubLLM:
    """Returns a queued response per invoke(); records every call."""

    def __init__(self, responses: list[str]) -> None:
        # responses are popped in order; if exhausted, repeats the last.
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def invoke(self, messages, **kw):
        self.calls.append([dict(m) for m in messages])
        if self._responses:
            return _StubResp(self._responses.pop(0))
        return _StubResp("")


def _make_agent(llm: _StubLLM) -> SearchAgent:
    agent = SearchAgent.__new__(SearchAgent)
    agent._settings = None  # not used by parse path except via _cfg fallbacks
    agent.llm = llm
    agent.intent_parser = search_agent_module.IntentParser(agent)
    agent.explainer = search_agent_module._SearchExplainer()
    return agent


def _clear_intent_cache() -> None:
    search_agent_module._INTENT_CACHE.clear()


def _intent_json(**overrides) -> str:
    payload = {
        "query": "diffusion models",
        "keywords": ["score-based", "generative"],
        "authors": [],
        "venues": ["NeurIPS"],
        "year_from": 2022,
        "year_to": 2024,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


# ── understand_intent happy path ────────────────────────────────────

def test_understand_intent_extracts_fields():
    _clear_intent_cache()
    llm = _StubLLM([_intent_json()])
    agent = _make_agent(llm)

    intent = agent.understand_intent("diffusion models recent", profile="accuracy")

    assert intent.query == "diffusion models"
    assert "score-based" in intent.keywords
    assert intent.venues == ["NeurIPS"]
    assert intent.year_from == 2022
    assert intent.year_to == 2024
    assert llm.calls, "LLM should have been invoked at least once"


def test_understand_intent_cache_hit_skips_llm():
    """Second identical call must hit the TTL cache — no extra LLM call."""
    _clear_intent_cache()
    llm = _StubLLM([_intent_json()])
    agent = _make_agent(llm)

    first = agent.understand_intent("diffusion models recent", profile="accuracy")
    calls_after_first = len(llm.calls)
    second = agent.understand_intent("diffusion models recent", profile="accuracy")

    assert second is first or second.query == first.query
    # cache hit → no new LLM invocation
    assert len(llm.calls) == calls_after_first


# ── invalid JSON → retries exhausted → RuntimeError ─────────────────

def test_understand_intent_invalid_json_raises_after_retries():
    _clear_intent_cache()
    # Always returns garbage → every retry fails → IntentParseError("search_agent_intent_failed")
    from app.exceptions import IntentParseError, LLMError
    llm = _StubLLM(["not json at all", "still not json", "{}"])  # {} has no query → empty
    agent = _make_agent(llm)

    try:
        agent.understand_intent("something", profile="accuracy")
    except IntentParseError as e:
        assert e.code == "search_agent_intent_failed"
        return
    except LLMError as e:
        # llm_unavailable also acceptable (subclass of LLMError)
        assert "unavailable" in e.code or "intent_failed" in e.code
        return
    assert False, "expected IntentParseError/LLMError for unparseable intent"


# ── profile normalization ───────────────────────────────────────────

def test_normalize_profile_accepts_accuracy_and_novelty():
    assert SearchAgent._normalize_profile("accuracy") == "accuracy"
    assert SearchAgent._normalize_profile("novelty") == "novelty"
    assert SearchAgent._normalize_profile("bogus") == "accuracy"
    assert SearchAgent._normalize_profile(None) == "accuracy"
    assert SearchAgent._normalize_profile("  ACCURACY  ") == "accuracy"


# ── empty message short-circuit ─────────────────────────────────────

def test_intent_parser_empty_message_returns_default():
    _clear_intent_cache()
    llm = _StubLLM([])
    agent = _make_agent(llm)
    intent = agent.intent_parser.parse("", profile="accuracy")
    # empty message → default SearchIntent, no LLM call
    assert intent.query == ""
    assert llm.calls == []


# ── fallback: query derived from keywords when query empty ──────────

def test_intent_falls_back_to_keywords_when_query_empty():
    _clear_intent_cache()
    payload = _intent_json(query="", keywords=["transformer attention"])
    llm = _StubLLM([payload])
    agent = _make_agent(llm)

    intent = agent.understand_intent("transformer attention", profile="accuracy")
    # _parse_llm_primary: empty query → first keyword becomes query
    assert intent.query == "transformer attention"


def test_explicit_arxiv_id_uses_deterministic_fast_path():
    _clear_intent_cache()
    llm = _StubLLM([_intent_json(query="AutoSchemaKG", keywords=[])])
    agent = _make_agent(llm)

    intent = agent.understand_intent("请查找 arXiv 2505.23628v3", profile="accuracy")

    assert intent.arxiv_id_list == ["2505.23628"]
    assert intent.sources == ["arxiv"]
    assert intent.use_llm_rank is False
    assert intent.search_strategy == "targeted_lookup"
    assert llm.calls == []

    plan = ResolvedSearchPlan.from_search_intent(intent)
    assert plan.sources == ["arxiv"]
    assert plan.use_llm_rank is False


def test_explicit_arxiv_url_is_preserved_when_llm_misses_it():
    _clear_intent_cache()
    llm = _StubLLM([_intent_json(query="paper", keywords=[])])
    agent = _make_agent(llm)

    intent = agent.understand_intent(
        "https://arxiv.org/abs/2505.23628v3", profile="accuracy"
    )

    assert intent.arxiv_id_list == ["2505.23628"]
    assert llm.calls == []


def test_text_api_query_keeps_small_keyword_expansion_set():
    query = plain_query_for_text_apis(
        "graph neural network explainability",
        {"llm_keywords": ["graph neural network", "GNN", "explainable AI", "interpretability"]},
    )

    assert query == "graph neural network explainability GNN explainable AI interpretability"


def test_text_api_query_can_disable_expansion_for_precision():
    query = plain_query_for_text_apis(
        "retrieval augmented generation",
        {
            "llm_keywords": ["RAG", "large language model", "external knowledge"],
            "text_api_query_expansion": False,
        },
    )

    assert query == "retrieval augmented generation"


def test_relevance_post_processing_prefers_topic_match_over_citation_count():
    generic = Paper(
        title="A Survey of Machine Learning",
        abstract="A broad overview.",
        citations=5000,
        source="openalex",
    )
    relevant = Paper(
        title="GNNExplainer: Generating Explanations for Graph Neural Networks",
        abstract="Explainability and interpretability for graph neural network predictions.",
        citations=100,
        arxiv_id="1903.03894",
        source="arxiv",
    )
    searcher = PaperSearcher.__new__(PaperSearcher)

    ranked = searcher._post_process_results(
        [generic, relevant],
        "graph neural network explainability",
        max_results=2,
        sort="relevance",
        llm_keywords=["graph neural network", "explainability", "interpretability"],
    )

    assert ranked[0] is relevant


def test_relevance_post_processing_keeps_canonical_exact_topic_paper_first():
    canonical = Paper(
        title="Retrieval-Augmented Generation for Large Language Models: A Survey",
        abstract="A foundational survey of retrieval augmented generation.",
        citations=681,
        doi="10.48550/arxiv.2312.10997",
        source="openalex",
    )
    niche = Paper(
        title="Performance comparison of retrieval-augmented generation in construction safety",
        abstract="A domain application using a large language model and external knowledge.",
        citations=97,
        doi="10.example/niche",
        source="openalex",
    )
    searcher = PaperSearcher.__new__(PaperSearcher)

    ranked = searcher._post_process_results(
        [niche, canonical],
        "retrieval augmented generation",
        max_results=2,
        sort="relevance",
        llm_keywords=["RAG", "knowledge retrieval", "large language model", "external knowledge"],
    )

    assert ranked[0] is canonical


def test_ranker_recovers_complete_items_from_truncated_json():
    papers = [
        RankedPaper(Paper(title="Paper A")),
        RankedPaper(Paper(title="Paper B")),
        RankedPaper(Paper(title="Paper C")),
    ]
    raw = (
        '{"rankings": ['
        '{"rank": 1, "paper_index": 2, "fine_score": 9.1, "reason": "best"},'
        '{"rank": 2, "paper_index": 1, "fine_score": 8.0, "reason": "good"},'
        '{"rank": 3, "paper_index": 3, "fine_score":'
    )
    ranker = LlmPaperRanker.__new__(LlmPaperRanker)

    ranked = ranker._parse_ranking_result(raw, papers)

    assert [item.paper.title for item in ranked] == ["Paper B", "Paper A"]
    assert ranked[0].fine_score == 9.1


def test_biomedical_domain_adds_europe_pmc_and_crossref_enrichment_jobs():
    plan = ResolvedSearchPlan(
        query="CRISPR cancer therapy",
        keywords=["genome editing", "oncology"],
        research_domain="biomedical",
    )
    runtime = SearchRuntimeConfig.from_settings(Settings(), plan)
    ctx = build_recall_context(plan)

    jobs = build_recall_jobs(plan, ctx, runtime=runtime, constraint_kwargs=ctx.search_kwargs)
    jobs_by_name = {job.name: job for job in jobs}

    assert jobs_by_name["europe_pmc_biomedical"].sources == ["europe_pmc"]
    assert jobs_by_name["crossref_doi_metadata"].run_when == "incomplete_doi_metadata"
    assert jobs_by_name["crossref_doi_metadata"].max_results == 2


def test_general_domain_does_not_add_biomedical_recall():
    plan = ResolvedSearchPlan(query="graph neural network explainability", research_domain="computer_science")
    runtime = SearchRuntimeConfig.from_settings(Settings(), plan)
    ctx = build_recall_context(plan)

    jobs = build_recall_jobs(plan, ctx, runtime=runtime, constraint_kwargs=ctx.search_kwargs)

    assert "europe_pmc_biomedical" not in {job.name for job in jobs}


def test_crossref_enrichment_only_runs_for_incomplete_doi_metadata():
    runtime = SearchRuntimeConfig.from_settings(Settings(), ResolvedSearchPlan())
    plan = ResolvedSearchPlan()
    job = RecallJob("crossref_doi_metadata", "", ["crossref"], 2, run_when="incomplete_doi_metadata")

    assert should_run_job(job, [Paper(title="No identifier")], plan=plan, runtime=runtime) is False
    assert should_run_job(
        job,
        [Paper(title="Complete", doi="10.1000/example", journal="Journal", year=2024)],
        plan=plan,
        runtime=runtime,
    ) is False
    assert should_run_job(
        job,
        [Paper(title="Missing venue", doi="10.1000/example", year=2024)],
        plan=plan,
        runtime=runtime,
    ) is True


def test_title_fallback_runs_only_when_exact_target_is_missing():
    runtime = SearchRuntimeConfig.from_settings(Settings(), ResolvedSearchPlan())
    plan = ResolvedSearchPlan(target_titles=["Attention Is All You Need"])
    plan.recipe = SearchRecipe.TITLE
    job = RecallJob(
        "semantic_scholar_title_fallback",
        plan.target_titles[0],
        ["semantic_scholar"],
        3,
        run_when="missing_target_title",
    )

    assert should_run_job(job, [], plan=plan, runtime=runtime) is True
    assert should_run_job(
        job,
        [Paper(title="Attention is all you need", year=2017)],
        plan=plan,
        runtime=runtime,
    ) is False
    assert should_run_job(
        job,
        [Paper(title="Attention Is Not All You Need", year=2021)],
        plan=plan,
        runtime=runtime,
    ) is True


def test_target_title_plan_uses_deterministic_title_recipe():
    plan = ResolvedSearchPlan(
        query="Attention Is All You Need",
        target_titles=["Attention Is All You Need"],
        use_llm_rank=True,
    )

    finalize_plan_recipe(plan)

    assert plan.recipe == SearchRecipe.TITLE
    assert plan.ranking_profile == "classic"
    assert plan.use_llm_rank is False


def test_exact_target_title_outranks_partial_title_variants():
    exact = Paper(title="Attention Is All You Need", citations=100)
    partial = Paper(title="Cross-Attention Is All You Need for Translation", citations=1000)

    ranked = rank_by_semantic_relevance(
        [partial, exact],
        "Attention Is All You Need",
        target_titles=["Attention Is All You Need"],
        use_embeddings=False,
    )

    assert ranked[0][0] is exact


def test_recent_venue_keeps_explicit_year_range():
    plan = ResolvedSearchPlan(
        query="large language models",
        venues=["ACL"],
        year_from=2021,
        year_to=2022,
        wants_recent=True,
    )

    finalize_plan_recipe(plan)

    assert (plan.year_from, plan.year_to) == (2021, 2022)
    assert plan.sort == "date"


def test_topic_with_pinned_venue_year_requires_proceedings_signal():
    plan = ResolvedSearchPlan(
        query="retrieval augmented generation",
        venues=["SIGIR"],
        year_from=2024,
        year_to=2024,
    )

    finalize_plan_recipe(plan)

    assert plan.main_conference_proceedings_only is True


def test_venue_alias_does_not_match_generic_topic_or_embedded_acronym():
    generic_ir = Paper(title="Retrieval survey", journal="Information Retrieval Journal")
    embedded_acl = Paper(title="Clinical NLP", journal="Journal of Clinical AI")
    sigir = Paper(title="RAG-Ex", journal="Proceedings of the 47th International ACM SIGIR Conference")

    assert has_strong_main_conference_venue_signal(generic_ir, "SIGIR") is False
    assert has_strong_main_conference_venue_signal(embedded_acl, "ACL") is False
    assert has_strong_main_conference_venue_signal(sigir, "SIGIR") is True


def _runtime_for_test(*, proc_min: int = 3) -> SearchRuntimeConfig:
    return SearchRuntimeConfig(
        max_results=10,
        recall_max=24,
        recall_cap=24,
        recall_wall=15,
        rank_wall=15,
        arxiv_fallback_wall=8,
        proc_min=proc_min,
        proc_enabled=True,
        http_timeout_sec=10,
        http_max_attempts=2,
        openalex_timeout_sec=10,
        dblp_timeout_sec=10,
    )


def test_runtime_uses_one_configured_timeout_for_all_primary_sources():
    settings = Settings(
        papergraph_search_recall_http_timeout_sec=7.5,
        papergraph_search_recall_wall_sec=15,
    )
    runtime = SearchRuntimeConfig.from_settings(settings, ResolvedSearchPlan())

    assert runtime.http_timeout_sec == 7.5
    assert runtime.openalex_timeout_sec == 7.5
    assert runtime.dblp_timeout_sec == 7.5
    assert runtime.execution_kwargs()["dblp_timeout_sec"] == 7.5


def test_proceedings_supplement_is_driven_by_verified_candidate_count(monkeypatch):
    monkeypatch.setattr(
        "app.services.retrieval.recall_jobs.should_supplement_from_proceedings_site",
        lambda plan: True,
    )
    plan = ResolvedSearchPlan(venues=["SIGIR"], year_from=2024, year_to=2024)
    job = RecallJob(
        "proceedings",
        "rag",
        ["proceedings"],
        24,
        runner="proceedings",
        run_when="sparse_or_venue_browse",
    )
    verified = [Paper(title=f"Paper {i}", journal="SIGIR", year=2024) for i in range(3)]
    noisy = [Paper(title="Generic RAG", journal="arXiv", year=2024)]

    assert should_run_job(job, verified, plan=plan, runtime=_runtime_for_test()) is False
    assert should_run_job(job, noisy, plan=plan, runtime=_runtime_for_test()) is True


def test_venue_browse_skips_llm_and_uses_deterministic_authority_order():
    plan = ResolvedSearchPlan(
        query="",
        venues=["CVPR"],
        year_from=2024,
        year_to=2024,
        main_conference_proceedings_only=True,
        recipe=SearchRecipe.VENUE_YEAR,
    )
    papers = [
        Paper(title="Low cited", journal="CVPR", year=2024, citations=2),
        Paper(title="High cited", journal="CVPR", year=2024, citations=100),
    ]

    assert should_use_llm_rank(plan) is False
    ranked = rank_venue_browse_deterministic(papers, venue="CVPR", top_k=10)
    assert [item.paper.title for item in ranked] == ["High cited", "Low cited"]


def test_adaptive_ranker_skips_llm_for_general_topic_but_keeps_constraints():
    general = ResolvedSearchPlan(query="retrieval augmented generation", use_llm_rank=True)
    constrained = ResolvedSearchPlan(
        query="retrieval augmented generation",
        venues=["NeurIPS"],
        use_llm_rank=True,
        recipe=SearchRecipe.VENUE_YEAR,
    )

    assert should_use_llm_rank(general) is False
    assert should_use_llm_rank(constrained) is True


def test_broad_result_diversity_limits_one_application_domain():
    ranked = [
        RankedPaper(Paper(title=f"Medical RAG {i}", abstract="clinical patient healthcare"), fine_score=10 - i)
        for i in range(5)
    ] + [
        RankedPaper(Paper(title="RAG Survey", abstract="general retrieval augmented generation"), fine_score=4),
        RankedPaper(Paper(title="Graph RAG", abstract="knowledge graph retrieval"), fine_score=3),
        RankedPaper(Paper(title="Code RAG", abstract="software code repository"), fine_score=2),
    ]

    diverse = diversify_broad_ranked_results(ranked, top_k=6, max_per_domain=3)

    titles = [item.paper.title for item in diverse]
    assert titles[:3] == ["Medical RAG 0", "Medical RAG 1", "Medical RAG 2"]
    assert "RAG Survey" in titles
    assert "Graph RAG" in titles
    assert "Code RAG" in titles


def test_broad_result_diversity_does_not_limit_general_foundational_papers():
    ranked = [
        RankedPaper(Paper(title=f"General RAG Survey {i}", abstract="retrieval augmented generation"), fine_score=10 - i)
        for i in range(5)
    ] + [
        RankedPaper(Paper(title="Medical RAG", abstract="clinical patient healthcare"), fine_score=4),
    ]

    diverse = diversify_broad_ranked_results(ranked, top_k=5, max_per_domain=2)

    assert [item.paper.title for item in diverse] == [
        "General RAG Survey 0",
        "General RAG Survey 1",
        "General RAG Survey 2",
        "General RAG Survey 3",
        "General RAG Survey 4",
    ]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
