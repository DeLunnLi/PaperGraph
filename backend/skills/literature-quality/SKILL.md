---
name: literature-quality
description: Use for paper import, metadata enrichment or merging, candidate ranking, citations, exports, recommendations, negative-feedback learning, and graph construction. Enforces scholarly identity, provenance, deduplication, and hallucination resistance.
---

# Literature Quality

Apply these standards whenever scholarly records or model-generated claims cross a trust boundary.

## Metadata Rules

1. Normalize identifiers at boundaries: lowercase DOI without resolver prefix, canonical arXiv ID, normalized PMID and PMC ID.
2. Prefer identifier evidence over fuzzy title similarity.
3. Do not silently merge records with conflicting DOI, arXiv, PMID, or PMC identities.
4. Preserve the richest abstract, ordered authors, publication details, open-access links, and source provenance when fields do not conflict.
5. Validate years, URLs, citation counts, and enum-like metadata before storage.

## Citation and Graph Rules

- Resolve references only from identifiers or sufficiently strong title/author/year evidence.
- Store relation direction, relation type, confidence, and evidence separately.
- Never present model-generated citations as verified references.
- Make recommendation reasons traceable to source metadata or paper content.
- Keep BibTeX generation deterministic, escaped, and collision-aware.

## Safety

- Scope all saved papers, reading history, graph nodes, relations, and exports to the authenticated user.
- Flag conflicts for review instead of destructively overwriting trusted identifiers.
- Distinguish verified source facts from inferred labels, summaries, and recommendations.
