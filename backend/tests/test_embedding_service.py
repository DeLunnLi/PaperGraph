from __future__ import annotations

from types import SimpleNamespace

from app.services.embedding import embedding_service
from app.services.retrieval import semantic_scoring


def test_cosine_similarity_handles_identity_and_dimension_mismatch():
    assert embedding_service.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert embedding_service.cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_semantic_ranking_uses_embedding_signal(monkeypatch):
    papers = [
        SimpleNamespace(title="Protein folding", abstract="Structure prediction", keywords=[]),
        SimpleNamespace(title="Dense retrieval", abstract="Retrieve evidence for generation", keywords=[]),
    ]
    monkeypatch.setattr(semantic_scoring, "embedding_enabled", lambda: True)
    monkeypatch.setattr(
        semantic_scoring,
        "embed_texts",
        lambda texts: [[1.0, 0.0], [0.0, 1.0], [0.95, 0.05]],
    )

    ranked = semantic_scoring.rank_by_semantic_relevance(papers, "检索增强生成")

    assert ranked[0][0] is papers[1]
    assert ranked[0][1] > ranked[1][1]


def test_semantic_ranking_uses_citations_only_as_topic_gated_tiebreaker(monkeypatch):
    papers = [
        SimpleNamespace(
            title="Retrieval Augmented Generation for Large Language Models: A Survey",
            abstract="retrieval augmented generation",
            keywords=[],
            citations=680,
        ),
        SimpleNamespace(
            title="A niche retrieval augmented generation application",
            abstract="retrieval augmented generation",
            keywords=[],
            citations=2,
        ),
        SimpleNamespace(
            title="Gradient-based learning applied to document recognition",
            abstract="convolutional networks",
            keywords=[],
            citations=58000,
        ),
    ]
    monkeypatch.setattr(semantic_scoring, "embedding_enabled", lambda: False)

    ranked = semantic_scoring.rank_by_semantic_relevance(
        papers,
        "retrieval augmented generation",
        use_embeddings=False,
    )

    assert ranked[0][0] is papers[0]
    assert ranked[-1][0] is papers[2]


def test_semantic_ranking_falls_back_when_embedding_fails(monkeypatch):
    papers = [
        SimpleNamespace(title="Unrelated", abstract="Protein folding", keywords=[]),
        SimpleNamespace(title="Retrieval augmented generation", abstract="Dense retrieval", keywords=[]),
    ]
    monkeypatch.setattr(semantic_scoring, "embedding_enabled", lambda: True)

    def fail(_texts):
        raise TimeoutError("upstream timeout")

    monkeypatch.setattr(semantic_scoring, "embed_texts", fail)
    ranked = semantic_scoring.rank_by_semantic_relevance(papers, "retrieval augmented generation")

    assert ranked[0][0] is papers[1]
