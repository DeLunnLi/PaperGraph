---
name: knowledge-graph
description: Use for extracting, validating, storing, or explaining relations among papers, methods, datasets, tasks, and authors. Builds evidence-grounded scholarly graphs with typed, directional, confidence-scored relations.
---

# Scholarly Knowledge Graph

Apply these standards to relation extraction, graph persistence, graph traversal, and graph-based explanations.

## Relation Workflow

1. Resolve each paper to stable identifiers before creating edges.
2. Use explicit directional relation types such as cites, extends, compares_with, uses_method, uses_dataset, and contradicts.
3. Attach evidence independently from relation labels and confidence scores.
4. Prefer direct citation/reference evidence; use title, author, and year evidence only when identifiers are unavailable.
5. Avoid converting semantic similarity into a factual citation or extension relation.
6. Surface uncertainty and conflicting evidence rather than forcing a single relation.

## Integrity Rules

- Never create nodes for unverified, model-invented papers.
- Never infer citation direction from publication year alone.
- Keep source provenance for every external identifier and relation.
- Filter all graph reads and writes by authenticated ownership.
- Merge nodes only when stable identity evidence supports the merge.
