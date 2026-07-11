"""阶段 1 探针：验证 LLMClient 的 quirk 透传与兼容接口。

确定性部分（mock openai client）：
  1. DeepSeek base_url → chat/invoke_with_tools 调用时 extra_body 含 thinking:disabled
  2. OpenAI base_url → 不注入 extra_body
  3. summary/developer 角色被归一化
  4. invoke 返回 str（兼容 8 处直接调用），chat 返回 ChatResult

端到端部分（需 LLM_API_KEY）：
  5. 真实 chat + chat_with_tools 各一次，不抛异常
"""
from __future__ import annotations
import json
from unittest.mock import MagicMock, patch

from app.services.llm.client import LLMClient, ChatResult


def _fake_openai_response(*, content="ok", tool_calls=None):
    """构造一个最小可用的 openai ChatCompletion-like 对象。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.reasoning_content = None
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def test_deepseek_thinking_disabled_on_chat():
    c = LLMClient(model="deepseek-v4-flash", api_key="sk-fake", base_url="https://api.deepseek.com/v1")
    fake = _fake_openai_response(content="hi")
    with patch.object(c._client.chat.completions, "create", return_value=fake) as m:
        c.chat([{"role": "user", "content": "ping"}], temperature=0.1, max_tokens=30)
    _, kw = m.call_args
    assert kw.get("extra_body") == {"thinking": {"type": "disabled"}}, kw.get("extra_body")
    assert kw["temperature"] == 0.1 and kw["max_tokens"] == 30
    print("  [PASS] deepseek chat 注入 thinking:disabled")


def test_deepseek_thinking_disabled_on_invoke_with_tools():
    c = LLMClient(model="deepseek-v4-flash", api_key="sk-fake", base_url="https://api.deepseek.com/v1")
    fake = _fake_openai_response(content="answer", tool_calls=None)
    with patch.object(c._client.chat.completions, "create", return_value=fake) as m:
        c.invoke_with_tools(
            messages=[{"role": "user", "content": "q"}],
            tools=[{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}],
            tool_choice="auto",
        )
    _, kw = m.call_args
    assert kw.get("extra_body") == {"thinking": {"type": "disabled"}}, kw.get("extra_body")
    assert kw["tool_choice"] == "auto"
    assert "tools" in kw
    print("  [PASS] deepseek invoke_with_tools 注入 thinking:disabled + 透传 tools/tool_choice")


def test_openai_no_thinking_injection():
    c = LLMClient(model="gpt-4o", api_key="sk-fake", base_url="https://api.openai.com/v1")
    fake = _fake_openai_response(content="hi")
    with patch.object(c._client.chat.completions, "create", return_value=fake) as m:
        c.chat([{"role": "user", "content": "ping"}])
    _, kw = m.call_args
    assert "extra_body" not in kw or kw.get("extra_body") is None, kw
    print("  [PASS] openai 不注入 extra_body")


def test_role_normalization_passthrough():
    c = LLMClient(model="gpt-4o", api_key="sk-fake", base_url="https://api.openai.com/v1")
    fake = _fake_openai_response(content="hi")
    with patch.object(c._client.chat.completions, "create", return_value=fake) as m:
        c.chat([
            {"role": "summary", "content": "前文"},
            {"role": "developer", "content": "指令"},
            {"role": "user", "content": "q"},
        ])
    _, kw = m.call_args
    roles = [m["role"] for m in kw["messages"]]
    assert roles == ["user", "system", "user"], roles
    print("  [PASS] summary→user, developer→system 归一化透传")


def test_invoke_returns_str_chat_returns_chatresult():
    c = LLMClient(model="gpt-4o", api_key="sk-fake", base_url="https://api.openai.com/v1")
    fake = _fake_openai_response(content="hello world")
    with patch.object(c._client.chat.completions, "create", return_value=fake):
        s = c.invoke([{"role": "user", "content": "q"}])
        r = c.chat([{"role": "user", "content": "q"}])
    assert isinstance(s, str) and s == "hello world", repr(s)
    assert isinstance(r, ChatResult) and r.content == "hello world"
    # memory_store.py:364 的用法：str(summary or "").strip()
    assert str(s or "").strip() == "hello world"
    print("  [PASS] invoke→str, chat→ChatResult, 兼容 str(summary or '').strip()")


def test_real_end_to_end_if_configured():
    try:
        from app.services.llm.llm_service import is_llm_configured, get_llm
    except Exception as e:
        print(f"  [SKIP] 真实调用：llm_service import 失败 {e}")
        return
    if not is_llm_configured():
        print("  [SKIP] 真实调用：LLM_API_KEY 未配置")
        return
    llm = get_llm()
    # 1) chat
    r = llm.chat([{"role": "user", "content": "只回复两个字：你好"}], temperature=0.0, max_tokens=20)
    assert isinstance(r, ChatResult) and r.content, f"chat 空: {r}"
    print(f"  [PASS] 真实 chat: provider={llm.provider} content={r.content!r}")
    # 2) chat_with_tools（带一个占位工具，让模型可能不调用直接答）
    tools = [{
        "type": "function",
        "function": {
            "name": "echo",
            "description": "原样返回输入",
            "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
        },
    }]
    resp = llm.chat_with_tools(
        [{"role": "user", "content": "直接回复两个字：你好，不要调用工具"}],
        tools=tools, tool_choice="auto", temperature=0.0, max_tokens=20,
    )
    msg = resp.choices[0].message
    print(f"  [PASS] 真实 chat_with_tools: content={msg.content!r} tool_calls={bool(msg.tool_calls)}")
    # 3) invoke 兼容
    s = llm.invoke([{"role": "user", "content": "只回复两个字：你好"}], temperature=0.0, max_tokens=20)
    assert isinstance(s, str) and s, f"invoke 空: {s!r}"
    print(f"  [PASS] 真实 invoke→str: {s!r}")


if __name__ == "__main__":
    print("=== LLMClient quirk 探针（mock 部分）===")
    test_deepseek_thinking_disabled_on_chat()
    test_deepseek_thinking_disabled_on_invoke_with_tools()
    test_openai_no_thinking_injection()
    test_role_normalization_passthrough()
    test_invoke_returns_str_chat_returns_chatresult()
    print("\n=== LLMClient quirk 探针（真实端到端）===")
    test_real_end_to_end_if_configured()
    print("\n✅ 全部通过")
