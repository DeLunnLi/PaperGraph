"""Deadlines must return promptly without spawning unlimited abandoned work."""

from __future__ import annotations

from types import SimpleNamespace
import threading
import time

import pytest

from app.services.llm import agent_runtime
from app.services.llm.call_budget import llm_deadline
from app.services.llm.llm_service import HelloAgentsLLM


@pytest.fixture
def executor(monkeypatch):
    runner = agent_runtime._TaskExecutor(max_workers=1)
    monkeypatch.setattr(agent_runtime, "_get_task_executor", lambda: runner)
    yield runner
    runner.pool.shutdown(wait=True, cancel_futures=True)


def test_timeout_returns_before_running_work_finishes(executor):
    release = threading.Event()
    started = threading.Event()

    def blocked():
        started.set()
        release.wait(2)

    begin = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            agent_runtime._run_with_optional_timeout(blocked, 0.03)
        assert started.is_set()
        assert time.monotonic() - begin < 0.5
        assert not release.is_set()
    finally:
        release.set()


def test_timed_out_work_keeps_capacity_until_it_finishes(executor):
    release = threading.Event()
    unexpected_call = threading.Event()
    try:
        with pytest.raises(TimeoutError):
            agent_runtime._run_with_optional_timeout(lambda: release.wait(2), 0.03)
        with pytest.raises(TimeoutError, match="capacity"):
            agent_runtime._run_with_optional_timeout(unexpected_call.set, 0.03)
        assert not unexpected_call.is_set()
    finally:
        release.set()


def test_success_and_task_errors_are_preserved(executor):
    assert agent_runtime._run_with_optional_timeout(lambda: 42, 1) == 42

    def fail():
        raise ValueError("invalid result")

    with pytest.raises(ValueError, match="invalid result"):
        agent_runtime._run_with_optional_timeout(fail, 1)


def test_agent_does_not_retry_an_inflight_timeout(executor, monkeypatch):
    release = threading.Event()
    calls = []

    class Agent:
        def __init__(self, **kwargs):
            pass

        def run(self, prompt):
            calls.append(prompt)
            release.wait(2)
            return "late result"

    monkeypatch.setattr(agent_runtime, "SimpleAgent", Agent)
    monkeypatch.setattr(agent_runtime, "papergraph_agent_config", lambda **kwargs: None)
    try:
        with pytest.raises(agent_runtime.LLMError):
            agent_runtime.run_agent_task(
                task_name="deadline_test", agent_name="test", llm=object(),
                system_prompt="", user_prompt="question", timeout_sec=0.03, retries=3,
            )
        assert calls == ["question"]
    finally:
        release.set()


def test_worker_propagates_deadline_to_http_timeout(executor):
    requests = []

    def invoke(self, messages, **kwargs):
        requests.append(kwargs)
        return SimpleNamespace(content="ok")

    adapter_type = type("OpenAIAdapter", (), {"invoke": invoke})
    llm = HelloAgentsLLM.__new__(HelloAgentsLLM)
    llm._adapter = adapter_type()
    llm.temperature = 0
    llm.max_tokens = None
    llm.timeout = 60
    llm.model = "test"

    result = agent_runtime._run_with_optional_timeout(
        lambda: llm.invoke([{"role": "user", "content": "test"}]), 1,
    )
    assert result.content == "ok"
    assert 0 < requests[0]["timeout"] <= 1
    assert llm.timeout == 60
    assert llm_deadline.get() is None


def test_expired_deadline_prevents_another_http_request():
    adapter_type = type("OpenAIAdapter", (), {"invoke": lambda *args, **kwargs: pytest.fail("HTTP request started")})
    llm = HelloAgentsLLM.__new__(HelloAgentsLLM)
    llm._adapter = adapter_type()
    token = llm_deadline.set(time.monotonic() - 1)
    try:
        with pytest.raises(TimeoutError, match="deadline"):
            llm.invoke([])
    finally:
        llm_deadline.reset(token)
