"""SearchRecipe — RECIPE_RULES 表统一判定与应用。"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Callable

from .method_acronym import resolve_method_acronym
from .plan_helpers import _keywords, _venues, is_pinned_single_year

if TYPE_CHECKING:
    from .search_plan import ResolvedSearchPlan


class SearchRecipe(str, Enum):
    GENERAL = "general"
    TITLE = "title"
    AUTHOR = "author"
    VENUE_YEAR = "venue_year"
    METHOD = "method"
    DEEP = "deep"


def _is_title(plan: "ResolvedSearchPlan") -> bool:
    return bool([title for title in (plan.target_titles or []) if str(title).strip()])


def _apply_title(plan: "ResolvedSearchPlan") -> None:
    # 标题查询的目标是找原文而非扩展主题；保留多源交叉验证，关闭昂贵且
    # 非确定的 LLM 微调，由精确标题信号与引用权威性完成排序。
    plan.ranking_profile = "classic"
    plan.use_llm_rank = False


def _is_venue_method(plan: "ResolvedSearchPlan") -> bool:
    return bool(_venues(plan) and resolve_method_acronym(plan.query or "", _keywords(plan)))


def _is_venue_year(plan: "ResolvedSearchPlan") -> bool:
    return bool(_venues(plan))


def _is_method(plan: "ResolvedSearchPlan") -> bool:
    return bool(resolve_method_acronym(plan.query or "", _keywords(plan)))


def _normalize_venue_years(plan: "ResolvedSearchPlan") -> None:
    venues = _venues(plan)
    if not venues:
        return

    yf, yt = plan.year_from, plan.year_to
    if plan.wants_recent and not plan.wants_classic:
        plan.main_conference_proceedings_only = True
        plan.sort = "date"
        from ..search_intent.parsing import infer_target_edition_year_for_recent

        # “最新”只在用户未给出任何年份时推断会议届次。显式年份或区间
        # 是硬约束，不能因为 LLM 同时设置 wants_recent 而被当前年份覆盖。
        if yf is None and yt is None:
            pin_y = infer_target_edition_year_for_recent(is_latest=True)
            yf = yt = pin_y

    if yf is not None and yt is None:
        yt = yf
    plan.year_from, plan.year_to = yf, yt

    pinned_single_year = isinstance(yf, int) and isinstance(yt, int) and yf == yt
    has_topic = bool(_keywords(plan) or (plan.query or "").strip())
    # 明确写出“会议 + 单年份”时，会议归属与年份都是硬约束；不仅限于
    # 无主题的 proceedings 浏览，否则主题检索会混入同年 arXiv/其他会论文。
    if pinned_single_year:
        plan.main_conference_proceedings_only = True
    if pinned_single_year and not has_topic and not plan.authors and not plan.target_titles:
        if not (plan.sort or "").strip() or plan.sort == "relevance":
            plan.sort = "date"


def _apply_venue_year(plan: "ResolvedSearchPlan", *, skip_browse_limits: bool = False) -> None:
    _normalize_venue_years(plan)
    if not (is_pinned_single_year(plan) and plan.main_conference_proceedings_only):
        return

    # Keep all sources (arxiv + dblp + openalex) — let relevance guard and LLM ranker filter,
    # instead of hardcoding source exclusion.
    if "arxiv" not in plan.sources:
        plan.sources = ["arxiv"] + plan.sources
    try:
        from ...settings import get_settings

        recall_cap = int(get_settings().papergraph_recall_max_candidates)
    except Exception:
        recall_cap = 24
    plan.recall_max_candidates = min(max(int(plan.recall_max_candidates or 24), 8), recall_cap)

    venues = _venues(plan)
    venue = venues[0] if venues else ""
    year = plan.year_from if isinstance(plan.year_from, int) else None
    orig_q = (plan.query or "").strip()
    v_blob = " ".join(v.lower() for v in venues)

    if not orig_q or any(v.lower() == orig_q.lower() for v in venues):
        plan.query = ""

    plan.keywords = [
        k
        for k in (plan.keywords or [])
        if str(k).strip()
        and str(k).strip().lower() not in v_blob
        and "computer vision" not in str(k).lower()
    ][:8]

    from ...core.search.normalize import extract_pinned_topic_terms

    query_only_topic = extract_pinned_topic_terms(
        query=orig_q, merged_kw=list(plan.keywords or []), venue=venue, year=year
    ).strip()
    if not (plan.query or "").strip():
        if query_only_topic:
            plan.query = query_only_topic[:200]
        else:
            # 用户句子里无独立主题（如「CVPR 最新论文」）；丢弃 LLM 附带的 latest/papers 等
            plan.keywords = []

    if skip_browse_limits:
        return
    from .plan_helpers import effective_max_results, effective_recall_max_candidates, is_venue_browse_plan

    if is_venue_browse_plan(plan):
        plan.max_results = effective_max_results(plan, plan.max_results)
        plan.recall_max_candidates = effective_recall_max_candidates(plan, plan.recall_max_candidates)


def _apply_method(plan: "ResolvedSearchPlan") -> None:
    ma = resolve_method_acronym(plan.query or "", _keywords(plan))
    if ma:
        plan.method_acronym = ma


def _apply_method_at_venue(plan: "ResolvedSearchPlan") -> None:
    _apply_method(plan)
    _apply_venue_year(plan, skip_browse_limits=True)


RecipeApplyFn = Callable[["ResolvedSearchPlan"], None]
RecipeRule = tuple[Callable[["ResolvedSearchPlan"], bool], SearchRecipe, RecipeApplyFn]

RECIPE_RULES: list[RecipeRule] = [
    (_is_title, SearchRecipe.TITLE, _apply_title),
    (_is_venue_method, SearchRecipe.METHOD, _apply_method_at_venue),
    (_is_venue_year, SearchRecipe.VENUE_YEAR, _apply_venue_year),
    (_is_method, SearchRecipe.METHOD, _apply_method),
]


def finalize_plan_recipe(plan: "ResolvedSearchPlan") -> "ResolvedSearchPlan":
    if getattr(plan, "deep_search", False):
        plan.recipe = SearchRecipe.DEEP
        plan.max_iterations = max(0, min(3, int(plan.max_iterations or 2)))
        return plan
    for predicate, recipe, apply_fn in RECIPE_RULES:
        if predicate(plan):
            plan.recipe = recipe
            apply_fn(plan)
            return plan
    plan.recipe = SearchRecipe.GENERAL
    return plan
