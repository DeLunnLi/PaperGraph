---
name: academic-search
description: Use for literature discovery, exact-paper lookup, author or method searches, deep-search decomposition and synthesis, biomedical retrieval, and venue/year constraints. Applies source-aware recall, identifier verification, deduplication, and evidence-based ranking.
---

# Academic Search

Apply these standards to literature discovery, query decomposition, candidate ranking, and search-result synthesis.

## Workflow

1. Classify the request as exact identifier, exact title, author, venue/year, method, biomedical, or broad topic.
2. Prefer stable identifiers in this order: DOI, arXiv ID, PMID/PMC ID, then normalized title plus year.
3. Use complementary sources rather than treating every source as equally authoritative:
   - arXiv for preprints and recent computer-science work.
   - DBLP for computer-science authors and proceedings.
   - OpenAlex for broad discovery and citation metadata.
   - Semantic Scholar only as an exact-title fallback when primary sources miss the target.
   - Europe PMC for biomedical and life-science discovery.
   - Crossref only to verify and enrich an already known DOI.
4. Apply venue and year constraints before final ranking. Do not infer a venue from topical similarity.
5. Deduplicate by identifiers before normalized title. Preserve richer non-conflicting metadata when merging.
6. Rank exact title and hard-constraint evidence before citation authority. Citations are a tie-breaker, not proof of relevance.
7. If a source fails or times out, retain useful results from independent sources and disclose reduced coverage.

## Quality Rules

- Never fabricate a paper, DOI, author, venue, citation count, or PDF URL.
- Do not accept a fuzzy title candidate as an exact-paper result without strong normalized-title evidence.
- Keep canonical publications and preprints linked when identifiers support the relation, but do not destructively merge conflicting identities.
- For biomedical requests, retain PMID, PMC ID, MeSH terms, and open-access evidence when available.
- Explain why leading papers match the query using verified metadata and content evidence.
