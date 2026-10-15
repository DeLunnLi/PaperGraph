"""Per-task deadlines without mutating the shared LLM client's settings."""

from __future__ import annotations

from contextvars import ContextVar
import time


llm_deadline: ContextVar[float | None] = ContextVar("papergraph_llm_deadline", default=None)


def remaining_call_budget() -> float | None:
    deadline = llm_deadline.get()
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("agent task deadline exceeded")
    return remaining
