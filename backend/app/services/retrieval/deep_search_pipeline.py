"""Deep search pipeline: query decomposition → iterative retrieval → RRF fusion → LLM rank → synthesis.

This is a parallel pipeline that does NOT modify the standard search flow.
When plan.deep_search=True, the search_pipeline dispatches here instead.

Strategy (inspired by GPT Researcher / STORM / mshumer):
1. LLM decomposes user query into 2-4 complementary sub-queries
2. Round 1: parallel search_async for each sub-query → RecordedCandidate[]
3. (Optional) Round 2+: LLM checks for blind spots → may add sub-queries → parallel search
4. RRF fuse all candidates across sub-queries and rounds
5. LlmPaperRanker.rank on top-24 fused → final ranking
6. (Optional) LLM synthesizes a 300-500 word brief from top-8

LLM calls ≤ 5: decompose(1) + expand(0-2) + rank(1) + synthesis(0-1)
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ...core.paper import Paper as LitPaper
from ..llm.agent_runtime import run_agent_task, run_json_task
from ..llm.llm_service import is_llm_configured
from ...agents.prompts.deep_search import (
    DEEP_SEARCH_DECOMPOSE_PROMPT,
    DEEP_SEARCH_EXPAND_PROMPT,
    DEEP_SEARCH_SYNTHESIS_PROMPT,
)
from ...settings import get_settings
from .paper_ranker import LlmPaperRanker, RankedPaper
from .rrf_fusion import RecordedCandidate, rrf_fuse_recorded
from .search_plan import ResolvedSearchPlan

logger = logging.getLogger(__name__)


@dataclass
class DeepSearchPipelineResult:
    effective_query: str
    total_candidates: int
    ranking_method: str  # "rrf_llm" / "rrf_only" / "decompose_fallback"
    ranked: list[RankedPaper]
    synthesis: str
    metadata: dict[str, Any]


def decompose_query(
    llm: Any,
    user_query: str,
    plan: ResolvedSearchPlan,
    *,
    max_sub_queries: int = 4,
    timeout_sec: float = 20.0,
) -> list[str]:
    """Use LLM to decompose user query into complementary sub-queries."""
    if not is_llm_configured():
        return [plan.query or user_query]
    s = get_settings()
    prompt = DEEP_SEARCH_DECOMPOSE_PROMPT.format(
        max_subqueries=max_sub_queries,
        user_query=user_query[:800],
        keywords=", ".join(plan.keywords[:8]) or "—",
        venues=", ".join(plan.venues[:4]) or "—",
        year_from=plan.year_from or "—",
        year_to=plan.year_to or "—",
        sort=plan.sort,
    )
    try:
        data = run_json_task(
            task_name="deep_search_decompose",
            agent_name="deep_search_decomposer",
            llm=llm,
            system_prompt="你是学术检索子问题分解器。只输出 JSON。",
            user_prompt=prompt,
            timeout_sec=timeout_sec,
            retries=1,
        )
        subs = data.get("sub_queries") or []
        if isinstance(subs, list):
            subs = [str(x).strip() for x in subs if str(x).strip()][:max_sub_queries]
            if subs:
                logger.info("deep_search: decomposed into %d sub-queries: %s", len(subs), subs)
                return subs
    except Exception:
        logger.warning("deep_search: decompose failed, using original query")
    return [plan.query or user_query]


async def _search_subqueries_parallel(
    searcher: Any,
    sub_queries: list[str],
    plan: ResolvedSearchPlan,
    *,
    per_subquery_max: int = 12,
    round_idx: int = 0,
) -> list[RecordedCandidate]:
    """Run search_async for each sub-query in parallel, collect RecordedCandidates."""
    sources = plan.sources or ["arxiv", "dblp", "openalex"]
    timeout = 45.0

    async def _search_one(sq: str) -> list[RecordedCandidate]:
        try:
            papers = await asyncio.wait_for(
                searcher.search_async(sq, sources=sources, max_results=per_subquery_max, http_timeout_sec=timeout),
                timeout=timeout + 5,
            )
            return [
                RecordedCandidate(paper=p, sub_query=sq, round=round_idx, source_rank=i + 1)
                for i, p in enumerate(papers or [])
            ]
        except Exception as e:
            logger.warning("deep_search: sub-query '%s' failed: %s", sq[:60], str(e)[:120])
            return []

    results = await asyncio.gather(*[_search_one(sq) for sq in sub_queries])
    all_cands: list[RecordedCandidate] = []
    for r in results:
        all_cands.extend(r)
    return all_cands


def _should_continue_iteration(
    llm: Any,
    user_query: str,
    used_subqueries: list[str],
    accumulated_count: int,
    round_idx: int,
    sample_titles: list[str],
    *,
    threshold: int = 30,
    max_new: int = 2,
    timeout_sec: float = 15.0,
) -> tuple[bool, list[str]]:
    """LLM decides whether to continue iteration and proposes new sub-queries."""
    if accumulated_count >= threshold:
        return False, []
    if not is_llm_configured():
        return False, []
    prompt = DEEP_SEARCH_EXPAND_PROMPT.format(
        round=round_idx + 1,
        accumulated=accumulated_count,
        user_query=user_query[:500],
        used_subqueries="\n".join(f"- {sq}" for sq in used_subqueries),
        sample_titles="\n".join(f"- {t}" for t in sample_titles[:10]),
        threshold=threshold,
        max_new=max_new,
    )
    try:
        data = run_json_task(
            task_name="deep_search_expand",
            agent_name="deep_search_expander",
            llm=llm,
            system_prompt="你是学术检索盲区检测器。只输出 JSON。",
            user_prompt=prompt,
            timeout_sec=timeout_sec,
            retries=0,
        )
        done = bool(data.get("done", False))
        new_subs = data.get("new_sub_queries") or []
        if isinstance(new_subs, list):
            new_subs = [str(x).strip() for x in new_subs if str(x).strip()][:max_new]
        else:
            new_subs = []
        return (not done and len(new_subs) > 0), new_subs
    except Exception:
        logger.debug("deep_search: expand check failed, stopping iteration")
        return False, []


def synthesize_brief(
    llm: Any,
    user_query: str,
    ranked: list[RankedPaper],
    *,
    timeout_sec: float = 45.0,
) -> str:
    """Generate a 300-500 word synthesis from top papers."""
    if not ranked or not is_llm_configured():
        return ""
    top = ranked[:8]
    papers_text = []
    for i, rp in enumerate(top, 1):
        p = rp.paper
        authors = ", ".join(a.name for a in (p.authors or [])[:3])
        if len(p.authors or []) > 3:
            authors += " et al."
        abstract = (p.abstract or "")[:150]
        papers_text.append(f"{i}. **{p.title}** ({authors}, {p.year or '—'})\n   {abstract}")
    prompt = DEEP_SEARCH_SYNTHESIS_PROMPT.format(
        user_query=user_query[:500],
        papers_with_abstracts="\n\n".join(papers_text),
    )
    try:
        text = run_agent_task(
            task_name="deep_search_synthesis",
            agent_name="deep_search_synthesizer",
            llm=llm,
            system_prompt="你是学术综述撰写专家。直接输出 Markdown 综述。",
            user_prompt=prompt,
            timeout_sec=timeout_sec,
            retries=0,
        )
        return (text or "").strip()
    except Exception:
        logger.warning("deep_search: synthesis failed")
        return ""


async def run_deep_search_pipeline_async(
    *,
    searcher: Any,
    plan: ResolvedSearchPlan,
    max_results: int | None = None,
    llm: Any | None = None,
    progress_callback: Callable[[str, dict], None] | None = None,
) -> DeepSearchPipelineResult:
    """Main entry point for deep search pipeline."""
    s = get_settings()
    mr = max_results or plan.max_results or 15
    user_query = plan.raw_user_message or plan.query
    max_sub = int(getattr(s, "papergraph_deep_search_max_sub_queries", 4))
    max_iter = min(int(plan.max_iterations or 2), int(getattr(s, "papergraph_deep_search_max_iterations", 2)))
    per_sq = int(getattr(s, "papergraph_deep_search_recall_per_subquery", 12))
    decomp_timeout = float(getattr(s, "papergraph_deep_search_decompose_timeout_sec", 20.0))
    synth_enabled = bool(getattr(s, "papergraph_deep_search_synthesis_enabled", True))
    synth_timeout = float(getattr(s, "papergraph_deep_search_synthesis_timeout_sec", 45.0))

    def _emit(event_type: str, payload: dict) -> None:
        if progress_callback:
            try:
                progress_callback(event_type, payload)
            except Exception:
                pass

    # --- Step 1: Decompose ---
    _emit("deep:decompose", {"phase": "decompose"})
    sub_queries = decompose_query(llm, user_query, plan, max_sub_queries=max_sub, timeout_sec=decomp_timeout)
    _emit("deep:decompose", {"sub_queries": sub_queries, "round": 0})

    all_candidates: list[RecordedCandidate] = []
    all_used_subqueries = list(sub_queries)

    # --- Step 2: Iterative retrieval ---
    for round_idx in range(max_iter):
        _emit("deep:round", {
            "round": round_idx,
            "total_rounds": max_iter,
            "n_subqueries": len(sub_queries),
            "phase": "search",
        })
        round_cands = await _search_subqueries_parallel(
            searcher, sub_queries, plan,
            per_subquery_max=per_sq, round_idx=round_idx,
        )
        all_candidates.extend(round_cands)
        logger.info("deep_search: round %d got %d candidates (total %d)", round_idx, len(round_cands), len(all_candidates))

        # Check if we should continue
        if round_idx + 1 < max_iter and all_candidates:
            unique_count = len(set(_paper_identity_short(c.paper) for c in all_candidates))
            sample_titles = [str(c.paper.title or "")[:100] for c in all_candidates[:10]]
            should_cont, new_subs = _should_continue_iteration(
                llm, user_query, all_used_subqueries, unique_count, round_idx, sample_titles,
                threshold=mr * 3,
            )
            if should_cont and new_subs:
                _emit("deep:expand", {"new_subqueries": new_subs, "round": round_idx + 1})
                all_used_subqueries.extend(new_subs)
                sub_queries = new_subs
            else:
                logger.info("deep_search: stopping after round %d (done or threshold)", round_idx)
                break
        else:
            break

    if not all_candidates:
        return DeepSearchPipelineResult(
            effective_query=user_query, total_candidates=0,
            ranking_method="empty", ranked=[], synthesis="",
            metadata={"sub_queries": all_used_subqueries, "rounds": max_iter},
        )

    # --- Step 3: RRF Fusion ---
    _emit("deep:rrf", {"fused_count": len(all_candidates), "phase": "rrf"})
    fused = rrf_fuse_recorded(all_candidates, k=60)
    fused_papers = [p for p, _ in fused]
    rrf_scores = {id(p): score for p, score in fused}
    logger.info("deep_search: RRF fused %d unique papers", len(fused_papers))

    # --- Step 4: LLM Rank ---
    _emit("deep:rank", {"phase": "rank", "candidate_count": min(len(fused_papers), 24)})
    ranking_method = "rrf_only"
    ranked: list[RankedPaper] = []
    if fused_papers:
        top_for_rank = fused_papers[:24]
        if is_llm_configured() and llm:
            try:
                ranker = LlmPaperRanker(recall_max=24, fine_top_k=mr)
                ranked, rank_meta = ranker.rank(
                    top_for_rank, plan.query or user_query, top_k=mr,
                    ranking_profile=plan.ranking_profile,
                    target_venue=(plan.venues[0] if plan.venues else None),
                    main_conference_proceedings_only=plan.main_conference_proceedings_only,
                    year_from=plan.year_from,
                    year_to=plan.year_to,
                )
                ranking_method = "rrf_llm"
            except Exception:
                logger.warning("deep_search: LLM rank failed, using RRF order")
                ranked = [
                    RankedPaper(paper=p, final_score=rrf_scores.get(id(p), 0.0))
                    for p in top_for_rank[:mr]
                ]
        else:
            ranked = [
                RankedPaper(paper=p, final_score=rrf_scores.get(id(p), 0.0))
                for p in top_for_rank[:mr]
            ]

    # --- Step 5: Synthesis ---
    synthesis = ""
    if synth_enabled and ranked:
        _emit("deep:synthesis", {"phase": "synthesis"})
        synthesis = synthesize_brief(llm, user_query, ranked, timeout_sec=synth_timeout)

    result = DeepSearchPipelineResult(
        effective_query=user_query,
        total_candidates=len(fused_papers),
        ranking_method=ranking_method,
        ranked=ranked,
        synthesis=synthesis,
        metadata={
            "sub_queries": all_used_subqueries,
            "rounds": max_iter,
            "rrf_top_scores": [round(s, 6) for _, s in fused[:5]],
        },
    )

    # Write deep search insights to shared memory for other agents
    try:
        from ...services.memory.agent_memory import get_agent_memory
        am = get_agent_memory()
        top_titles = "; ".join(
            str(getattr(rp.paper, "title", "") or "")[:50]
            for rp in (ranked or [])[:3]
        )
        am.add(
            agent_name="deep_search",
            content=f"[深度搜索] {user_query[:60]} → {len(ranked)}篇(method={ranking_method}): {top_titles}",
            memory_type="working",
            importance=0.6,
            shared=True,
            tags=["deep_search"],
        )
        if synthesis and len(synthesis) > 100:
            am.add(
                agent_name="deep_search",
                content=f"[深度搜索综述] {user_query[:40]}: {synthesis[:300]}",
                memory_type="episodic",
                importance=0.65,
                shared=True,
                tags=["deep_search"],
            )
    except Exception:
        logger.debug("deep_search: shared memory write failed", exc_info=True)

    logger.info("deep_search: done method=%s ranked=%d synthesis=%d chars",
                ranking_method, len(ranked), len(synthesis))
    return result


def _paper_identity_short(p: LitPaper) -> str:
    """Quick identity for counting unique papers."""
    ax = (getattr(p, "arxiv_id", None) or "").strip().lower()
    if ax:
        return f"arxiv:{ax}"
    doi = (getattr(p, "doi", None) or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    title = (getattr(p, "title", None) or "").strip().lower()[:80]
    return f"title:{title}"
