"""Query enhancement and expansion for better search recall and precision."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .search_plan import ResolvedSearchPlan


# Common academic term expansions
TERM_EXPANSIONS: dict[str, list[str]] = {
    "llm": ["large language model", "language model"],
    "lm": ["language model"],
    "nlp": ["natural language processing"],
    "cv": ["computer vision"],
    "ml": ["machine learning"],
    "dl": ["deep learning"],
    "rl": ["reinforcement learning"],
    "ai": ["artificial intelligence"],
    "nn": ["neural network", "neural networks"],
    "transformer": ["attention mechanism", "self-attention"],
    "bert": ["pre-training", "language representation"],
    "gpt": ["generative pre-training", "language model"],
    "cnn": ["convolutional neural network"],
    "rnn": ["recurrent neural network", "lstm", "gru"],
    "gan": ["generative adversarial network"],
    "vae": ["variational autoencoder"],
}

def expand_query_terms(query: str) -> list[str]:
    """Expand query with related terms for better recall."""
    query_lower = query.lower()
    expansions = []

    for term, related in TERM_EXPANSIONS.items():
        if term in query_lower:
            for rel in related:
                if rel not in query_lower:
                    expansions.append(rel)

    return expansions


def build_enhanced_query(
    plan: "ResolvedSearchPlan",
    *,
    include_expansions: bool = True,
) -> str:
    """Build an enhanced query string from search plan.

    Combines original query, keywords, and optionally term expansions
    for better semantic matching.
    """
    parts = []

    # Original query
    if plan.query:
        parts.append(plan.query.strip())

    # Keywords
    if plan.keywords:
        kw_str = " ".join(str(k).strip() for k in plan.keywords if str(k).strip())
        if kw_str:
            parts.append(kw_str)

    # Method acronym expansion
    if plan.method_acronym:
        parts.append(plan.method_acronym)

    # Term expansions for better recall
    if include_expansions and plan.query:
        expansions = expand_query_terms(plan.query)
        if expansions:
            parts.extend(expansions[:3])  # Limit expansions

    # Deduplicate while preserving order
    seen = set()
    unique_parts = []
    for p in parts:
        p_lower = p.lower()
        if p_lower not in seen:
            seen.add(p_lower)
            unique_parts.append(p)

    return " ".join(unique_parts)


def calculate_query_specificity(plan: "ResolvedSearchPlan") -> float:
    """How constrained a query is (0.0–1.0).

    More constraints (authors, venues, years, target titles) → higher score.
    """
    score = 0.0
    if plan.query and len(plan.query.strip()) > 10:
        score += 0.2
    if plan.keywords:
        score += min(len(plan.keywords) * 0.05, 0.2)
    if plan.authors:
        score += min(len(plan.authors) * 0.1, 0.2)
    if plan.venues:
        score += min(len(plan.venues) * 0.1, 0.2)
    if plan.year_from or plan.year_to:
        score += 0.1
    if plan.target_titles:
        score += min(len(plan.target_titles) * 0.05, 0.1)
    return min(score, 1.0)


def optimize_recall_strategy(plan: "ResolvedSearchPlan") -> dict[str, Any]:
    """Tune recall parameters and source weights from query analysis.

    Returns ``{recall_cap, use_expansion, source_weights}``.
    """
    specificity = calculate_query_specificity(plan)
    is_broad = specificity < 0.3
    is_venue_specific = bool(plan.venues and not plan.query)

    strategy: dict[str, Any] = {
        "recall_cap": 36 if is_broad else 24,
        "use_expansion": is_broad,
        "source_weights": {"arxiv": 1.0, "openalex": 1.0, "dblp": 1.0},
    }

    if is_venue_specific:
        strategy["source_weights"]["dblp"] = 1.3
        strategy["source_weights"]["openalex"] = 1.2
    if plan.wants_recent:
        strategy["source_weights"]["arxiv"] = 1.2
    if plan.wants_classic:
        strategy["source_weights"]["openalex"] = 1.3
        strategy["source_weights"]["dblp"] = 1.2

    return strategy
