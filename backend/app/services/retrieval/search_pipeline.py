from __future__ import annotations

from collections import Counter
import logging
import re
from dataclasses import dataclass
from typing import Any

import anyio

logger = logging.getLogger(__name__)

from ...core.paper import Paper as LitPaper
from ...settings import get_settings
from .paper_filters import has_strong_main_conference_venue_signal, should_exclude_main_conference_paper
from .paper_ranker import LlmPaperRanker, RankedPaper
from .pipeline_runtime import SearchRuntimeConfig
from .plan_helpers import is_venue_browse_plan, method_acronym_for, primary_venue
from .recall_context import RecallContext, build_recall_context, enrich_recall_context_from_tavily
from .recall_jobs import build_recall_jobs, dedupe_papers, execute_recall_jobs, merge_candidates
from .relevance_guard import apply_relevance_guard
from .search_plan import ResolvedSearchPlan
from .search_recipe import SearchRecipe
from .semantic_scoring import rank_by_semantic_relevance


_DOMAIN_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("biomedical", ("medical", "medicine", "health", "clinical", "patient", "disease", "biomedical", "hospital", "hepat", "nephro", "drug")),
    ("software", ("software", "code", "programming", "test case", "repository")),
    ("knowledge_graph", ("knowledge graph", "graph retrieval", "ontology")),
    ("security", ("security", "privacy", "attack", "injection", "vulnerability")),
    ("networking", ("network", "communication", "wireless", "6g", "telecom")),
    ("science", ("chemistry", "material", "molecule", "scientific discovery")),
)


def _paper_domain_bucket(paper: LitPaper) -> str:
    fields = [
        getattr(paper, "title", "") or "",
        getattr(paper, "abstract", "") or "",
        getattr(paper, "journal", "") or "",
        " ".join(str(x) for x in (getattr(paper, "keywords", None) or [])[:12]),
    ]
    blob = re.sub(r"\s+", " ", " ".join(fields).lower())
    for bucket, signals in _DOMAIN_SIGNALS:
        if any(signal in blob for signal in signals):
            return bucket
    return "general"


def diversify_broad_ranked_results(
    ranked: list[RankedPaper],
    *,
    top_k: int,
    max_per_domain: int = 3,
) -> list[RankedPaper]:
    """Keep broad-topic results relevant while preventing one application domain dominating."""
    if len(ranked) <= max_per_domain or top_k <= 3:
        return ranked[:top_k]
    selected: list[RankedPaper] = []
    deferred: list[RankedPaper] = []
    counts: Counter[str] = Counter()
    for item in ranked:
        bucket = _paper_domain_bucket(item.paper)
        # "general" contains foundational/survey papers rather than one narrow
        # application domain; limiting it would push canonical work out of Top-K.
        if bucket == "general" or counts[bucket] < max_per_domain:
            selected.append(item)
            counts[bucket] += 1
        else:
            deferred.append(item)
        if len(selected) >= top_k:
            return selected[:top_k]
    selected.extend(deferred[: max(0, top_k - len(selected))])
    return selected[:top_k]


@dataclass
class SearchPipelineResult:
    effective_query: str
    total_candidates: int
    ranking_method: str
    ranked: list[RankedPaper]
    metadata: dict[str, Any]
    plan: dict[str, Any]
    plan_explanation: str


async def _merge_pinned_papers(
    candidates: list[LitPaper], pinned_ids: list[str], searcher: Any
) -> list[LitPaper]:
    if not pinned_ids:
        return candidates
    try:
        if hasattr(searcher, "search_async"):
            pinned = await searcher.search_async(
                "",
                sources=["arxiv"],
                arxiv_id_list=pinned_ids,
                max_results=len(pinned_ids) * 2,
            )
        elif hasattr(searcher, "search_by_arxiv_ids"):
            pinned = await anyio.to_thread.run_sync(searcher.search_by_arxiv_ids, pinned_ids)
        else:
            pinned = await anyio.to_thread.run_sync(
                lambda: searcher.search(
                    "",
                    sources=["arxiv"],
                    arxiv_id_list=pinned_ids,
                    max_results=len(pinned_ids) * 2,
                )
            )
        pinned = pinned or []
    except Exception:
        pinned = []
    return merge_candidates(candidates, list(pinned), "prepend")


