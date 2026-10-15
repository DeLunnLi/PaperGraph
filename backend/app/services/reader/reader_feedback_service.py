"""Reader-turn feedback attribution for the self-evolving memory system.

A feedback event (👍 / 👎 / citation click) is attributed to the memories that
were injected into that turn's prompt (``paper_reader_turns.metadata.memory_ids``).
The *size and direction* of each memory's utility change is decided by the
LLM judge (``judge_feedback_deltas``) — no fixed increments. When the judge
is unavailable the raw event is still written to the evolution log (delta 0)
so the knowledge-base timeline stays complete.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from .paper_reader_history import get_turn

logger = logging.getLogger(__name__)

_VALID_SIGNALS = {"up", "down", "citation_click"}


def _apply_deltas(store: Any, docs: list[dict[str, Any]], deltas: dict[str, float], *, event: str, reason: str) -> int:
    """Apply judge-decided utility deltas to memory docs; log-only when delta is 0."""
    affected = 0
    for d in docs:
        mid = str(d.get("memory_id") or "")
        if not mid:
            continue
        delta = float(deltas.get(mid, 0.0))
        if delta:
            ok = store.adjust_utility(mid, delta=delta, event=event, reason=reason)
        else:
            # Judge unavailable or judged neutral — still record the raw signal.
            store.store.log_evolution(
                mid, event,
                utility_after=float(d.get("utility") or 0.0),
                importance_after=float(d.get("importance") or 0.5),
                access_count_after=int(d.get("access_count") or 0),
                reason=reason + " (no adjustment)",
            )
            ok = True
        if ok:
            affected += 1
    return affected


def record_reader_feedback(*, db_path: str, user_id: int, paper_id: int, turn_id: int, signal: str) -> dict[str, Any]:
    from ..memory.memory_store import MemoryStore

    sig = (signal or "").strip().lower()
    if sig not in _VALID_SIGNALS:
        return {"success": False, "affected_memory_count": 0, "detail": f"unknown signal: {signal}"}

    turn = get_turn(db_path, turn_id=int(turn_id), user_id=int(user_id))
    if not turn or turn["role"] != "assistant" or int(turn["paper_id"]) != int(paper_id):
        return {"success": False, "affected_memory_count": 0, "detail": "turn not found"}

    try:
        meta = json.loads(turn["metadata"] or "{}")
    except (TypeError, json.JSONDecodeError):
        meta = {}
    memory_ids = [str(m) for m in (meta.get("memory_ids") or []) if m]
    if not memory_ids:
        return {"success": True, "affected_memory_count": 0}

    store = MemoryStore(db_path)
    docs = store.store.get_memories_by_ids(memory_ids)
    if not docs:
        return {"success": True, "affected_memory_count": 0}

    from ..memory.memory_judge import judge_feedback_deltas

    deltas = judge_feedback_deltas(
        signal=sig,
        memories=docs,
        assistant_excerpt=turn["content"],
    )
    event = {
        "up": "feedback_up", "down": "feedback_down",
        "citation_click": "citation_click",
    }[sig]
    affected = _apply_deltas(store, docs, deltas, event=event, reason=f"feedback:{sig} turn={int(turn_id)}")
    return {"success": True, "affected_memory_count": affected}


def record_engaged_read(*, db_path: str, paper_id: int, user_id: int, duration_sec: int) -> dict[str, Any]:
    """Implicit engagement signal: a long reading session credits the memories
    that were injected during that session window.

    Attribution window = [now - duration - grace, now] over ``last_used``;
    the judge decides each memory's utility delta (log-only when unavailable).
    """
    from ...settings import get_settings
    from ..memory.memory_store import MemoryStore

    min_sec = int(get_settings().memory_engaged_read_min_sec)
    if int(duration_sec) < min_sec:
        return {"success": True, "affected_memory_count": 0}

    store = MemoryStore(db_path)
    now = time.time()
    from ..memory.policy import get_memory_policy

    grace = int(get_memory_policy().engaged_read_grace_sec)
    window_start = now - int(duration_sec) - grace  # grace: chat may precede the timer
    docs = [
        d for d in store._list_recent_docs(
            scope="paper", paper_id=int(paper_id),
            kinds=["working", "short", "long", "paper_summary"], limit_per_kind=200,
        )
        if float(d.get("last_used") or 0) >= window_start
    ]
    if not docs:
        return {"success": True, "affected_memory_count": 0}

    from ..memory.memory_judge import judge_feedback_deltas

    deltas = judge_feedback_deltas(signal="engaged_read", memories=docs)
    affected = _apply_deltas(
        store, docs, deltas, event="engaged_read",
        reason=f"engaged_read paper={int(paper_id)} duration={int(duration_sec)}s user={int(user_id)}",
    )
    return {"success": True, "affected_memory_count": affected}
