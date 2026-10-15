"""Regenerated reader openings must actually be persisted.

Regression: ensure_opening_turn() refreshed the stored opening with

    UPDATE paper_reader_turns SET content=? WHERE ... ORDER BY created_at LIMIT 1

`UPDATE ... ORDER BY ... LIMIT` requires SQLite to be compiled with
SQLITE_ENABLE_UPDATE_DELETE_LIMIT, which the CPython stdlib build is not. The
statement therefore raised OperationalError on *every* call, and a bare
`except Exception: pass` swallowed it — so a regenerated opening was silently
discarded and the reader kept showing the stale text forever.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.reader.paper_reader_history import (
    append_turn,
    ensure_opening_turn,
    list_turns,
)


@pytest.fixture()
def db_path() -> str:
    return str(Path(tempfile.mkdtemp()) / "reader.db")


def _contents(db: str) -> list[str]:
    return [t["content"] for t in list_turns(db, paper_id=1, user_id=1, limit=20)]


def test_first_opening_is_stored(db_path: str) -> None:
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="first")
    assert _contents(db_path) == ["first"]


def test_regenerated_opening_replaces_the_old_one(db_path: str) -> None:
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="original")
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="regenerated")
    assert _contents(db_path) == ["regenerated"], "stale opening was not refreshed"


def test_refresh_does_not_disturb_later_turns(db_path: str) -> None:
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="original")
    append_turn(db_path, paper_id=1, user_id=1, role="user", content="question")
    append_turn(db_path, paper_id=1, user_id=1, role="assistant", content="answer")

    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="updated")

    assert _contents(db_path) == ["updated", "question", "answer"]


def test_identical_opening_is_a_noop(db_path: str) -> None:
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="same")
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="same")
    assert _contents(db_path) == ["same"]


def test_other_users_openings_are_untouched(db_path: str) -> None:
    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="user1 opening")
    ensure_opening_turn(db_path, paper_id=1, user_id=2, opening_text="user2 opening")

    ensure_opening_turn(db_path, paper_id=1, user_id=1, opening_text="user1 updated")

    assert _contents(db_path) == ["user1 updated"]
    u2 = [t["content"] for t in list_turns(db_path, paper_id=1, user_id=2, limit=5)]
    assert u2 == ["user2 opening"], "refreshing one user's opening hit another user's row"


def test_update_order_by_limit_is_unsupported_here() -> None:
    """Documents *why* the subquery form is required — if a future SQLite build
    enables UPDATE_LIMIT this test flags that the constraint changed."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, c TEXT, ts INT)")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("UPDATE t SET c='x' ORDER BY ts LIMIT 1")