def _merge_target_titles(plan: ResolvedSearchPlan, ctx: RecallContext) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in list(plan.target_titles or []) + list(ctx.canonical_titles or []):
        tl = (t or "").strip()
        if tl and tl.lower() not in seen:
            seen.add(tl.lower())
            out.append(tl)
    return out[:6]


def normalize_and_filter_candidates(
    candidates: list[LitPaper],
    *,
    plan: ResolvedSearchPlan,
    ctx: RecallContext,
    recall_cap: int,
    meta: dict[str, Any],
) -> list[LitPaper]:
    venue = primary_venue(plan)
    ma = method_acronym_for(plan, ctx) or None
    candidates = dedupe_papers(candidates)
    before_year_filter = len(candidates)
    if plan.year_from is not None:
        candidates = [p for p in candidates if getattr(p, "year", None) is not None and int(p.year) >= plan.year_from]
    if plan.year_to is not None:
        candidates = [p for p in candidates if getattr(p, "year", None) is not None and int(p.year) <= plan.year_to]
    if len(candidates) != before_year_filter:
        meta["year_filter_removed"] = before_year_filter - len(candidates)

    if ma:
        from .method_acronym import paper_matches_method_query

        narrowed = [
            p
            for p in candidates
            if paper_matches_method_query(
                p,
                ma,
                canonical_titles=ctx.canonical_titles,
                pinned_arxiv_ids=ctx.pinned_arxiv_ids,
                venue=venue,
            )
        ]
        if narrowed:
            candidates = narrowed

    guard_threshold = max(36, recall_cap + 8)
    if ma:
        guard_threshold = max(10, min(guard_threshold, len(candidates) + 2))
    if not is_venue_browse_plan(plan):
        candidates, guard_applied = apply_relevance_guard(candidates, plan=plan, guard_threshold=guard_threshold)
        if guard_applied:
            meta["relevance_guard"] = True

    if plan.main_conference_proceedings_only and venue:
        pin_y = plan.year_from if plan.year_from == plan.year_to else None
        # Only require strong venue signal if we actually found venue-verified papers
        from .paper_filters import has_strong_main_conference_venue_signal
        venue_verified_count = sum(1 for p in candidates if has_strong_main_conference_venue_signal(p, venue))
        require_venue_signal = venue_verified_count >= 3
        candidates = [
            p
            for p in candidates
            if not should_exclude_main_conference_paper(
                p,
                venue,
                pinned_year=pin_y,
                require_venue_signal=require_venue_signal,
            )
        ]
    return candidates


def should_use_llm_rank(plan: ResolvedSearchPlan) -> bool:
    """Reserve expensive LLM ranking for genuinely ambiguous/constrained searches."""
    if not plan.use_llm_rank:
        return False
    if is_venue_browse_plan(plan):
        return False
    return bool(
        plan.recipe in {SearchRecipe.METHOD, SearchRecipe.TITLE, SearchRecipe.VENUE_YEAR}
        or plan.target_titles
        or plan.authors
        or plan.venues
        or plan.method_acronym
    )


def rank_venue_browse_deterministic(
    candidates: list[LitPaper], *, venue: str | None, top_k: int
) -> list[RankedPaper]:
    """Rank a pure proceedings browse without an unnecessary LLM call."""
    ordered = sorted(
        candidates,
        key=lambda paper: (
            not has_strong_main_conference_venue_signal(paper, venue),
            -int(getattr(paper, "citations", 0) or 0),
            (getattr(paper, "title", "") or "").lower(),
        ),
    )
    return [
        RankedPaper(paper=paper, fine_score=float(getattr(paper, "citations", 0) or 0))
        for paper in ordered[:top_k]
    ]


