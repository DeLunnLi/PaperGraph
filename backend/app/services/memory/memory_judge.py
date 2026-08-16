"""LLM judges for the self-evolving memory system.

Every memory *scoring/selection* decision is delegated to the model — there
are no hardcoded weighted formulas in the retrieval, feedback or pruning
paths. The model receives candidate memories together with their
instrumentation (importance / utility / access_count / last_used / age) and
returns structured decisions:

- :func:`judge_select_memories` — which candidates to inject into a prompt,
  in rank order (replaces a weighted scoring formula)
- :func:`judge_feedback_deltas` — per-memory utility delta given user feedback
  on a turn (replaces fixed ±0.5 increments)
- :func:`judge_evolution_batch` — periodic keep/merge/prune review of stale
  memories (replaces a hardcoded threshold prune)

All functions fail soft: they return ``None`` / ``{}`` / ``[]`` when the LLM
is unconfigured or errors, so callers degrade to recency order or log-only.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from ...settings import get_settings
from .policy import get_memory_policy

logger = logging.getLogger(__name__)

_UTILITY_MIN = get_memory_policy().utility_min
_UTILITY_MAX = get_memory_policy().utility_max


def _llm_or_none() -> Any | None:
    try:
        from ..llm.llm_service import get_llm, is_llm_configured

        if not is_llm_configured():
            return None
        return get_llm()
    except Exception:
        logger.debug("memory_judge: llm unavailable", exc_info=True)
        return None


def _judge_enabled() -> bool:
    try:
        return bool(get_settings().memory_llm_judge_enabled)
    except Exception:
        return True


def _chat_json(llm: Any, system: str, user: str, *, max_tokens: int = 600) -> dict[str, Any] | None:
    from ..search_intent import extract_json_object
    from ..llm.agent_runtime import stateless_llm_chat

    try:
        txt = stateless_llm_chat(llm, system, user, temperature=0.0, max_tokens=max_tokens)
        data = extract_json_object(txt or "")
        return data if isinstance(data, dict) else None
    except Exception:
        logger.debug("memory_judge chat_json failed", exc_info=True)
        return None


def _format_candidate(idx: int, doc: dict[str, Any], now: float) -> str:
    ts = float(doc.get("timestamp") or 0)
    age_h = max(0.0, (now - ts) / 3600)
    last_used = float(doc.get("last_used") or 0)
    last_used_h = f"{max(0.0, (now - last_used) / 3600):.1f}h前" if last_used else "从未"
    content = str(doc.get("content") or "").replace("\n", " ")[:180]
    return (
        f"[{idx}] id={doc.get('memory_id')} kind={doc.get('memory_type')} "
        f"importance={float(doc.get('importance') or 0.5):.2f} "
        f"utility={float(doc.get('utility') or 0.0):.2f} "
        f"access={int(doc.get('access_count') or 0)} 上次命中={last_used_h} "
        f"年龄={age_h:.1f}h\n内容: {content}"
    )


def _format_scorecard(scorecard: dict[str, Any] | None) -> str:
    """Render the judge's past-decision scorecard for prompt injection.

    Self-iteration: the model reads how its prior rescue/reflection/prune
    decisions turned out and self-calibrates. Counts shown alongside rates so
    small-N (single-user traffic) doesn't cause overreaction — an empty or
    tiny scorecard produces an empty string (no spurious calibration).
    """
    if not scorecard:
        return ""
    rescue = scorecard.get("rescue") or {}
    refl = scorecard.get("reflection") or {}
    prn = scorecard.get("prune_of_accessed") or {}
    lookback = int(scorecard.get("lookback_days") or 0)
    parts: list[str] = []
    if rescue.get("total"):
        parts.append(f"上浮决策 {rescue['total']} 次，其中 {rescue['hits']} 次后来被实际使用（{int(rescue['rate']*100)}%）")
    if refl.get("total"):
        parts.append(f"反思元记忆 {refl['total']} 条，其中 {refl['hits']} 条后来被使用（{int(refl['rate']*100)}%）")
    if prn.get("total"):
        parts.append(f"归档决策 {prn['total']} 次，其中 {prn['count']} 次归档了曾被访问过的记忆（{int(prn['rate']*100)}%，偏高说明可能过度删除）")
    if not parts:
        return ""
    summary = "；".join(parts)
    return f"【你过去{lookback}天的决策成绩单：{summary}。据此校准你的 rescue/prune 倾向，但样本小勿过度反应。】"


def judge_select_memories(
    *,
    candidates: list[dict[str, Any]],
    purpose: str,
    context_hint: str = "",
    max_items: int = 10,
) -> list[str] | None:
    """Model-judged selection: which candidate memories to inject, ranked.

    Returns a list of memory_ids (best first, at most ``max_items``), or
    ``None`` when the judge is unavailable — callers must fall back to their
    previous (recency) ordering.
    """
    if not candidates or not _judge_enabled():
        return None
    llm = _llm_or_none()
    if llm is None:
        return None
    cap = max(4, int(get_settings().memory_judge_max_candidates))
    pool = candidates[:cap]
    now = time.time()
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(pool))
    system = (
        "你是学术助手记忆系统的检索判别器。给定候选记忆（含重要性/反馈效用/命中统计）和使用场景，"
        "判断哪些记忆对当前场景真正有用，避免注入无关或过时内容。"
        "只输出 JSON：{\"selected\": [\"id1\", \"id2\", ...]}，按有用程度从高到低排序，"
        f"最多 {int(max_items)} 条；都不相关时输出空数组。id 必须原样取自候选列表。"
    )
    user = f"使用场景：{purpose}\n"
    if context_hint:
        user += f"上下文线索：{context_hint[:400]}\n"
    user += f"\n候选记忆：\n{lines}"
    data = _chat_json(llm, system, user, max_tokens=400)
    if not data:
        return None
    raw = data.get("selected")
    if not isinstance(raw, list):
        return None
    valid = {str(d.get("memory_id")) for d in pool}
    picked: list[str] = []
    for item in raw:
        mid = str(item)
        if mid in valid and mid not in picked:
            picked.append(mid)
        if len(picked) >= max_items:
            break
    if raw and not picked:
        # Model returned only unknown/hallucinated ids — treat as judge
        # failure so callers fall back instead of acting on an empty selection.
        return None
    return picked


def rank_docs_with_judge(
    docs: list[dict[str, Any]],
    *,
    purpose: str,
    context_hint: str = "",
    max_items: int,
) -> list[dict[str, Any]] | None:
    """Shared judge-ranking helper for MemoryStore and AgentMemory.

    Returns the judged docs in rank order, or ``None`` when the judge is
    unavailable/failed — callers then apply their own fallback (recency or
    TF-IDF). Centralizes the id-matching so a judge returning unknown ids
    degrades safely everywhere.
    """
    picked_ids = judge_select_memories(
        candidates=docs,
        purpose=purpose,
        context_hint=context_hint,
        max_items=max_items,
    )
    if picked_ids is None:
        return None
    by_id = {str(d.get("memory_id") or ""): d for d in docs}
    return [by_id[mid] for mid in picked_ids if mid in by_id]


def judge_feedback_deltas(
    *,
    signal: str,
    memories: list[dict[str, Any]],
    assistant_excerpt: str = "",
) -> dict[str, float]:
    """Model-judged feedback attribution: per-memory utility delta in [-1, 1].

    ``signal`` is ``up`` / ``down`` / ``citation_click``. Returns
    ``{memory_id: delta}``; empty dict when the judge is unavailable (callers
    should still log the raw feedback event).
    """
    if not memories or not _judge_enabled():
        return {}
    llm = _llm_or_none()
    if llm is None:
        return {}
    now = time.time()
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(memories))
    signal_desc = {
        "up": "用户对这条回答点了 👍（满意）",
        "down": "用户对这条回答点了 👎（不满意）",
        "citation_click": "用户点击了回答中的引用锚点（在核对原文依据）",
        "engaged_read": "用户在注入这些记忆后持续深度阅读了这篇论文（停留时长显著，属于正向参与信号）",
    }.get(signal, signal)
    system = (
        "你是学术助手记忆系统的反馈归因器。一条助手回答在生成时注入了下列记忆，现在收到了用户反馈。"
        "判断每条记忆对该回答质量的贡献方向与幅度，输出每条记忆的 utility 调整量。"
        "只输出 JSON：{\"deltas\": {\"id1\": 0.4, \"id2\": -0.3, ...}}，取值范围 [-1.0, 1.0]；"
        "与回答无关的记忆给 0；id 必须原样取自列表，不要遗漏也不要编造。"
    )
    user = f"反馈信号：{signal_desc}\n"
    if assistant_excerpt:
        user += f"回答摘录：{assistant_excerpt[:400]}\n"
    user += f"\n注入的记忆：\n{lines}"
    data = _chat_json(llm, system, user, max_tokens=400)
    if not data:
        return {}
    raw = data.get("deltas")
    if not isinstance(raw, dict):
        return {}
    valid = {str(d.get("memory_id")) for d in memories}
    out: dict[str, float] = {}
    for mid, val in raw.items():
        key = str(mid)
        if key not in valid:
            continue
        try:
            out[key] = max(-1.0, min(1.0, float(val)))
        except (TypeError, ValueError):
            continue
    return out


def judge_write_conflict(
    *,
    new_content: str,
    candidates: list[dict[str, Any]],
) -> dict[str, str] | None:
    """Mem0-style write-time judgment: how does the new fact relate to each
    similar existing memory?

    Returns ``{memory_id: "same"|"supersede"|"keep"}``:
    - ``same`` — semantic duplicate: merge into the existing memory (keep id + stats)
    - ``supersede`` — contradiction / newer value: replace the old content in place
    - ``keep`` — related but distinct: both stay

    ``None`` when the judge is unavailable (caller falls back to heuristics).
    """
    content = (new_content or "").strip()
    if not content or not candidates or not _judge_enabled():
        return None
    llm = _llm_or_none()
    if llm is None:
        return None
    now = time.time()
    pool = candidates[: get_memory_policy().conflict_judge_candidates]
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(pool))
    system = (
        "你是学术助手记忆系统的写入判别器。一条新记忆即将写入，判断它与每条相似旧记忆的关系："
        "same（语义重复，合并保留旧 id）、supersede（新记忆与旧记忆矛盾或是其更新版本，用新内容取代旧记忆）、"
        "keep（相关但不同，两条都保留）。"
        "只输出 JSON：{\"verdicts\": {\"id1\": \"same|supersede|keep\", ...}}，id 必须原样取自列表，每条都要给。"
    )
    user = f"新记忆：{content[:300]}\n\n相似旧记忆：\n{lines}"
    data = _chat_json(llm, system, user, max_tokens=300)
    if not data:
        return None
    raw = data.get("verdicts")
    if not isinstance(raw, dict):
        return None
    valid = {str(d.get("memory_id")) for d in pool}
    out: dict[str, str] = {}
    for mid, action in raw.items():
        key = str(mid)
        act = str(action).strip().lower()
        if key in valid and act in ("same", "supersede", "keep"):
            out[key] = act
    if raw and not out:
        return None  # all ids hallucinated → judge failure
    return out


def judge_memory_links(
    *,
    new_content: str,
    candidates: list[dict[str, Any]],
) -> list[str] | None:
    """A-MEM-style link generation: which existing memories should the new
    memory be linked to (associative recall edges).

    Returns a list of memory_ids to link, or ``None`` when the judge is
    unavailable (callers simply skip link creation).
    """
    content = (new_content or "").strip()
    if not content or not candidates or not _judge_enabled():
        return None
    llm = _llm_or_none()
    if llm is None:
        return None
    now = time.time()
    pool = candidates[: get_memory_policy().link_judge_candidates]
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(pool))
    system = (
        "你是学术助手记忆系统的关联判别器。一条新记忆已写入，判断它与哪些旧记忆存在实质关联"
        "（同一研究问题/方法/结论、因果关系、前提与推论、同一论文的不同侧面）。"
        "不要链接仅主题宽泛相似的内容。只输出 JSON：{\"links\": [\"id1\", ...]}，无关联时输出空数组；"
        "id 必须原样取自候选列表。"
    )
    user = f"新记忆：{content[:300]}\n\n候选旧记忆：\n{lines}"
    data = _chat_json(llm, system, user, max_tokens=200)
    if not data:
        return None
    raw = data.get("links")
    if not isinstance(raw, list):
        return None
    valid = {str(d.get("memory_id")) for d in pool}
    out = [str(m) for m in raw if str(m) in valid]
    if raw and not out:
        return None  # hallucinated ids → judge failure
    return list(dict.fromkeys(out))


def judge_reflect(
    *,
    evidence: list[dict[str, Any]],
    existing_reflections: list[str] | None = None,
) -> list[str] | None:
    """Reflection: synthesize cross-memory patterns into 1-3 meta-memories.

    ``evidence`` items carry a memory's content plus its feedback/usage
    outcome (utility, access_count, recent events). The judge looks for
    *patterns across memories* — e.g. "answers on topic X keep getting 👎
    when too technical" — which single-memory utility updates can never
    capture. Returns reflection texts, or ``None`` when unavailable.
    """
    if not evidence or not _judge_enabled():
        return None
    llm = _llm_or_none()
    if llm is None:
        return None
    now = time.time()
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(evidence))
    existing = "\n".join(f"- {r[:120]}" for r in (existing_reflections or [])[:5])
    system = (
        "你是学术助手记忆系统的反思器。给定一批记忆及其使用反馈（命中次数、反馈效用变化），"
        "提炼 1-3 条跨记忆的规律作为「元记忆」：关于用户偏好、回答质量教训、研究方向聚焦等的可执行洞察。"
        "每条不超过 60 字，必须能从证据中推出，不要复述单条记忆内容，不要编造证据中没有的规律。"
        "只输出 JSON：{\"reflections\": [\"...\", ...]}；证据不足以提炼时输出空数组。"
    )
    user = f"记忆及反馈证据：\n{lines}"
    if existing:
        user += f"\n\n已有元记忆（避免重复）：\n{existing}"
    data = _chat_json(llm, system, user, max_tokens=400)
    if not data:
        return None
    raw = data.get("reflections")
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for r in raw[:3]:
        text = str(r or "").strip()[:120]
        if text:
            out.append(text)
    return out


def judge_evolution_batch(
    *,
    candidates: list[dict[str, Any]],
    scorecard: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Model-judged periodic review (Karpathy "append-and-review"): rescue /
    merge / keep / prune stale memories.

    Returns a list of decisions:
    ``[{"memory_id": str, "action": "rescue"|"merge"|"keep"|"prune",
       "merge_into": str|None, "importance": float|None,
       "utility": float|None, "reason": str}]``

    - ``rescue`` — still deserves attention: float to top (refresh last_used).
      This is the core Karpathy review action — "fish through sunk notes and
      rescue anything that deserves to not leave attention".
    - ``merge`` — semantic duplicate of another candidate: combine (give
      merge_into target id; source soft-archived).
    - ``keep`` — leave as-is; may revise importance/utility.
    - ``prune`` — truly worthless: soft-retire (recoverable; purged after a
      TTL). Rare — the default for forgotten notes is to keep sinking, not
      deletion.

    ``scorecard`` (self-iteration): past-decision outcomes on this DB. When
    provided, the model reads its own hit rate and self-calibrates (e.g. a
    high prune-of-accessed rate → be more conservative about pruning accessed
    memories). Counts are shown alongside rates so small-N doesn't cause
    overreaction. Feeds the prompt, not learned weights.

    Empty list when the judge is unavailable (callers skip this cycle).
    """
    if not candidates or not _judge_enabled():
        return []
    llm = _llm_or_none()
    if llm is None:
        return []
    now = time.time()
    lines = "\n".join(_format_candidate(i + 1, d, now) for i, d in enumerate(candidates))
    scorecard_line = _format_scorecard(scorecard) if scorecard else ""
    system = (
        "你是学术助手记忆系统的周期 reviewer（参考 Karpathy 的 append-and-review："
        "记忆默认保留、只下沉，极少删除）。下列候选是长期未被检索命中的老记忆。"
        "对每条决定：rescue（仍值得注意，上浮到顶——刷新 last_used，优先考虑）、"
        "merge（与另一条语义重复，给出 merge_into 目标 id，源归档）、"
        "keep（保留现状，可修正 importance/utility）、"
        "prune（确实无价值的垃圾，软归档删除——很少用）。"
        "默认倾向 rescue/keep，prune 仅用于真正无价值的内容。"
        + scorecard_line +
        "只输出 JSON：{\"decisions\": [{\"memory_id\": \"...\", \"action\": \"rescue|merge|keep|prune\", "
        "\"merge_into\": null, \"importance\": null, \"utility\": null, \"reason\": \"一句话\"}, ...]}。"
        "id 必须原样取自候选列表，每条候选都要给出决定。"
    )
    user = f"候选记忆：\n{lines}"
    # Token budget scales with batch: ~60-75 tokens per decision object
    # (hex id + JSON overhead + reason); a full batch at a fixed small budget
    # truncated mid-JSON and the whole cycle silently skipped.
    data = _chat_json(llm, system, user, max_tokens=max(900, 90 * len(candidates) + 200))
    if not data:
        return []
    raw = data.get("decisions")
    if not isinstance(raw, list):
        return []
    valid = {str(d.get("memory_id")) for d in candidates}
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("memory_id") or "")
        action = str(item.get("action") or "").strip().lower()
        if mid not in valid or action not in ("rescue", "merge", "keep", "prune"):
            continue
        merge_into = item.get("merge_into")
        decision: dict[str, Any] = {
            "memory_id": mid,
            "action": action,
            "merge_into": str(merge_into) if merge_into and str(merge_into) in valid else None,
            "importance": None,
            "utility": None,
            "reason": str(item.get("reason") or "")[:200],
        }
        try:
            if item.get("importance") is not None:
                decision["importance"] = max(0.0, min(1.0, float(item["importance"])))
        except (TypeError, ValueError):
            pass
        try:
            if item.get("utility") is not None:
                decision["utility"] = max(_UTILITY_MIN, min(_UTILITY_MAX, float(item["utility"])))
        except (TypeError, ValueError):
            pass
        if action == "merge" and not decision["merge_into"]:
            # merge without a valid target is meaningless — treat as keep
            decision["action"] = "keep"
        out.append(decision)
    return out
