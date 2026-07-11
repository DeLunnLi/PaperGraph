"""薄 LLM 客户端 —— 直接基于 openai SDK，替代 hello_agents.HelloAgentsLLM。

设计目标：
- 统一 ``chat()`` → ``ChatResult``，消除 ``invoke`` 返回 LLMResponse 与
  ``invoke_with_tools`` 返回原生响应导致的输出形状不一致（原 ``coerce_*`` 的根因）。
- provider quirk 收敛到构造期 + ``_extra`` 一处，不再 monkey-patch 框架类：
    (a) 角色归一化：``summary`` → user、``developer`` → system（OpenAI 兼容端点不认这两个 role）。
    (b) DeepSeek thinking 禁用：base_url 含 deepseek 时注入 ``extra_body={"thinking":{"type":"disabled"}}``。
- 原生 async（``achat`` / ``achat_with_tools``），不再依赖 ``run_in_executor``。
- 兼容旧接口 ``invoke``（返回 str）/ ``invoke_with_tools``（返回原生 OpenAI 响应），
  让现有 8 处 ``llm.invoke([...])`` 与 ``SimpleAgent`` 在阶段 1 零断裂地继续工作。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterator, AsyncIterator, List, Dict, Union, Optional

from openai import OpenAI, AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass
class ChatResult:
    """统一 LLM 响应。``content`` 为最终文本，``raw`` 保留原生响应以备调试。"""
    content: str
    usage: Dict[str, Any] = field(default_factory=dict)
    raw: Any = None
    latency_ms: int = 0
    reasoning_content: Optional[str] = None


class LLMClient:
    """OpenAI 兼容 LLM 客户端（DeepSeek / aihubmix / Qwen / 智谱 / Ollama 等）。

    新接口（推荐）：
        - ``chat(messages, **kw) -> ChatResult``
        - ``achat(messages, **kw) -> ChatResult``  （原生 async）
        - ``chat_with_tools(messages, tools, **kw) -> 原生响应``

    兼容旧接口（阶段 1 过渡，供 SimpleAgent 与 8 处直接调用）：
        - ``invoke(messages, **kw) -> str``            （= chat().content）
        - ``invoke_with_tools(messages, tools, **kw) -> 原生响应``
    """

    def __init__(
        self,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 60,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        if not model:
            raise ValueError("LLMClient: 必须提供 model")
        if not api_key:
            raise ValueError("LLMClient: 必须提供 api_key")
        if not base_url:
            raise ValueError("LLMClient: 必须提供 base_url")

        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._aclient = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

        # quirk (b): DeepSeek 禁用 thinking —— 原来靠 monkey-patch，现在构造时判定一次。
        self._disable_thinking = "deepseek" in (base_url or "").lower()
        # provider 仅用于日志展示
        self.provider = self._detect_provider(base_url)

    @staticmethod
    def _detect_provider(base_url: str) -> str:
        u = (base_url or "").lower()
        if "deepseek" in u:
            return "deepseek"
        if "aihubmix" in u:
            return "aihubmix"
        if "dashscope" in u or "qwen" in u:
            return "qwen"
        if "bigmodel" in u or "zhipu" in u:
            return "zhipu"
        if "moonshot" in u or "kimi" in u:
            return "kimi"
        if "ollama" in u:
            return "ollama"
        return "openai"

    # ------------------------------------------------------------------
    # quirk 处理
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_messages(messages: Any) -> List[Dict[str, Any]]:
        """quirk (a): OpenAI 兼容端点不认 ``summary`` / ``developer`` role，归一化。"""
        if not isinstance(messages, list):
            return messages
        out: List[Dict[str, Any]] = []
        for m in messages:
            if not isinstance(m, dict):
                out.append(m)
                continue
            role = str(m.get("role") or "").strip().lower()
            content = m.get("content")
            if role == "summary":
                text = content if isinstance(content, str) else ("" if content is None else str(content))
                out.append({"role": "user", "content": ("[前文摘要]\n" + text).strip()})
                continue
            if role == "developer":
                text = content if isinstance(content, str) else ("" if content is None else str(content))
                nm = dict(m)
                nm["role"] = "system"
                nm["content"] = text
                out.append(nm)
                continue
            out.append(m)
        return out

    def _merge_extra(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """quirk (b): DeepSeek 注入 thinking:disabled，合并而非覆盖调用方传入的 extra_body。"""
        if not self._disable_thinking:
            return kwargs
        kw = dict(kwargs)
        extra = dict(kw.get("extra_body") or {})
        if "thinking" not in extra:
            extra["thinking"] = {"type": "disabled"}
        kw["extra_body"] = extra
        return kw

    def _build_call_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """合并 temperature / max_tokens 默认值 + quirk。"""
        kw = dict(kwargs)
        kw.setdefault("temperature", self.temperature)
        if self.max_tokens is not None and "max_tokens" not in kw:
            kw["max_tokens"] = self.max_tokens
        return self._merge_extra(kw)

    # ------------------------------------------------------------------
    # 新接口
    # ------------------------------------------------------------------
    def chat(self, messages: List[Dict[str, str]], **kwargs: Any) -> ChatResult:
        kw = self._build_call_kwargs(kwargs)
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            **kw,
        )
        return self._to_chat_result(resp)

    async def achat(self, messages: List[Dict[str, str]], **kwargs: Any) -> ChatResult:
        kw = self._build_call_kwargs(kwargs)
        resp = await self._aclient.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            **kw,
        )
        return self._to_chat_result(resp)

    def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: Union[str, Dict[str, Any]] = "auto",
        **kwargs: Any,
    ) -> Any:
        """带工具调用，返回原生 OpenAI ChatCompletion（含 choices[0].message.tool_calls）。"""
        kw = self._build_call_kwargs(kwargs)
        kw["tool_choice"] = tool_choice
        return self._client.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            tools=tools,
            **kw,
        )

    async def achat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: Union[str, Dict[str, Any]] = "auto",
        **kwargs: Any,
    ) -> Any:
        kw = self._build_call_kwargs(kwargs)
        kw["tool_choice"] = tool_choice
        return await self._aclient.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            tools=tools,
            **kw,
        )

    # ------------------------------------------------------------------
    # 兼容旧接口（阶段 1 过渡）
    # ------------------------------------------------------------------
    def invoke(self, messages: List[Dict[str, str]], **kwargs: Any) -> str:
        """兼容 HelloAgentsLLM.invoke —— 返回 str（= chat().content）。

        SimpleAgent 与 8 处直接调用都依赖返回值可直接当字符串使用
        （memory_store 用 ``str(summary or "")``，其余过 coerce）。
        """
        return self.chat(messages, **kwargs).content

    def invoke_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: Union[str, Dict[str, Any]] = "auto",
        **kwargs: Any,
    ) -> Any:
        """兼容 HelloAgentsLLM.invoke_with_tools —— 返回原生 OpenAI 响应。

        支持关键字调用（SimpleAgent: ``invoke_with_tools(messages=, tools=, tool_choice=)``）。
        """
        return self.chat_with_tools(messages, tools, tool_choice=tool_choice, **kwargs)

    async def ainvoke(self, messages: List[Dict[str, str]], **kwargs: Any) -> ChatResult:
        """兼容 HelloAgentsLLM.ainvoke —— 返回 ChatResult（而非 str，便于取 usage）。"""
        return await self.achat(messages, **kwargs)

    # ------------------------------------------------------------------
    # 流式（保留与 HelloAgentsLLM 同名方法，未被项目使用，提供以备未来）
    # ------------------------------------------------------------------
    def stream_invoke(self, messages: List[Dict[str, str]], **kwargs: Any) -> Iterator[str]:
        kw = self._build_call_kwargs(kwargs)
        for chunk in self._client.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            stream=True,
            **kw,
        ):
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    async def astream_invoke(self, messages: List[Dict[str, str]], **kwargs: Any) -> AsyncIterator[str]:
        kw = self._build_call_kwargs(kwargs)
        async for chunk in await self._aclient.chat.completions.create(
            model=self.model,
            messages=self._normalize_messages(messages),
            stream=True,
            **kw,
        ):
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta

    # ------------------------------------------------------------------
    @staticmethod
    def _to_chat_result(resp: Any) -> ChatResult:
        choice = resp.choices[0]
        msg = choice.message
        content = getattr(msg, "content", None) or ""
        reasoning = getattr(msg, "reasoning_content", None)
        usage: Dict[str, Any] = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
                "total_tokens": getattr(u, "total_tokens", 0) or 0,
            }
        return ChatResult(content=content, usage=usage, raw=resp, reasoning_content=reasoning)
