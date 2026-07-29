"""Tests for paper_reader_history.list_turns — esp. the recent=True window
that locks in the round-1 fix (>limit turns must return the MOST RECENT, not
the oldest, in chronological order).
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.reader.paper_reader_history import append_turn, list_turns, ensure_tables


def _db_path() -> str:
    fd, p = tempfile.mkstemp(suffix=".db")
    import os
    os.close(fd)
    ensure_tables(p)
    return p


def _insert_turn(db_path: str, *, paper_id: int, user_id: int, role: str, content: str, ts: int) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO paper_reader_turns(paper_id,user_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (paper_id, user_id, role, content, ts),
        )


def test_list_turns_recent_returns_most_recent_in_chronological_order():
    db = _db_path()
    # 210 turns with increasing timestamps; limit=200 must keep turns 11..210
    # (the newest 200), ordered chronologically (oldest-of-the-kept first).
    base = 1_700_000_000
    for i in range(210):
        _insert_turn(db, paper_id=1, user_id=7, role="user", content=f"turn {i}", ts=base + i)
    turns = list_turns(db, paper_id=1, user_id=7, limit=200, recent=True)
    assert len(turns) == 200
    # The dropped ones are the oldest 10 (turns 0..9); kept start at turn 10.
    assert turns[0]["content"] == "turn 10"
    assert turns[-1]["content"] == "turn 209"
    # Chronological order (ASC) despite the DESC fetch.
    ts_seq = [t["created_at"] for t in turns]
    assert ts_seq == sorted(ts_seq)


def test_list_turns_default_ascending_keeps_oldest():
    """Default (recent=False) preserves the original ASC + LIMIT semantics
    used by ensure_opening_turn (inspects the first turn)."""
    db = _db_path()
    base = 1_700_000_000
    for i in range(210):
        _insert_turn(db, paper_id=1, user_id=7, role="user", content=f"turn {i}", ts=base + i)
    turns = list_turns(db, paper_id=1, user_id=7, limit=200)
    assert len(turns) == 200
    assert turns[0]["content"] == "turn 0"  # oldest
    assert turns[-1]["content"] == "turn 199"


def test_list_turns_recent_under_limit_returns_all_chronological():
    db = _db_path()
    base = 1_700_000_000
    for i in range(5):
        _insert_turn(db, paper_id=1, user_id=7, role="user", content=f"turn {i}", ts=base + i)
    turns = list_turns(db, paper_id=1, user_id=7, limit=200, recent=True)
    assert [t["content"] for t in turns] == [f"turn {i}" for i in range(5)]


def test_list_turns_user_id_scoping():
    db = _db_path()
    _insert_turn(db, paper_id=1, user_id=7, role="user", content="user7", ts=100)
    _insert_turn(db, paper_id=1, user_id=8, role="user", content="user8", ts=101)
    turns = list_turns(db, paper_id=1, user_id=7, limit=200, recent=True)
    assert len(turns) == 1
    assert turns[0]["content"] == "user7"


def test_append_turn_then_list_recent_roundtrip():
    db = _db_path()
    append_turn(db, paper_id=2, user_id=7, role="user", content="hello")
    append_turn(db, paper_id=2, user_id=7, role="assistant", content="hi back")
    turns = list_turns(db, paper_id=2, user_id=7, limit=200, recent=True)
    assert [t["role"] for t in turns] == ["user", "assistant"]
