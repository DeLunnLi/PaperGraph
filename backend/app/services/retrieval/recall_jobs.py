"""RecallJob — capability 驱动；build + execute 合一模块。"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable, Literal

import anyio

from ...core.paper import Paper as LitPaper
from ...core.search import PaperSearcher
from ...core.search.normalize import titles_match_strict
from .method_acronym import derive_full_title_from_named_method
from .paper_filters import has_strong_main_conference_venue_signal
from .plan_helpers import (
    is_venue_browse_plan,
    method_acronym_for,
    primary_venue,
    should_supplement_from_proceedings_site,
)
from .pipeline_runtime import SearchRuntimeConfig
from .proceedings_recall import recall_from_proceedings_site
from .recall_context import RecallContext
from .search_plan import ResolvedSearchPlan
from .search_recipe import SearchRecipe

MergeStrategy = Literal["prepend", "replace", "append"]
RunWhen = Literal["always", "empty_candidates", "missing_target_title", "incomplete_doi_metadata", "sparse_or_venue_browse"]
SideEffect = Literal["none", "derive_method_title", "record_arxiv_fallback"]
Runner = Literal["search", "proceedings"]

_SKIP_KW = frozenset({"sources", "max_results"})


@dataclass
class RecallJob:
    name: str
    query: str
    sources: list[str]
    max_results: int
    kwargs: dict[str, Any] = field(default_factory=dict)
    runner: Runner = "search"
    merge_strategy: MergeStrategy = "prepend"
    run_when: RunWhen = "always"
    side_effect: SideEffect = "none"
    required: bool = False
    needs_derived_query: bool = False
    timeout_sec: float | None = None


def dedupe_papers(
    papers: list[LitPaper],
    *,
    identity_fn: Callable[[LitPaper], str] | None = None,
) -> list[LitPaper]:
    if not papers:
        return []
    if identity_fn is not None:
        seen, out = set(), []
        for p in papers:
            k = identity_fn(p) or f"untitled:{id(p)}"
            if k in seen:
                continue
            seen.add(k)
            out.append(p)
        papers = out
    searcher = PaperSearcher.__new__(PaperSearcher)
    return PaperSearcher._smart_deduplicate(searcher, papers)


def merge_candidates(
    current: list[LitPaper],
    batch: list[LitPaper],
    strategy: MergeStrategy,
) -> list[LitPaper]:
    if not batch:
        return current
    if strategy == "replace":
        return dedupe_papers(list(batch))
    if strategy == "append":
        return dedupe_papers(current + batch)
    return dedupe_papers(batch + current)


def should_run_job(
    job: RecallJob,
    candidates: list[LitPaper],
    *,
    plan: ResolvedSearchPlan,
    runtime: SearchRuntimeConfig,
) -> bool:
    if job.run_when == "empty_candidates":
        return not candidates
    if job.run_when == "incomplete_doi_metadata":
        return any(
            str(getattr(paper, "doi", None) or "").strip()
            and (not str(getattr(paper, "journal", None) or "").strip() or getattr(paper, "year", None) is None)
            for paper in candidates
        )
    if job.run_when == "missing_target_title":
        targets = [str(title).strip() for title in (plan.target_titles or []) if str(title).strip()]
        return bool(targets) and not any(
            titles_match_strict(getattr(paper, "title", None), target)
            for paper in candidates
            for target in targets
        )
    if job.run_when == "sparse_or_venue_browse":
        if not runtime.proc_enabled or not should_supplement_from_proceedings_site(plan):
            return False
        if is_venue_browse_plan(plan):
            return True
        venue = primary_venue(plan)
        # Proceedings 补召回代价高，只在主召回没有足量“可证明属于目标会议”
        # 的论文时执行。不能仅因查询包含 venue 就无条件再跑一轮网页发现。
        verified = sum(
            1
            for paper in candidates
            if has_strong_main_conference_venue_signal(paper, venue)
            and (
                plan.year_from is None
                or getattr(paper, "year", None) is not None
                and int(paper.year) >= int(plan.year_from)
            )
            and (
                plan.year_to is None
                or getattr(paper, "year", None) is not None
                and int(paper.year) <= int(plan.year_to)
            )
        )
        return verified < runtime.proc_min
    return True


def _job_wall(job: RecallJob, runtime: SearchRuntimeConfig) -> float:
    if job.timeout_sec is not None:
        return float(job.timeout_sec)
    if job.run_when == "empty_candidates":
        return runtime.arxiv_fallback_wall
    if job.required:
        return runtime.recall_wall
    return 12.0


def _constraint_kwargs(constraint_kwargs: dict[str, Any], plan: ResolvedSearchPlan, runtime: SearchRuntimeConfig) -> dict[str, Any]:
    sk = {k: v for k, v in constraint_kwargs.items() if k not in _SKIP_KW}
    sk["sort"] = plan.sort or sk.get("sort") or "relevance"
    sk.update(runtime.execution_kwargs())
    return sk


def build_recall_jobs(
    plan: ResolvedSearchPlan,
    ctx: RecallContext,
    *,
    runtime: SearchRuntimeConfig,
    constraint_kwargs: dict[str, Any],
) -> list[RecallJob]:
    sk = _constraint_kwargs(constraint_kwargs, plan, runtime)
    jobs: list[RecallJob] = [
        RecallJob(
            "primary",
            ctx.effective_query,
            list(ctx.recall_sources),
            runtime.recall_cap,
            kwargs=dict(sk),
            required=True,
        )
    ]

    if plan.research_domain == "biomedical" and plan.recipe != SearchRecipe.VENUE_YEAR:
        jobs.append(
            RecallJob(
                "europe_pmc_biomedical",
                ctx.effective_query,
                ["europe_pmc"],
                min(16, runtime.recall_cap),
                kwargs={**sk, "http_timeout_sec": min(12.0, runtime.http_timeout_sec), "http_max_attempts": 1},
                merge_strategy="append",
                timeout_sec=14.0,
            )
        )

    if plan.recipe == SearchRecipe.TITLE and plan.target_titles:
        jobs.append(
            RecallJob(
                "semantic_scholar_title_fallback",
                str(plan.target_titles[0]),
                ["semantic_scholar"],
                3,
                kwargs={**sk, "target_titles": list(plan.target_titles), "http_timeout_sec": 10, "http_max_attempts": 1},
                run_when="missing_target_title",
                timeout_sec=12.0,
            )
        )

    ma = method_acronym_for(plan, ctx)
    if ma and plan.recipe in (SearchRecipe.METHOD, SearchRecipe.VENUE_YEAR):
        ax_sk = {**sk, "llm_keywords": [ma]}
        jobs.append(
            RecallJob("method_arxiv_boost", ma, ["arxiv"], 16, kwargs=ax_sk, side_effect="derive_method_title", timeout_sec=12.0)
        )
        if plan.venues:
            jobs.append(
                RecallJob(
                    "method_venue_recall",
                    "",
                    ["dblp", "openalex"],
                    runtime.recall_cap,
                    kwargs={k: v for k, v in sk.items() if k != "venue_browse"},
                    needs_derived_query=True,
                    timeout_sec=18.0,
                )
            )

    if plan.fallback.allow_arxiv_only and "arxiv" not in ctx.recall_sources:
        q = (ctx.effective_query or ctx.rank_query or plan.query or "")[:100]
        jobs.append(
            RecallJob(
                "arxiv_fallback",
                q,
                ["arxiv"],
                20,
                kwargs={k: v for k, v in sk.items() if k != "venue_fallback_if_empty"}
                | {"http_timeout_sec": 8, "http_max_attempts": 1},
                merge_strategy="replace",
                run_when="empty_candidates",
                side_effect="record_arxiv_fallback",
            )
        )

    jobs.append(
        RecallJob(
            "crossref_doi_metadata",
            "",
            ["crossref"],
            2,
            kwargs={**sk, "http_timeout_sec": min(10.0, runtime.http_timeout_sec), "http_max_attempts": 1},
            merge_strategy="append",
            run_when="incomplete_doi_metadata",
            timeout_sec=12.0,
        )
    )

    if should_supplement_from_proceedings_site(plan):
        jobs.append(
            RecallJob(
                "proceedings",
                ctx.effective_query,
                ["proceedings"],
                runtime.recall_cap,
                runner="proceedings",
                run_when="sparse_or_venue_browse",
            )
        )
    return jobs


def enrich_method_context_from_boost(ax_papers: list[LitPaper], method_acronym: str, ctx: RecallContext) -> str | None:
    derived: str | None = None
    for p in ax_papers:
        if full := derive_full_title_from_named_method(p, method_acronym):
            if full not in ctx.canonical_titles:
                ctx.canonical_titles.append(full)
            derived = derived or full
    if derived:
        tt = list(ctx.search_kwargs.get("target_titles") or [])
        if derived not in tt:
            ctx.search_kwargs["target_titles"] = (tt + [derived])[:6]
    return derived


async def _run_search_job(searcher: Any, job: RecallJob, runtime: SearchRuntimeConfig) -> tuple[list[LitPaper], str | None]:
    if not searcher:
        return [], None
    wall = _job_wall(job, runtime)
    sk = {k: v for k, v in job.kwargs.items() if k not in _SKIP_KW}
    sk.setdefault("sort", "relevance")
    try:
        with anyio.fail_after(wall):
            if hasattr(searcher, "search_async"):
                papers = await searcher.search_async(job.query, sources=job.sources, max_results=job.max_results, **sk)
            else:
                papers = await anyio.to_thread.run_sync(
                    lambda: searcher.search(job.query, sources=job.sources, max_results=job.max_results, **sk)
                )
            return list(papers or []), None
    except TimeoutError:
        return ([], f"多源召回超时（{wall:.0f}秒）") if job.required else ([], None)
    except Exception as e:
        return ([], f"搜索异常: {str(e)[:100]}") if job.required else ([], None)


async def execute_recall_jobs(
    searcher: Any,
    jobs: list[RecallJob],
    *,
    plan: ResolvedSearchPlan,
    ctx: RecallContext,
    runtime: SearchRuntimeConfig,
    meta: dict[str, Any],
    fallbacks: list[dict[str, Any]],
) -> list[LitPaper]:
    candidates: list[LitPaper] = []
    search_error: str | None = None
    pending_derived = next((j for j in jobs if j.needs_derived_query), None)
    jobs_executed: list[str] = []
    venue = primary_venue(plan)

    for job in jobs:
        if job.needs_derived_query or not should_run_job(job, candidates, plan=plan, runtime=runtime):
            continue

        if job.run_when == "incomplete_doi_metadata":
            dois = list(dict.fromkeys(
                str(getattr(paper, "doi", None) or "").strip().lower()
                for paper in candidates
                if str(getattr(paper, "doi", None) or "").strip()
                and (not str(getattr(paper, "journal", None) or "").strip() or getattr(paper, "year", None) is None)
            ))[:2]
            job = replace(job, max_results=len(dois), kwargs={**job.kwargs, "dois": dois})

        batch: list[LitPaper] = []
        try:
            if job.runner == "proceedings":
                wall = max(8.0, min(45.0, runtime.recall_wall * 0.6))
                with anyio.fail_after(wall):
                    batch = list(
                        await recall_from_proceedings_site(
                            searcher, plan=plan, ctx=ctx, max_results=job.max_results
                        )
                        or []
                    )
            else:
                batch, err = await _run_search_job(searcher, job, runtime)
                if job.required:
                    search_error = err
        except TimeoutError:
            if job.runner == "proceedings":
                meta["proceedings_supplement"] = {"error": "timeout"}
            continue
        except Exception as e:
            if job.runner == "proceedings":
                meta["proceedings_supplement"] = {"error": str(e)[:120]}
            continue

        if not batch and job.runner != "proceedings":
            continue

        if job.runner == "proceedings":
            before = len(candidates)
            candidates = merge_candidates(candidates, batch, job.merge_strategy)
            meta["proceedings_supplement"] = {
                "venue": venue,
                "year": plan.year_from,
                "added": len(candidates) - before,
                "source": "openaccess_proceedings",
            }
            fallbacks.append(
                {"type": "proceedings_site", "reason": "sparse_dblp_openalex_main_track", "count": len(batch)}
            )
        else:
            candidates = merge_candidates(candidates, batch, job.merge_strategy)
            if job.side_effect == "derive_method_title" and batch:
                ma = method_acronym_for(plan, ctx)
                if ma:
                    meta["method_acronym_arxiv_boost"] = len(batch)
                    derived = enrich_method_context_from_boost(batch, ma, ctx)
                    if pending_derived and derived:
                        if resolved := _resolve_derived_job(pending_derived, derived):
                            extra, _ = await _run_search_job(searcher, resolved, runtime)
                            if extra:
                                meta["method_acronym_venue_recall"] = len(extra)
                                candidates = merge_candidates(candidates, extra, "prepend")
                            pending_derived = None
            if job.side_effect == "record_arxiv_fallback":
                fallbacks.append({"type": "arxiv_only", "reason": "no_candidates_from_primary"})
                meta.setdefault("search_debug", {})["fallback"] = "arxiv_only"

        jobs_executed.append(job.name)

    meta["search_debug"] = {
        "effective_query": ctx.effective_query[:200],
        "rank_query": ctx.rank_query[:200],
        "candidates_raw_count": len(candidates),
        "search_error": search_error,
        "recall_sources": list(ctx.recall_sources),
        "recipe": plan.recipe.value,
        "jobs_executed": jobs_executed,
    }
    return candidates


def _resolve_derived_job(job: RecallJob, derived_query: str) -> RecallJob | None:
    if len(derived_query) < 12:
        return None
    kwargs = {**job.kwargs, "target_titles": [derived_query]}
    return replace(job, query=derived_query, kwargs=kwargs, needs_derived_query=False)
