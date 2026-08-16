"""Probe: retrieval-quality golden cases (real search APIs required).

Not collected by pytest (probe_* naming). Run manually:
    conda run -n papergraph python tests/probe_retrieval.py
    # or with explicit path:
    cd backend && PYTHONPATH=. python tests/probe_retrieval.py

Golden cases are well-known ML papers with stable arXiv ids. For each query
we check whether the expected paper surfaces in the top-k results, at what
rank, and via which source. This measures REAL retrieval quality — when a
change to fusion/RRF/semantic-scoring ships, re-run to prove "better" not
just "not regressed". A drop in recall@5 means regression.

Tolerates API flakiness: a case is PASS if the expected paper is found by
ANY source in top-10; individual-source miss is reported but not fatal.
Exits 1 if overall recall@5 < 0.75 (threshold tunable).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.search.normalize import strip_arxiv_version
from app.api.dependencies import get_searcher


# Golden cases: query phrase → expected arXiv id (version-stripped) + title
# keywords (fallback when a source doesn't return arxiv_id, e.g. DBLP).
GOLDEN = [
    ("attention is all you need", "1706.03762", ["attention", "all you need"]),
    ("deep residual learning for image recognition", "1512.03385", ["deep", "residual"]),
    ("batch normalization accelerating deep network training", "1502.03167", ["batch", "normalization"]),
    ("adam a method for stochastic optimization", "1412.6980", ["adam", "stochastic"]),
    ("dropout a simple way to prevent neural networks overfitting", "1207.0580", ["dropout", "prevent"]),
    ("sequence to sequence learning with neural networks", "1409.3215", ["sequence", "sequence"]),
    ("neural machine translation by jointly learning to align and translate", "1409.0473", ["align", "translate"]),
    ("generative adversarial networks", "1406.2661", ["generative", "adversarial"]),
    ("autoencoding variational bayes", "1312.6114", ["autoencoding", "variational"]),
    ("denoising diffusion probabilistic models", "2006.11239", ["denoising", "diffusion"]),
]

_PASSED: list[str] = []
_FAILED: list[str] = []
_SOURCE_HITS: dict[str, int] = {}


def _check(name: str, ok: bool, detail: str = "") -> None:
    (_PASSED if ok else _FAILED).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _matches(paper, expect_arxiv: str, title_kws: list[str]) -> bool:
    """A paper matches if its arxiv_id (version-stripped) equals the expected,
    OR its title contains all the expected title keywords (fuzzy fallback)."""
    aid = strip_arxiv_version(getattr(paper, "arxiv_id", None) or "")
    if aid and aid == strip_arxiv_version(expect_arxiv):
        return True
    title = (getattr(paper, "title", "") or "").lower()
    if title and all(kw.lower() in title for kw in title_kws):
        return True
    return False


async def _run_one(searcher, query: str, expect_arxiv: str, title_kws: list[str]) -> tuple[bool, int | None, str | None]:
    """Search and locate the expected paper. Returns (found, rank, source)."""
    results = await searcher.search_async(query=query, max_results=10)
    for rank, paper in enumerate(results, start=1):
        if _matches(paper, expect_arxiv, title_kws):
            return True, rank, getattr(paper, "source", None)
    return False, None, None


async def main() -> int:
    searcher = get_searcher()
    print(f"=== retrieval golden cases ({len(GOLDEN)} queries, top-10) ===\n")
    recall5 = 0
    recall10 = 0
    for query, expect_arxiv, title_kws in GOLDEN:
        tag = query[:38].replace(" ", "_")
        try:
            found, rank, source = await _run_one(searcher, query, expect_arxiv, title_kws)
        except Exception as exc:
            _check(f"retrieval.{tag}", False, f"exception: {exc!r}")
            continue
        if found:
            recall10 += 1
            if rank is not None and rank <= 5:
                recall5 += 1
            if source:
                _SOURCE_HITS[source] = _SOURCE_HITS.get(source, 0) + 1
            _check(f"retrieval.{tag}", True, f"rank={rank} source={source}")
        else:
            _check(f"retrieval.{tag}", False, f"expected {expect_arxiv} not in top-10")

    n = len(GOLDEN)
    print(f"\n=== summary ===")
    print(f"recall@5:  {recall5}/{n} = {recall5/n:.0%}")
    print(f"recall@10: {recall10}/{n} = {recall10/n:.0%}")
    print(f"per-source hits: {_SOURCE_HITS or '(none)'}")

    threshold = 0.75
    overall_ok = (recall5 / n) >= threshold
    _check(f"retrieval.recall5>={int(threshold*100)}%", overall_ok,
           f"{recall5}/{n} = {recall5/n:.0%}")

    print(f"\n[probe] {len(_PASSED)} passed, {len(_FAILED)} failed")
    return 0 if not _FAILED else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