async def rank_candidates(
    candidates: list[LitPaper],
    *,
    plan: ResolvedSearchPlan,
    ctx: RecallContext,
    runtime: SearchRuntimeConfig,
    meta: dict[str, Any],
) -> tuple[list[RankedPaper], str, dict[str, Any]]:
    if not candidates:
        return [], "recall_only", {}
    if is_venue_browse_plan(plan):
        ranked = rank_venue_browse_deterministic(
            candidates, venue=primary_venue(plan), top_k=runtime.max_results
        )
        return ranked, "deterministic_venue_browse", {
            "llm_rank_skipped": True,
            "skip_reason": "pure_venue_browse_no_topic",
        }
    use_llm_rank = should_use_llm_rank(plan)
    venue = primary_venue(plan)
    constraints_fully_verified = bool(
        venue
        and plan.main_conference_proceedings_only
        and candidates
        and all(has_strong_main_conference_venue_signal(paper, venue) for paper in candidates)
    )
    if not use_llm_rank or constraints_fully_verified:
        scored = rank_by_semantic_relevance(
            candidates,
            ctx.enhanced_query or ctx.rank_query,
            keywords=ctx.merged_keywords,
            target_titles=_merge_target_titles(plan, ctx),
        )
        semantic_ranked = [RankedPaper(paper=p, fine_score=score) for p, score in scored]
        if not (plan.authors or plan.venues or plan.target_titles or plan.year_from or plan.year_to):
            semantic_ranked = diversify_broad_ranked_results(
                semantic_ranked, top_k=runtime.max_results
            )
        return (
            semantic_ranked[: runtime.max_results],
            "hybrid_semantic" if not plan.use_llm_rank else "adaptive_semantic",
            {
                "semantic_scores": [rp.fine_score for rp in semantic_ranked[: runtime.max_results]],
                "llm_rank_skipped": bool(plan.use_llm_rank),
                "skip_reason": (
                    "constraints_fully_verified"
                    if constraints_fully_verified
                    else "deterministic_general_topic" if plan.use_llm_rank else None
                ),
            },
        )

    ranker = LlmPaperRanker(recall_max=runtime.recall_max, fine_top_k=runtime.max_results)
    prefer_rec = (plan.sort or "").strip().lower() == "date" or bool(plan.year_from) or bool(venue)
    try:
        with anyio.fail_after(runtime.rank_wall):
            ranked, ranking_metadata = await anyio.to_thread.run_sync(
                lambda: ranker.rank(
                    candidates,
                    ctx.rank_query,
                    runtime.max_results,
                    ranking_profile=ctx.ranking_profile,
                    target_venue=venue,
                    target_titles=_merge_target_titles(plan, ctx),
                    authors=list(plan.authors or []),
                    venues=list(plan.venues or []),
                    year_from=plan.year_from,
                    year_to=plan.year_to,
                    sort=plan.sort,
                    prefer_recency=prefer_rec,
                    main_conference_proceedings_only=bool(plan.main_conference_proceedings_only),
                    intent_source_message=ctx.intent_source_message,
                    method_acronym=ctx.search_kwargs.get("method_acronym"),
                    keywords=ctx.merged_keywords,
                    _wall_timeout_sec=runtime.rank_wall,
                ),
                abandon_on_cancel=True,
            )
        return ranked, ranking_metadata.get("ranking_method", "llm_rank"), ranking_metadata
    except TimeoutError:
        meta["ranking_timeout"] = True
        # Use semantic scoring as fallback instead of simple recency
        use_semantic = bool(ctx.enhanced_query or ctx.rank_query)
        if use_semantic:
            try:
                scored = rank_by_semantic_relevance(
                    candidates,
                    ctx.enhanced_query or ctx.rank_query,
                    keywords=ctx.merged_keywords,
                    target_titles=_merge_target_titles(plan, ctx),
                )
                ranked = [RankedPaper(paper=p, fine_score=score) for p, score in scored]
                if not (plan.authors or plan.venues or plan.target_titles or plan.year_from or plan.year_to):
                    ranked = diversify_broad_ranked_results(
                        ranked, top_k=runtime.max_results
                    )
                ranked = ranked[: runtime.max_results]
                return ranked, "semantic_fallback_timeout", {"semantic_scores": [rp.fine_score for rp in ranked]}
            except Exception:
                pass
        from .paper_ranker import _papers_to_ranked_pool

        pool = _papers_to_ranked_pool(candidates, cap=runtime.recall_max, prefer_recency=prefer_rec)
        return pool[: runtime.max_results], "recall_fallback_timeout", {}


