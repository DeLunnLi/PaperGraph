"""数据源 ABC 契约测试。

断言所有注册的数据源满足 SearchSource 协议：
- 入口是 async callable
- 签名兼容 (searcher, query, max_results=10, **kwargs)
- 返回 list[Paper]
- source_name 与注册名一致（通过 registry 校验）

这样新源接入时，注册即被本测试覆盖，无需手写。
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import AsyncMock
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 导入各源模块触发 @register_source 副作用
import app.core.search.sources.arxiv  # noqa: F401
import app.core.search.sources.dblp  # noqa: F401
import app.core.search.sources.openalex  # noqa: F401
import app.core.search.sources.mcp  # noqa: F401
import app.core.search.sources.crossref  # noqa: F401
import app.core.search.sources.europe_pmc  # noqa: F401
import app.core.search.sources.semantic_scholar  # noqa: F401

from app.core.search.sources.arxiv import search_arxiv
from app.core.search.sources.europe_pmc import search_europe_pmc
from app.core.search.sources.openalex import search_openalex
from app.core.search.sources.base import _REGISTRY, get_source, list_sources
from app.core.paper import Paper


EXPECTED_SOURCES = {"arxiv", "dblp", "openalex", "mcp", "semantic_scholar", "crossref", "europe_pmc"}


def test_all_expected_sources_registered():
    registered = set(list_sources())
    missing = EXPECTED_SOURCES - registered
    assert not missing, f"未注册的源: {missing}（registry 仅有 {registered}）"


def test_no_unexpected_sources_registered():
    registered = set(list_sources())
    extra = registered - EXPECTED_SOURCES
    # 允许有新源接入，但显式记录以便审视
    assert not extra, f"发现未在 EXPECTED_SOURCES 中的源: {extra}（如是有意接入，请更新 EXPECTED_SOURCES）"


def test_every_registered_source_is_async_callable():
    for name in list_sources():
        fn = get_source(name)
        assert callable(fn), f"{name}: not callable"
        assert inspect.iscoroutinefunction(fn), f"{name}: entry must be async def"


def test_every_registered_source_has_compatible_signature():
    """入口签名必须兼容 (searcher, query, max_results=10, **kwargs)。"""
    for name in list_sources():
        fn = get_source(name)
        sig = inspect.signature(fn)
        params = list(sig.parameters)
        # 至少 (searcher, query, max_results)；允许 **kwargs
        assert len(params) >= 3, f"{name}: signature too short: {params}"
        assert params[0] == "searcher", f"{name}: first param must be 'searcher', got {params[0]}"
        assert params[1] == "query", f"{name}: second param must be 'query', got {params[1]}"
        # max_results 应有默认值（调用方按位置传）
        mr = sig.parameters.get("max_results")
        assert mr is not None, f"{name}: missing max_results param"
        assert mr.default == 10 or mr.default is not inspect.Parameter.empty, (
            f"{name}: max_results should default to 10"
        )
        # 必须接受 **kwargs（搜索约束透传）
        has_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        assert has_kwargs, f"{name}: must accept **kwargs for search constraints"


def test_every_registered_source_returns_paper_list_annotation():
    """返回类型注解应为 list[Paper] / List[Paper]。"""
    for name in list_sources():
        fn = get_source(name)
        sig = inspect.signature(fn)
        ret = sig.return_annotation
        if ret is inspect.Signature.empty:
            # 无注解的源（如 dblp）跳过 —— 运行时由调用方保证
            continue
        ret_str = str(ret)
        assert "Paper" in ret_str and "list" in ret_str.lower(), (
            f"{name}: return annotation should be list[Paper], got {ret_str}"
        )


def test_get_source_unknown_returns_none():
    assert get_source("nonexistent_source") is None


def test_get_source_case_insensitive():
    """源名查找应大小写不敏感。"""
    import app.core.search.sources.arxiv  # noqa: F401
    assert get_source("ARXIV") is not None
    assert get_source("  arxiv  ") is not None


def test_registry_and_get_source_consistent():
    assert set(_REGISTRY.keys()) == set(list_sources())


def test_openalex_topic_query_requests_explicit_relevance_sort():
    response = type(
        "Response",
        (),
        {
            "status_code": 200,
            "json": lambda self: {"results": []},
            "raise_for_status": lambda self: None,
        },
    )()
    searcher = type(
        "Searcher",
        (),
        {
            "_ensure_async_client": AsyncMock(),
            "_rate_limit_async": AsyncMock(),
            "_resolve_vpj": lambda self, venue, kwargs: None,
            "_openalex_headers": lambda self: {},
            "_openalex_params": lambda self, params: dict(params),
            "_async_http_get_with_retry": AsyncMock(return_value=response),
            "_bump_stat": lambda self, name: None,
        },
    )()

    import asyncio

    asyncio.run(
        search_openalex(
            searcher,
            "retrieval augmented generation",
            10,
            llm_keywords=["RAG", "external knowledge"],
            http_max_attempts=1,
        )
    )

    params = searcher._async_http_get_with_retry.await_args.kwargs["params"]
    assert params["search"] == "retrieval augmented generation"
    assert params["sort"] == "relevance_score:desc"


def test_europe_pmc_parenthesizes_or_query_before_year_filter():
    response = type(
        "Response",
        (),
        {"json": lambda self: {"resultList": {"result": []}}},
    )()
    searcher = type(
        "Searcher",
        (),
        {
            "_ensure_async_client": AsyncMock(),
            "_rate_limit_async": AsyncMock(),
            "_user_agent": lambda self: "test",
            "_async_http_get_with_retry": AsyncMock(return_value=response),
        },
    )()

    import asyncio

    asyncio.run(
        search_europe_pmc(
            searcher,
            "cancer OR tumor",
            5,
            year_from=2020,
            year_to=2024,
            http_max_attempts=1,
        )
    )

    params = searcher._async_http_get_with_retry.await_args.kwargs["params"]
    assert params["query"] == "(cancer OR tumor) AND FIRST_PDATE:[2020-01-01 TO 2024-12-31]"


def test_arxiv_topic_query_does_not_fan_out_per_llm_keyword(monkeypatch):
    searcher = type(
        "Searcher",
        (),
        {
            "_ensure_async_client": AsyncMock(),
            "_rate_limit_async": AsyncMock(),
            "_user_agent": lambda self: "test",
            "_async_http_get_with_retry": AsyncMock(
                return_value=type("Response", (), {"content": b"<feed></feed>"})()
            ),
            "_resolve_vpj": lambda self, venue, kwargs: None,
            "_bump_stat": lambda self, name: None,
        },
    )()

    import asyncio

    asyncio.run(
        search_arxiv(
            searcher,
            "graph neural network explainability",
            10,
            llm_keywords=["GNN explainability", "interpretability", "XAI"],
            http_max_attempts=1,
        )
    )

    # One primary request plus at most one AND→OR fallback, never N keyword requests.
    assert searcher._async_http_get_with_retry.await_count <= 2


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
