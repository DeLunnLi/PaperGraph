"""数据源统一契约 —— SearchSource Protocol + registry。

PaperGraph 的 5 个数据源（arxiv/dblp/openalex/mcp/tavily）原本是散落的函数式
实现，``paper_searcher._fetch_src`` 用 if/elif 分派。这里定义统一契约，让：

1. 新数据源（Semantic Scholar / PubMed / IEEE）接入只需实现 Protocol + 注册，
   不再改 ``_fetch_src`` 的分派逻辑。
2. 各源入口签名一致性可被静态/测试校验（``test_search_source_contract``）。
3. 超时/去重等横切配置有统一锚点。

设计选择：用 ``typing.Protocol``（结构化子类型）而非 ABC —— 源仍是函数式
``async def(searcher, query, max_results, **kwargs) -> list[Paper]``，无需改成类。
Protocol 在运行时零侵入，registry 用 adapter 包装现有函数。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from ...paper import Paper  # noqa: F401  (re-export for sources)


#: 数据源入口函数签名 —— 所有源必须满足。
#: ``searcher`` 是 PaperSearcher 实例（提供 _ensure_async_client / _rate_limit_async /
#: _async_get_with_retry / _make_paper）。``**kwargs`` 透传搜索约束（venue/year_from/...）。
SearchSourceFn = Callable[..., Awaitable[list[Paper]]]


@runtime_checkable
class SearchSource(Protocol):
    """数据源统一契约（结构化，源无需显式继承）。"""

    #: 源标识（小写，与 Paper.source 字段一致）：如 ``"arxiv"``/``"dblp"``。
    source_name: str

    async def search(
        self,
        searcher: Any,
        query: str,
        max_results: int = 10,
        **kwargs: Any,
    ) -> list[Paper]:
        """检索论文，返回去重前的候选列表。失败时应返回 ``[]`` 而非抛出（由调用方统一兜底）。"""
        ...


# ── registry ────────────────────────────────────────────────────────

#: 注册表：源名 → 入口函数。``paper_searcher._fetch_src`` 据此分派。
_REGISTRY: dict[str, SearchSourceFn] = {}

#: 各源默认超时（秒），``_fetch_src`` 用 ``_src_timeouts`` 覆盖。
DEFAULT_TIMEOUTS: dict[str, float] = {
    "arxiv": 30.0,
    "dblp": 32.0,
    "openalex": 45.0,
    "mcp": 45.0,
    "semantic_scholar": 10.0,
    "europe_pmc": 12.0,
    "crossref": 10.0,
}


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
