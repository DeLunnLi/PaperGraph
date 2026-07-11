"""无状态 agent 循环 + 极简 Tool 协议。

替代 hello_agents.SimpleAgent 的工具调用循环。关键差异：
- **无状态**：history 由 caller 传入，不在实例上累积 → 天然并发安全，
  无需像 SimpleAgent 那样每请求新建实例。
- **caller 拥有 history**：调用方决定上下文如何拼接，循环只负责一轮工具编排。
- **ToolSpec 极简**：``fn`` + JSON schema，不继承框架 Tool 基类、无 circuit_breaker /
  expandable 等未用复杂度。

本阶段（阶段 1）独立存在、暂不被调用；阶段 5 起 paper_reader_reply 改用它。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    """函数式工具：``fn`` 接参数字典返回字符串，``parameters_schema`` 是 OpenAI JSON schema。

    与 hello_agents ``Tool`` 的关系：``Tool.to_openai_schema()`` 生成同形 schema，
    迁移期可由 ``Tool`` 子类的 ``build_tool_spec()`` 包装出 ToolSpec（见阶段 4）。
    """
    name: str
    description: str
    parameters_schema: Dict[str, Any]
    fn: Callable[[Dict[str, Any]], str]


async def run_agent_loop(
    *,
    llm: Any,
    system_prompt: str,
    history: List[Dict[str, str]],
    user_prompt: str,
    tools: Optional[List[ToolSpec]] = None,
    max_tool_iterations: int = 5,
    temperature: float = 0.3,
    **llm_kwargs: Any,
) -> str:
    """无状态 function-calling 循环。

    Args:
        llm: LLMClient（需提供 ``achat`` / ``achat_with_tools``）。
        system_prompt: 系统提示词。
        history: 对话历史（caller 拼好，每条 {"role","content"}）。循环不修改它。
        user_prompt: 本轮用户输入。
        tools: 可选工具列表；为空则单轮直答。
        max_tool_iterations: 最大工具调用轮数。
        temperature: 采样温度。
        **llm_kwargs: 透传给 llm 的额外参数（如 max_tokens）。
    """
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.extend(history)
    messages.append({"role": "user", "content": user_prompt})

    schemas = [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters_schema,
            },
        }
        for t in (tools or [])
    ]
    tool_map: Dict[str, Callable[[Dict[str, Any]], str]] = {t.name: t.fn for t in (tools or [])}

    if not schemas:
        result = await llm.achat(messages, temperature=temperature, **llm_kwargs)
        return result.content or ""

    last_content = ""
    for _ in range(max_tool_iterations):
        resp = await llm.achat_with_tools(
            messages, schemas, tool_choice="auto", temperature=temperature, **llm_kwargs
        )
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            last_content = getattr(msg, "content", None) or ""
            return last_content

        # 助手消息（含 tool_calls）原样追加，供下一轮模型看到调用上下文。
        messages.append({
            "role": "assistant",
            "content": getattr(msg, "content", None) or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_call_id = tc.id
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError as e:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"错误：参数格式不正确 - {e}",
                })
                continue
            fn = tool_map.get(tool_name)
            if fn is None:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": f"错误：未知工具 {tool_name}",
                })
                continue
            try:
                result = fn(args)
            except Exception as e:
                logger.exception("agent_loop_tool_failed: %s", tool_name)
                result = f"ERROR: 工具 {tool_name} 执行失败 - {e}"
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": result if isinstance(result, str) else str(result),
            })

    # 超过最大迭代轮数，做一次无工具收尾。
    final = await llm.achat(messages, temperature=temperature, **llm_kwargs)
    return final.content or last_content


def run_agent_loop_sync(**kwargs: Any) -> str:
    """同步入口：在无运行 event loop 时用 ``asyncio.run`` 驱动 ``run_agent_loop``。

    ``paper_reader_reply`` 跑在 FastAPI 的 ``run_in_threadpool`` 独立线程里（无 event loop），
    ``asyncio.run`` 安全。若意外在已有 loop 的上下文调用，退到临时线程池避免冲突。
    """
    try:
        asyncio.get_running_loop()
        running = True
    except RuntimeError:
        running = False

    if not running:
        return asyncio.run(run_agent_loop(**kwargs))

    coro: Awaitable[str] = run_agent_loop(**kwargs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()
