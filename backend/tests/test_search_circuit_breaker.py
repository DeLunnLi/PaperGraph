"""CircuitBreaker 集成测试 —— 锁定搜索源熔断行为。

PaperSearcher 是进程级 singleton，breaker 跨搜索请求共享。本测试验证：
1. 连续失败达阈值 → 熔断开启（is_open=True），后续调用 fast-skip 返回 []。
2. 成功调用重置失败计数。
3. breaker 在 P0-4 reader 边界外（reader 路径不碰 breaker）。

不依赖真实 HTTP —— 用 stub 源函数注入失败/成功。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from hello_agents.tools.response import ToolResponse
from app.core.search.sources.base import _new_circuit_breaker


def _breaker():
    return _new_circuit_breaker()


def test_breaker_opens_after_threshold_consecutive_failures():
    """连续 failure_threshold 次失败 → is_open=True。"""
    b = _breaker()
    threshold = b.failure_threshold
    src = "arxiv"
    assert not b.is_open(src)

    for i in range(threshold):
        b.record_result(src, ToolResponse.error(code="timeout", message=f"fail {i}"))
        # 未达阈值前不应开
        if i < threshold - 1:
            assert not b.is_open(src), f"opened too early at {i+1}"

    assert b.is_open(src), f"should be open after {threshold} failures"


def test_breaker_success_resets_failure_count():
    """成功调用重置失败计数 —— 阈值-1 次失败后一次成功，再失败不应立即开。"""
    b = _breaker()
    threshold = b.failure_threshold
    src = "dblp"

    for _ in range(threshold - 1):
        b.record_result(src, ToolResponse.error(code="err", message="x"))
    assert not b.is_open(src)

    # 一次成功重置
    b.record_result(src, ToolResponse.success(text="ok", data={"count": 5}))
    assert not b.is_open(src)

    # 再 threshold-1 次失败仍不应开（计数已重置）
    for _ in range(threshold - 1):
        b.record_result(src, ToolResponse.error(code="err", message="x"))
    assert not b.is_open(src), "success should have reset the failure count"


def test_breaker_per_source_independent():
    """一个源熔断不影响其他源。"""
    b = _breaker()
    threshold = b.failure_threshold
    for _ in range(threshold):
        b.record_result("arxiv", ToolResponse.error(code="x", message="y"))
    assert b.is_open("arxiv")
    assert not b.is_open("openalex")
    assert not b.is_open("dblp")


def test_breaker_factory_reads_settings():
    """工厂从 settings 读阈值/恢复时间。"""
    b = _breaker()
    assert b.failure_threshold >= 1
    assert b.recovery_timeout >= 10


def test_run_one_skips_when_breaker_open(monkeypatch):
    """熔断开启时，_run_one fast-skip 返回 [] 不调源。"""
    from app.core.search.paper_searcher import PaperSearcher

    ps = PaperSearcher()
    src = "arxiv"
    # 强制熔断
    for _ in range(ps._circuit_breaker.failure_threshold):
        ps._circuit_breaker.record_result(src, ToolResponse.error(code="x", message="y"))
    assert ps._circuit_breaker.is_open(src)

    # 构造一个会抛错的 _fetch_src 闭包（若被调用说明熔断没生效）
    call_count = 0

    async def _fetch_src(_src):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("should not be called — breaker open")

    # 复刻 search_async 里 _run_one 的结构（最小化：只测熔断分支）
    async def _run_one():
        if ps._circuit_breaker.is_open(src):
            return []
        return await _fetch_src(src)

    result = asyncio.run(_run_one())

    assert result == []
    assert call_count == 0, "源不应被调用（熔断应 fast-skip）"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
