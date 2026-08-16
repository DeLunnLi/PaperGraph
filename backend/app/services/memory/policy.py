"""Single source of truth for memory-system tunables (no scattered magic numbers).

Design rule for the self-evolving memory system: *decisions* belong to the
LLM judges (memory_judge.py); *instrumentation knobs* live here. Anything
that is a ratio, threshold, budget, cap, or window must be a named policy
field with a documented default — never a bare literal buried in logic.

User-facing knobs (toggle/intervals/batch sizes) live in Settings
(settings/config.py); this module holds the internal mechanics constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class MemoryPolicy:
    # --- utility instrumentation bounds (shared by judge + store clamps) ---
    utility_min: float = -2.0
    utility_max: float = 5.0

    # --- write path ---
    # word-overlap above which two memories become judge/heuristic candidates
    dedup_candidate_overlap: float = 0.25
    # heuristic fallback (judge unavailable): merge above this overlap…
    dedup_merge_overlap: float = 0.7
    # …and only ask the (legacy) LLM duplicate check in this gray zone
    dedup_gray_zone_overlap: float = 0.35
    # write-time link/conflict judge candidate caps
    conflict_judge_candidates: int = 5
    link_judge_candidates: int = 8
    linkable_kinds: tuple[str, ...] = ("short", "long", "paper_summary", "preference", "reflection")
    # reflection (元记忆) write-time importance — distilled cross-memory patterns
    reflection_importance: float = 0.8

    # --- lifecycle (working tier) ---
    working_ttl_minutes: int = 60
    working_compress_trigger: int = 50
    working_compress_keep_head: int = 20
    # how many oldest working entries one compression summarizes AND deletes —
    # the deleted set must never exceed the summarized set (silent data loss)
    working_compress_batch: int = 20

    # --- retrieval ---
    max_context_items: int = 10
    chars_per_token_estimate: int = 3  # budget accounting for mixed CJK/EN
    link_expansion_max: int = 2        # [关联] memories appended per context block
    semantic_similarity_floor: float = 0.2   # recall gathering floor (judge still decides)
    embed_batch_size: int = 16

    # --- implicit signals ---
    engaged_read_grace_sec: int = 600  # chat may precede the frontend timer

    # --- write-time default importances (only used when the extractor LLM
    # did not assign one; singleton kinds are curated facts, not decisions) ---
    default_importance: float = 0.5
    paper_summary_importance: float = 0.75
    preference_importance: float = 0.85

    # --- agent (cross-agent shared) memory ---
    agent_episodic_cap: int = 20
    agent_procedural_cap: int = 30
    agent_shared_working_cap: int = 15
    agent_dedup_threshold: float = 0.75
    procedural_hints_per_query: int = 3


@lru_cache(maxsize=1)
def get_memory_policy() -> MemoryPolicy:
    """Process-wide policy singleton (frozen dataclass — tests may monkeypatch
    fields by clearing this cache with ``get_memory_policy.cache_clear()``)."""
    return MemoryPolicy()


def clamp_utility(value: float) -> float:
    """Clamp a utility score into the policy bounds — the one place the
    [-2, 5] range is defined for writes (judge prompts reference it too)."""
    p = get_memory_policy()
    return max(p.utility_min, min(p.utility_max, float(value)))


def attention_key(doc: dict) -> tuple[float, float]:
    """Karpathy "position = last touched" sort key (higher = top of mind).

    ``max(last_used, timestamp)`` collapses creation and rescue into one time
    axis: a freshly appended memory floats to the top (timestamp), and a
    rescued/Injected one floats above its creation recency (last_used). DESC
    sort gives the append-and-review "things sink unless rescued" ordering
    without a magic weighted formula — ``utility`` is a secondary tiebreak so
    among equally-recent memories the higher-value one wins. ``access_count``
    is intentionally NOT a key: in Karpathy's model frequency doesn't buy
    position, only recency of attention does.
    """
    last_used = float(doc.get("last_used") or 0)
    timestamp = float(doc.get("timestamp") or 0)
    position = last_used if last_used > 0 else timestamp
    return (position, float(doc.get("utility") or 0))
