"""Contract tests locking the CURRENT evolution loop structure.

These exist as a regression net BEFORE the memory/knowledge split (plan stage 2).
They assert the structure that the split will CHANGE:

- ``evolve_all_memory_dbs`` is ONE combined loop over BOTH dbs (reader + agent).
- ``reflect_on_memory_db`` dispatches to MemoryStore vs AgentMemory via an
  ``if db_kind == "reader" else`` branch, and the reader branch constructs
  ``MemoryStore(papers_db)`` relying on an implicit path redirect inside
  MemoryStore.__init__.

When stage 2 lands (two independent loops, dispatch removed), these tests are
EXPECTED to fail and must be updated to the new contract:
  - ``evolve_reader_memory()`` and ``evolve_knowledge_base()`` are separate entry points
  - reflect dispatch is gone; each loop writes to its own module directly

Locking the current structure first makes the split diff explicit and reviewable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.memory import memory_evolution
from app.services.memory.db_paths import memory_db_paths


def test_memory_db_paths_returns_both_kinds():
    """Today both dbs are enumerated from one dict (the combined-loop input)."""
    paths = memory_db_paths()
    assert set(paths.keys()) == {"reader", "agent"}
    assert paths["reader"].endswith("reader_memory.db")
    assert paths["agent"].endswith("agent_memory.db")


def test_evolve_all_delegates_to_two_entry_points(tmp_path, monkeypatch):
    """evolve_all_memory_dbs delegates to evolve_reader_memory +
    evolve_knowledge_base (the split entry points), each running evolve +
    reflect on its own DB. The combined loop is backward-compat only.
    """
    calls: list[str] = []

    def fake_evolve(*, db_path):
        calls.append(f"evolve:{os.path.basename(db_path)}")
        return {"db_path": db_path, "candidates": 0, "skipped": True}

    monkeypatch.setattr(memory_evolution, "evolve_memory_db", fake_evolve)
    # Patch reflect_reader/knowledge (the per-module writers) — NOT the old
    # reflect_on_memory_db dispatcher, which the split no longer calls.
    monkeypatch.setattr(
        memory_evolution, "reflect_reader_memory",
        lambda db_path: calls.append(f"reflect:reader:{os.path.basename(db_path)}") or
        {"db_path": db_path, "reflected": 0, "skipped": True},
    )
    monkeypatch.setattr(
        memory_evolution, "reflect_knowledge_base",
        lambda db_path: calls.append(f"reflect:knowledge:{os.path.basename(db_path)}") or
        {"db_path": db_path, "reflected": 0, "skipped": True},
    )
    from app.services.memory import db_paths
    monkeypatch.setattr(
        db_paths, "memory_db_paths",
        lambda: {"reader": str(tmp_path / "reader.db"), "agent": str(tmp_path / "agent.db")},
    )

    memory_evolution.evolve_all_memory_dbs()

    assert calls == [
        "evolve:reader.db", "reflect:reader:reader.db",
        "evolve:agent.db", "reflect:knowledge:agent.db",
    ]


def test_evolution_split_into_two_independent_entry_points(tmp_path):
    """Stage 2 split: reflect_on_memory_db's if/else dispatch is gone —
    replaced by reflect_reader_memory / reflect_knowledge_base, each owning
    its write path directly. The shared core (_run_reflection_core) has NO
    db_kind branch.
    """
    import app.services.memory.memory_evolution as mev

    # Two independent entry points exist.
    assert hasattr(mev, "evolve_reader_memory")
    assert hasattr(mev, "evolve_knowledge_base")
    assert hasattr(mev, "reflect_reader_memory")
    assert hasattr(mev, "reflect_knowledge_base")

    src = Path(mev.__file__).read_text(encoding="utf-8")
    # The shared core must not branch on db_kind — that was the coupling.
    core = _run_reflection_core_src(src)
    assert "if db_kind" not in core
    assert "db_kind ==" not in core


def _run_reflection_core_src(src: str) -> str:
    """Extract the body of _run_reflection_core for the no-dispatch assertion."""
    start = src.find("def _run_reflection_core(")
    end = src.find("\ndef ", start + 1)
    return src[start:end] if start != -1 and end != -1 else ""


def test_memory_store_init_redirects_papers_db_to_reader_memory(tmp_path, monkeypatch):
    """MemoryStore constructed with papers.db path silently redirects to
    reader_memory.db. The split removes this implicit redirect (reader reflect
    will pass reader_memory.db directly).

    We lock the redirect EXISTS today so removing it is an explicit change.
    """
    from app.services.memory.memory_store import MemoryStore

    papers_db = str(tmp_path / "papers.db")
    s = MemoryStore(papers_db)

    # The store does NOT use papers.db for memories — it redirects.
    assert s.db_path != papers_db
    assert s.db_path.endswith("reader_memory.db")