async def run_search_pipeline_async(
    *,
    searcher: Any,
    plan: ResolvedSearchPlan,
    max_results: int | None = None,
) -> SearchPipelineResult:
    runtime = SearchRuntimeConfig.from_settings(get_settings(), plan, max_results)
    ctx = await enrich_recall_context_from_tavily(build_recall_context(plan), plan)

    meta: dict[str, Any] = {
        "ranking_profile": ctx.ranking_profile,
        "source_plan": ctx.source_plan,
        "recall_context": {
            "effective_query": ctx.effective_query[:200],
            "rank_query": ctx.rank_query[:200],
            "merged_keywords": ctx.merged_keywords[:12],
        },
        "search_recipe": plan.recipe.value,
    }
    fallbacks: list[dict[str, Any]] = []

    constraint_kwargs = {**ctx.search_kwargs, "sort": plan.sort or ctx.search_kwargs.get("sort") or "relevance"}
    jobs = build_recall_jobs(plan, ctx, runtime=runtime, constraint_kwargs=constraint_kwargs)
    candidates = await execute_recall_jobs(
        searcher, jobs, plan=plan, ctx=ctx, runtime=runtime, meta=meta, fallbacks=fallbacks
    )

    pinned_ids = list(ctx.pinned_arxiv_ids or [])
    # The primary arXiv job already receives arxiv_id_list. Only retry missing IDs;
    # this removes a duplicate network request from exact-ID searches.
    found_ids = {
        str(getattr(p, "arxiv_id", "") or "").strip().lower().split("v", 1)[0]
        for p in candidates
        if getattr(p, "arxiv_id", None)
    }
    missing_pinned = [
        aid for aid in pinned_ids
        if aid.strip().lower().split("v", 1)[0] not in found_ids
    ]
    if missing_pinned and searcher is not None:
        try:
            with anyio.fail_after(min(8.0, runtime.recall_wall)):
                candidates = await _merge_pinned_papers(candidates, missing_pinned, searcher)
        except TimeoutError:
            fallbacks.append({"stage": "pinned_arxiv", "reason": "timeout"})

    candidates = normalize_and_filter_candidates(
        candidates, plan=plan, ctx=ctx, recall_cap=runtime.recall_cap, meta=meta
    )
    ranked, ranking_method, ranking_metadata = await rank_candidates(
        candidates, plan=plan, ctx=ctx, runtime=runtime, meta=meta
    )

    if not candidates and plan.fallback.allow_arxiv_only:
        fallbacks.append({"type": "arxiv_only", "reason": "no_candidates_after_recall"})

    sc = Counter(getattr(p, "source", "unknown") or "unknown" for p in candidates)
    rsc = Counter(getattr(rp.paper, "source", "unknown") or "unknown" for rp in ranked)
    metadata = {
        "tavily_enabled": plan.use_tavily,
        "tavily_keywords_count": len(ctx.tavily_keywords),
        "anchor_title": ctx.canonical_titles[0] if ctx.canonical_titles else None,
        "anchor_arxiv_ids": pinned_ids,
        "pinned_arxiv_ids": pinned_ids,
        "fallbacks": fallbacks,
        "candidates_by_source": dict(sc),
        "ranked_by_source": dict(rsc),
        "deduped_total": len(candidates),
        "final_ranked": len(ranked),
        **meta,
    }
    if ranking_metadata:
        metadata["ranking"] = ranking_metadata

    logger.info(
        "search.pipeline.done effective_query=%r recipe=%s candidates=%d ranked=%d ranking_method=%s",
        ctx.effective_query,
        plan.recipe.value,
        len(candidates),
        len(ranked),
        ranking_method,
    )

    return SearchPipelineResult(
        effective_query=ctx.effective_query,
        total_candidates=len(candidates),
        ranking_method=ranking_method,
        ranked=ranked,
        metadata=metadata,
        plan={
            "llm_keywords": ctx.merged_keywords,
            "tavily_keywords": ctx.tavily_keywords,
            "canonical_titles": ctx.canonical_titles,
            "recipe": plan.recipe.value,
        },
        plan_explanation="",
    )
