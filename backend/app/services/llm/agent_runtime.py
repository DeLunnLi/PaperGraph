
from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, TypeVar
from collections.abc import Callable

from hello_agents import SimpleAgent

from app.exceptions import LLMError
from ...settings import get_settings
from .agent_config import apply_project_skills, papergraph_agent_config, resolve_task_skills

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

def _exception_chain_predicate(exc: BaseException | None, pred) -> bool:
    seen: set[int] = set()
    depth = 0
    cur: BaseException | None = exc
    while cur is not None and depth < 12:
        if id(cur) in seen:
            break
        seen.add(id(cur))
        try:
            if pred(cur):
                return True
        except Exception:
            pass
        nxt = cur.__cause__
        if nxt is None:
            nxt = getattr(cur, "__context__", None)
        cur = nxt
        depth += 1
    return False

def _task_failed_due_to_timeout(exc: BaseException) -> bool:
    return _exception_chain_predicate(exc, lambda e: (
        isinstance(e, TimeoutError)
        or "timeout" in str(e).lower()
        or "timed out" in str(e).lower()
    ))

def _run_with_optional_timeout(fn: Callable[[], _T], timeout_sec: float | None) -> _T:
    if timeout_sec is None or float(timeout_sec) <= 0:
        return fn()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(fn)
        try:
            return fut.result(timeout=float(timeout_sec))
        except concurrent.futures.TimeoutError as exc:
            fut.cancel()
            raise TimeoutError(f"agent task timeout after {timeout_sec}s") from exc

def stateless_llm_chat(
    llm: Any, system_prompt: str | None, user_prompt: str, **invoke_kwargs: Any
) -> str:
    """Single-turn stateless LLM call: build ``[system?, user]`` → ``invoke`` → ``.content``.

    This is the shared chokepoint for one-shot LLM calls that must stay
    request-isolated (no ``SimpleAgent`` history accumulation, see P0-4).
    ``invoke_kwargs`` (e.g. ``temperature``, ``max_tokens``) are forwarded to ``invoke``.
    """
    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})
    return llm.invoke(messages, **invoke_kwargs).content


def run_agent_task(
    *,
    task_name: str,
    agent_name: str,
    llm: Any,
    system_prompt: str,
    user_prompt: str,
    timeout_sec: float | None = None,
    retries: int | None = None,
    max_tokens: int | None = None,
    task_logger: logging.Logger | None = None,
) -> str:
    log = task_logger or logger
    s = get_settings()
    resolved_timeout = float(timeout_sec) if timeout_sec is not None else float(
        getattr(s, "agent_runtime_default_timeout_sec", 20.0)
    )
    resolved_retries = int(retries) if retries is not None else int(
        getattr(s, "agent_runtime_default_retries", 1)
    )
    attempts = max(1, resolved_retries + 1)
    selected_skills = resolve_task_skills(task_name)
    if selected_skills:
        log.debug("[%s] project skills: %s", task_name, ",".join(selected_skills))
    resolved_system_prompt = apply_project_skills(system_prompt, selected_skills)
    last_error: Exception | None = None

    for i in range(attempts):
        try:
            agent = SimpleAgent(
                name=agent_name,
                llm=llm,
                system_prompt=resolved_system_prompt,
                config=papergraph_agent_config(max_tokens=max_tokens),
            )
            raw = _run_with_optional_timeout(lambda: agent.run(user_prompt), resolved_timeout)
            return (raw or "").strip()
        except Exception as exc:
            last_error = exc
            if i + 1 < attempts:
                log.warning("[%s] attempt %d/%d failed: %s", task_name, i + 1, attempts, exc)
            else:
                if _task_failed_due_to_timeout(last_error):
                    log.warning("[%s] failed after %d attempt(s): %s", task_name, attempts, last_error)
                else:
                    log.exception("[%s] failed after %d attempt(s)", task_name, attempts)
    raise LLMError(f"{task_name}_failed", code=f"{task_name}_failed") from last_error

def run_json_task(
    *,
    task_name: str,
    agent_name: str,
    llm: Any,
    system_prompt: str,
    user_prompt: str,
    timeout_sec: float | None = None,
    retries: int | None = None,
    default: dict[str, Any | None] = None,
    parse_fn: Callable[[str | None], Any] | None = None,
    task_logger: logging.Logger | None = None,
) -> dict[str, Any]:
    log = task_logger or logger
    if parse_fn is None:
        from ..search_intent import extract_json_object

        parser = extract_json_object
    else:
        parser = parse_fn
    raw = run_agent_task(
        task_name=task_name,
        agent_name=agent_name,
        llm=llm,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        timeout_sec=timeout_sec,
        retries=retries,
        task_logger=log,
    )
    data = parser(raw)
    if isinstance(data, dict):
        return data
    log.warning("[%s] JSON parse failed, fallback to default", task_name)
    return dict(default or {})
