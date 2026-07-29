"""Semantic relevance scoring for papers —— hybrid keyword + soft matching."""

from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING

from ..embedding.embedding_service import cosine_similarity, embed_texts, embedding_enabled

logger = logging.getLogger(__name__)

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


def _paper_embedding_text(paper: "LitPaper") -> str:
    title = " ".join(str(getattr(paper, "title", "") or "").split())[:500]
    abstract = " ".join(str(getattr(paper, "abstract", "") or "").split())[:3500]
    keywords = ", ".join(str(item).strip() for item in (getattr(paper, "keywords", []) or [])[:12] if str(item).strip())
    return f"Title: {title}\nKeywords: {keywords}\nAbstract: {abstract}".strip()


def rank_by_semantic_relevance(
    papers: list["LitPaper"],
    query: str,
    *,
    keywords: list[str] | None = None,
    target_titles: list[str] | None = None,
    top_k: int | None = None,
    use_embeddings: bool = True,
) -> list[tuple["LitPaper", float]]:
    """Rank papers with lexical signals plus optional embedding similarity.

    Embeddings are a soft ranking signal only. Any endpoint failure falls back to
    deterministic lexical scoring so search availability never depends on it.
    """
    lexical = [calculate_semantic_relevance(p, query, keywords=keywords) for p in papers]
    normalized_targets = {
        _normalize_text(title) for title in (target_titles or []) if _normalize_text(title)
    }
    exact_title = [
        bool(normalized_targets and _normalize_text(getattr(p, "title", None)) in normalized_targets)
        for p in papers
    ]
    # Citation authority is only a tie-breaker gated by topical evidence. This
    # helps seminal papers survive source noise without allowing famous but
    # unrelated papers to outrank an exact topical match.
    citation_logs = [math.log1p(max(0, int(getattr(p, "citations", 0) or 0))) for p in papers]
    max_citation_log = max(citation_logs, default=0.0)
    scores = [
        lex
        + (0.10 * (cit_log / max_citation_log) * min(1.0, lex / 0.20) if max_citation_log else 0.0)
        + (1.0 if exact else 0.0)
        for lex, cit_log, exact in zip(lexical, citation_logs, exact_title)
    ]
    if papers and use_embeddings and embedding_enabled():
        try:
            expanded_query = " ".join([query, *(keywords or [])]).strip()
            vectors = embed_texts([expanded_query, *(_paper_embedding_text(p) for p in papers)])
            query_vector = vectors[0]
            semantic = [max(0.0, cosine_similarity(query_vector, vector)) for vector in vectors[1:]]
            # Keep exact lexical evidence influential while letting cross-language
            # and synonymous matches surface through the embedding signal.
            scores = [
                0.35 * lex + 0.65 * sem
                + (0.08 * (cit_log / max_citation_log) * min(1.0, lex / 0.20) if max_citation_log else 0.0)
                + (1.0 if exact else 0.0)
                for lex, sem, cit_log, exact in zip(lexical, semantic, citation_logs, exact_title)
            ]
        except Exception as exc:
            logger.warning("embedding_ranking_fallback: %s", type(exc).__name__)

    scored = list(zip(papers, scores))
    scored.sort(key=lambda item: item[1], reverse=True)
    if top_k:
        return scored[:top_k]
    return scored
