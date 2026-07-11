"""Semantic relevance scoring for papers —— hybrid keyword + soft matching."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...core.paper import Paper as LitPaper


def _normalize_text(text: str | None) -> str:
    """Normalize text for comparison."""
    if not text:
        return ""
    t = str(text).lower()
    # Remove punctuation but keep spaces for word boundaries
    t = re.sub(r'[^\w\s]', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def _token_overlap_score(query_tokens: set[str], doc_tokens: set[str]) -> float:
    """Calculate Jaccard-like overlap score."""
    if not query_tokens:
        return 0.0
    if not doc_tokens:
        return 0.0
    intersection = query_tokens & doc_tokens
    # Weighted: more weight to exact matches
    return len(intersection) / max(len(query_tokens), len(doc_tokens))


def _ngram_tokens(text: str, n: int = 2) -> set[str]:
    """Extract n-grams from text."""
    words = text.split()
    if len(words) < n:
        return set(words)
    return set(' '.join(words[i:i+n]) for i in range(len(words)-n+1))


def calculate_semantic_relevance(
    paper: "LitPaper",
    query: str,
    *,
    keywords: list[str] | None = None,
    boost_authors: list[str] | None = None,
    boost_venue: str | None = None,
) -> float:
    """
    Calculate semantic relevance score (0.0 - 1.0) between paper and query.

    Uses hybrid approach:
    - Exact keyword matches (high weight)
    - Bigram overlap (medium weight)
    - Unigram overlap (low weight)
    """
    query_norm = _normalize_text(query)
    if not query_norm:
        return 0.0

    # Build document text
    title = _normalize_text(getattr(paper, 'title', None))
    abstract = _normalize_text(getattr(paper, 'abstract', None))
    doc_text = f"{title} {abstract}".strip()

    if not doc_text:
        return 0.0

    score = 0.0

    # 1. Title exact/prefix match (highest weight)
    if title:
        if query_norm in title:
            score += 0.4
        elif title in query_norm:
            score += 0.3
        # Word overlap in title
        query_words = set(query_norm.split())
        title_words = set(title.split())
        title_overlap = len(query_words & title_words) / max(len(query_words), 1)
        score += title_overlap * 0.2

    # 2. Keyword matches (high weight)
    if keywords:
        kw_score = 0.0
        doc_lower = doc_text
        for kw in keywords:
            kw_norm = _normalize_text(kw)
            if not kw_norm:
                continue
            if kw_norm in doc_lower:
                kw_score += 0.15
            elif len(kw_norm) > 5 and any(kw_norm in w for w in doc_lower.split()):
                kw_score += 0.08
        score += min(kw_score, 0.3)  # Cap keyword contribution

    # 3. N-gram semantic overlap (medium weight)
    query_bigrams = _ngram_tokens(query_norm, 2)
    doc_bigrams = _ngram_tokens(doc_text, 2)
    bigram_score = _token_overlap_score(query_bigrams, doc_bigrams)
    score += bigram_score * 0.15

    # 4. Author boost
    if boost_authors:
        authors = getattr(paper, 'authors', []) or []
        author_names = [_normalize_text(getattr(a, 'name', '')) for a in authors]
        for ba in boost_authors:
            ba_norm = _normalize_text(ba)
            if any(ba_norm in an or an in ba_norm for an in author_names if an):
                score += 0.1
                break

    # 5. Venue boost
    if boost_venue:
        venue = _normalize_text(getattr(paper, 'journal', None) or getattr(paper, 'venue', ''))
        if venue and boost_venue.lower() in venue:
            score += 0.05

    return min(score, 1.0)


def rank_by_semantic_relevance(
    papers: list["LitPaper"],
    query: str,
    *,
    keywords: list[str] | None = None,
    top_k: int | None = None,
) -> list[tuple["LitPaper", float]]:
    """Rank papers by semantic relevance, return (paper, score) tuples."""
    scored = [
        (p, calculate_semantic_relevance(p, query, keywords=keywords))
        for p in papers
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    if top_k:
        return scored[:top_k]
    return scored
