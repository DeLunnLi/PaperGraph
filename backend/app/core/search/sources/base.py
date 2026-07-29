"""数据源统一契约 —— SearchSourceFn 签名 + registry。

PaperGraph 的数据源（arxiv/dblp/openalex/mcp/...）是函数式实现，
``paper_searcher._fetch_src`` 据 registry 分派。新数据源接入只需用
``@register_source("name")`` 装饰入口函数并注册，不再改分派逻辑；各源入口
签名一致性由 ``test_search_source_contract`` 校验。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from ...paper import Paper  # noqa: F401  (re-export for sources)


#: 数据源入口函数签名 —— 所有源必须满足。
#: ``searcher`` 是 PaperSearcher 实例（提供 _ensure_async_client / _rate_limit_async /
#: _async_get_with_retry / _make_paper）。``**kwargs`` 透传搜索约束（venue/year_from/...）。
SearchSourceFn = Callable[..., Awaitable[list[Paper]]]


# ── registry ────────────────────────────────────────────────────────

#: 注册表：源名 → 入口函数。``paper_searcher._fetch_src`` 据此分派。
_REGISTRY: dict[str, SearchSourceFn] = {}


def register_source(name: str, fn: SearchSourceFn | None = None) -> Any:
    """注册一个数据源入口函数。

    支持两种用法::

        @register_source("arxiv")           # 带参装饰器（推荐）
        async def search_arxiv(...): ...

        register_source("arxiv", search_arxiv)  # 直接调用
    """
    if not name or not name.strip():
        raise ValueError("source name must be non-empty")
    key = name.strip().lower()

    def _do(f: SearchSourceFn) -> SearchSourceFn:
        _REGISTRY[key] = f
        return f

    if fn is None:
        return _do
    return _do(fn)


def get_source(name: str) -> SearchSourceFn | None:
    """按名取源；未注册返回 None（调用方应回退到空列表）。"""
    return _REGISTRY.get((name or "").strip().lower())


def list_sources() -> list[str]:
    """已注册的源名列表（有序、稳定）。"""
    return sorted(_REGISTRY)


# ── circuit breaker factory ─────────────────────────────────────────


def _new_circuit_breaker() -> Any:
    """按 settings 构造一个 hello-agents CircuitBreaker（per-PaperSearcher-singleton）。

    阈值/恢复时间从 settings 读，便于生产调参。返回类型用 Any 避免顶层
    import hello_agents 造成的循环依赖（CircuitBreaker 仅在调用处用）。
    """
    from hello_agents.tools.circuit_breaker import CircuitBreaker
    from ....settings import get_settings

    s = get_settings()
    return CircuitBreaker(
        failure_threshold=int(getattr(s, "papergraph_search_circuit_failure_threshold", 3) or 3),
        recovery_timeout=int(getattr(s, "papergraph_search_circuit_recovery_timeout_sec", 300) or 300),
        enabled=True,
    )
