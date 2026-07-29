"""PaperGraph 领域异常体系。

替代散落各处的 `raise RuntimeError("xxx_failed")` 字符串码 —— 用类型化的异常
让调用方能按语义 catch，并保留 `__cause__` 链便于诊断。

设计原则：
- 所有领域异常继承 ``PaperGraphError``，调用方可 `except PaperGraphError` 兜底。
- 每个异常带 ``code`` 属性（机器可读的稳定标识，用于日志/监控/前端错误码），
  保留 ``message`` 给人读。原 RuntimeError 字符串码迁移到 ``code``。
- 不破坏现有行为：``str(exc)`` 仍返回可读消息，``__cause__`` 透传。
"""

from __future__ import annotations

from typing import Optional


class PaperGraphError(Exception):
    """所有 PaperGraph 领域异常的基类。"""

    #: 机器可读的稳定错误码（如 ``"paper_analysis_llm_failed"``）。
    code: str = "papergraph_error"

    def __init__(self, message: str = "", *, code: Optional[str] = None) -> None:
        self.message = message or self.code
        if code:
            self.code = code
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


# ── LLM / Agent 层 ──────────────────────────────────────────────────

class LLMError(PaperGraphError):
    """LLM 调用或 agent 执行失败（初始化、调用、解析）。"""

    code = "llm_error"


class ConfigError(PaperGraphError):
    """配置缺失或非法（如 LLM_API_KEY 未配置）。"""

    code = "config_error"


class IntentParseError(LLMError):
    """搜索意图解析失败（LLM 返回非法 JSON / 空查询 / 重试耗尽）。"""

    code = "intent_parse_failed"


# ── 数据源 / 检索层 ─────────────────────────────────────────────────

class SearchSourceError(PaperGraphError):
    """某个检索数据源（arXiv/DBLP/OpenAlex/Tavily/MCP）调用失败。"""

    code = "search_source_error"

    def __init__(self, message: str = "", *, source: Optional[str] = None,
                 code: Optional[str] = None) -> None:
        self.source = source
        if source and message:
            message = f"[{source}] {message}"
        super().__init__(message, code=code)
