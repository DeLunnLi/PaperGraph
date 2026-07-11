"""Reciprocal Rank Fusion (RRF) for multi-list result merging.

RRF score(p) = Σ_{l ∈ lists} 1/(k + rank_l(p))

This is a差异化 feature — GPT Researcher, STORM, and open-deep-research all
use URL-dedup + string-concat, none implement RRF. PaperGraph's multi-subquery
parallel recall naturally produces multiple ranked lists, making RRF a natural fit.
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable

from ...core.paper import Paper as LitPaper
from ...core.search.normalize import _normalize_title_for_dedupe, _arxiv_canonical_from_paper


def _paper_identity(p: LitPaper) -> str:
    """Stable identity key for dedup — same logic as PaperSearcher._paper_dedupe_key."""
    ax = _arxiv_canonical_from_paper(p)
    if ax:
        return f"arxiv:{ax}"
    doi = (p.doi or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    nt = _normalize_title_for_dedupe(p.title)
    if not nt:
        return f"empty:{id(p)}"
    return "title:" + hashlib.md5(nt.encode("utf-8")).hexdigest()


def rrf_fuse(
    ranked_lists: list[list[LitPaper]],
    *,
    k: int = 60,
    identity_fn: Callable[[LitPaper], str] | None = None,
) -> list[tuple[LitPaper, float]]:
    """Fuse multiple ranked lists via Reciprocal Rank Fusion.

    Args:
        ranked_lists: each inner list is already ranked (index 0 = best).
        k: smoothing constant (default 60, standard RRF value).
        identity_fn: function to derive stable paper identity.

    Returns:
        [(paper, rrf_score)] sorted by rrf_score descending.
    """
    identity = identity_fn or _paper_identity
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, LitPaper] = {}

    for lst in ranked_lists:
        for rank, paper in enumerate(lst, start=1):
            key = identity(paper)
            scores[key] += 1.0 / (k + rank)
            if key not in best:
                best[key] = paper

    fused = sorted(
        ((best[key], score) for key, score in scores.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return fused


def rrf_fuse_weighted(
    ranked_lists: list[tuple[list[LitPaper], float]],
    *,
    k: int = 60,
    identity_fn: Callable[[LitPaper], str] | None = None,
) -> list[tuple[LitPaper, float]]:
    """Weighted RRF - allows giving different weights to different source lists.

    Args:
        ranked_lists: list of (papers, weight) tuples. Each paper list is already ranked.
        k: smoothing constant (default 60).
        identity_fn: function to derive stable paper identity.

    Returns:
        [(paper, rrf_score)] sorted by rrf_score descending.
    """
    identity = identity_fn or _paper_identity
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, LitPaper] = {}

    for lst, weight in ranked_lists:
        for rank, paper in enumerate(lst, start=1):
            key = identity(paper)
            # Weighted contribution: weight * 1/(k + rank)
            scores[key] += weight * (1.0 / (k + rank))
            if key not in best:
                best[key] = paper

    fused = sorted(
        ((best[key], score) for key, score in scores.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return fused


@dataclass
class RecordedCandidate:
    """A paper retrieved during deep search, with provenance for RRF."""
    paper: LitPaper
    sub_query: str
    round: int
    source_rank: int  # rank within this sub_query's results (1-based)


def rrf_fuse_recorded(
    candidates: list[RecordedCandidate],
    *,
    k: int = 60,
    identity_fn: Callable[[LitPaper], str] | None = None,
) -> list[tuple[LitPaper, float]]:
    """Fuse RecordedCandidates via RRF.

    Each (sub_query, round) pair forms an independent ranked list.
    Papers appearing in multiple sub-queries accumulate RRF score.
    """
    identity = identity_fn or _paper_identity
    scores: dict[str, float] = defaultdict(float)
    best: dict[str, LitPaper] = {}

    # Group by (sub_query, round) — each group is a ranked list
    groups: dict[tuple[str, int], list[RecordedCandidate]] = defaultdict(list)
    for c in candidates:
        groups[(c.sub_query, c.round)].append(c)

    for (_sq, _r), group in groups.items():
        # Sort by source_rank to ensure correct ranking order
        group.sort(key=lambda c: c.source_rank)
        for rank, cand in enumerate(group, start=1):
            key = identity(cand.paper)
            scores[key] += 1.0 / (k + rank)
            if key not in best:
                best[key] = cand.paper

    fused = sorted(
        ((best[key], score) for key, score in scores.items()),
        key=lambda x: x[1],
        reverse=True,
    )
    return fused
