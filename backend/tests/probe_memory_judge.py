"""Probe: golden cases for the memory LLM judges (real LLM required).

Not collected by pytest (probe_* naming). Run manually:
    conda run -n papergraph python tests/probe_memory_judge.py

Each case asserts the judge's decision direction on unambiguous inputs —
soft signals of judge quality, not exact-match checks. Exits 1 on failure.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

_PASSED: list[str] = []
_FAILED: list[str] = []


def _check(name: str, ok: bool, detail: str = "") -> None:
    (_PASSED if ok else _FAILED).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _doc(mid: str, content: str, kind: str = "short", imp: float = 0.6) -> dict:
    now = time.time()
    return {
        "memory_id": mid, "user_id": "papergraph:paper:1", "content": content,
        "memory_type": kind, "importance": imp, "utility": 0.0,
        "access_count": 0, "last_used": 0.0, "timestamp": now,
    }


def main() -> int:
    from app.services.llm.llm_service import is_llm_configured
    if not is_llm_configured():
        print("[probe] LLM 未配置，跳过（配置 LLM_API_KEY 后再跑）")
        return 0

    from app.services.memory import memory_judge as j

    # --- judge_select_memories: relevant beats irrelevant ---
    picked = j.judge_select_memories(
        candidates=[
            _doc("m1", "用户偏好简洁的中文回答"),
            _doc("m2", "红烧肉的做法：五花肉焯水后炒糖色"),
            _doc("m3", "用户最近在研究检索增强生成的重排序问题"),
        ],
        purpose="按用户当前问题检索相关记忆",
        context_hint="RAG  rerank 怎么做",
        max_items=2,
    )
    _check("select.relevant_picked", picked is not None and "m3" in picked, f"picked={picked}")
    _check("select.irrelevant_excluded", picked is not None and "m2" not in (picked or []))

    # --- judge_feedback_deltas: up positive, down negative ---
    mems = [_doc("m1", "用户偏好简洁的中文回答")]
    up = j.judge_feedback_deltas(signal="up", memories=mems, assistant_excerpt="简洁准确的回答")
    _check("feedback.up_positive", bool(up) and up.get("m1", 0) > 0, f"deltas={up}")
    down = j.judge_feedback_deltas(signal="down", memories=mems, assistant_excerpt="冗长跑题的回答")
    _check("feedback.down_negative", bool(down) and down.get("m1", 0) < 0, f"deltas={down}")

    # --- judge_write_conflict: same / supersede / keep ---
    dup = j.judge_write_conflict(
        new_content="用户偏好简洁的中文回答",
        candidates=[_doc("old", "用户喜欢简洁的中文回复")],
    )
    _check("conflict.duplicate_same", dup is not None and dup.get("old") == "same", f"verdicts={dup}")
    contra = j.judge_write_conflict(
        new_content="用户最近明确表示不想再读 transformer 方向的论文",
        candidates=[_doc("old", "用户喜欢 transformer 架构的论文")],
    )
    _check("conflict.contradiction_supersede", contra is not None and contra.get("old") == "supersede",
           f"verdicts={contra}")
    distinct = j.judge_write_conflict(
        new_content="用户开始关注图神经网络的引用预测",
        candidates=[_doc("old", "用户偏好简洁的中文回答")],
    )
    _check("conflict.distinct_keep", distinct is not None and distinct.get("old") == "keep",
           f"verdicts={distinct}")

    # --- judge_evolution_batch: junk pruned, value kept ---
    decisions = j.judge_evolution_batch(candidates=[
        _doc("junk", "用户问：哈哈哈", kind="working", imp=0.3),
        _doc("value", "用户的研究方向是检索增强生成的重排序与评测", imp=0.7),
    ])
    by_id = {d["memory_id"]: d["action"] for d in (decisions or [])}
    _check("evolution.junk_pruned", by_id.get("junk") == "prune", f"decisions={by_id}")
    _check("evolution.value_kept", by_id.get("value") == "keep", f"decisions={by_id}")

    # --- judge_memory_links: related linked, unrelated not ---
    links = j.judge_memory_links(
        new_content="用户发现 colbert 的延迟交互重排序效果最好",
        candidates=[
            _doc("rel", "用户最近在研究检索增强生成的重排序问题"),
            _doc("unrel", "红烧肉的做法：五花肉焯水后炒糖色"),
        ],
    )
    _check("links.related_linked", links is not None and "rel" in (links or []), f"links={links}")
    _check("links.unrelated_not_linked", links is not None and "unrel" not in (links or []))

    print(f"\n[probe] {len(_PASSED)} passed, {len(_FAILED)} failed")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
