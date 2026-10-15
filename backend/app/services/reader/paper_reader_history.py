
from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from ...utils.common import exec_sql, sqlite_connect
from ...utils.db_init import ensure_once

logger = logging.getLogger(__name__)

def ensure_tables(db_path: str) -> None:
    ensure_once(db_path, _create_tables)


def _create_tables(db_path: str) -> None:
    exec_sql(db_path,
        "CREATE TABLE IF NOT EXISTS paper_reader_turns(id INTEGER PRIMARY KEY AUTOINCREMENT,paper_id INTEGER NOT NULL,user_id INTEGER NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,created_at INTEGER NOT NULL,metadata TEXT)",
        "CREATE INDEX IF NOT EXISTS idx_paper_reader_turns_paper ON paper_reader_turns(paper_id,created_at)",
    )
    # Migration: add metadata column if missing
    try:
        with _conn(db_path) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_reader_turns)").fetchall()]
            if "metadata" not in cols:
                conn.execute("ALTER TABLE paper_reader_turns ADD COLUMN metadata TEXT")
            if "user_id" not in cols:
                conn.execute("ALTER TABLE paper_reader_turns ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_reader_turns_user_paper ON paper_reader_turns(user_id,paper_id,created_at)")
    except Exception:
        pass

@contextmanager
def _conn(db_path: str, *, row_factory=None):
    conn = sqlite_connect(db_path)
    if row_factory:
        conn.row_factory = row_factory
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def append_turn(db_path: str, *, paper_id: int, user_id: int, role: str, content: str, metadata: str | None = None) -> int | None:
    """Append a turn and return its row id (used as the feedback attribution key)."""
    ensure_tables(db_path)
    role2 = (role or "").strip().lower()
    if role2 not in ("user", "assistant"):
        role2 = "user"
    text = (content or "").strip()
    if not text:
        return None
    now = int(time.time())
    with _conn(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO paper_reader_turns(paper_id,user_id,role,content,created_at,metadata) VALUES(?,?,?,?,?,?)",
            (int(paper_id), int(user_id), role2, text, now, metadata),
        )
        return int(cur.lastrowid) if cur.lastrowid else None

def prepend_turn(
    db_path: str,
    *,
    paper_id: int,
    user_id: int,
    role: str,
    content: str,
    before_created_at: int,
) -> None:
    ensure_tables(db_path)
    role2 = (role or "").strip().lower()
    if role2 not in ("user", "assistant"):
        role2 = "user"
    text = (content or "").strip()
    if not text:
        return
    ts = int(before_created_at) - 1
    if ts < 0:
        ts = 0
    now = int(time.time())
    if ts >= now:
        ts = now - 1
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT INTO paper_reader_turns(paper_id,user_id,role,content,created_at) VALUES(?,?,?,?,?)",
            (int(paper_id), int(user_id), role2, text, ts),
        )

def ensure_opening_turn(db_path: str, *, paper_id: int, user_id: int, opening_text: str) -> None:
    op = (opening_text or "").strip()
    if not op:
        return
    turns = list_turns(db_path, paper_id=int(paper_id), user_id=user_id, limit=5)
    if not turns:
        append_turn(db_path, paper_id=int(paper_id), user_id=user_id, role="assistant", content=op)
        return
    first = turns[0]
    r0 = (first.get("role") or "").strip().lower()
    c0 = (first.get("content") or "").strip()
    if r0 == "assistant" and c0 == op:
        return  # Already up to date
    if r0 == "assistant":
        # Opening content changed (e.g. re-generated) — update the first turn.
        # NOTE: `UPDATE ... ORDER BY ... LIMIT` needs SQLite to be built with
        # SQLITE_ENABLE_UPDATE_DELETE_LIMIT, which the stdlib build is not. It
        # raised OperationalError on every call and the bare `except: pass`
        # hid it, so regenerated openings were silently never persisted.
        # Select the target row id in a subquery instead — portable everywhere.
        try:
            with _conn(db_path) as conn:
                conn.execute(
                    "UPDATE paper_reader_turns SET content=? WHERE id = ("
                    "  SELECT id FROM paper_reader_turns"
                    "  WHERE paper_id=? AND user_id=? AND role='assistant'"
                    "  ORDER BY created_at ASC, id ASC LIMIT 1"
                    ")",
                    (op, int(paper_id), int(user_id)),
                )
        except Exception as exc:
            logger.warning(
                "ensure_opening_turn: failed to refresh opening for paper=%s user=%s: %s",
                paper_id, user_id, exc,
            )
        return
    if r0 == "user":
        try:
            ts0 = int(first.get("created_at") or 0)
        except Exception:
            ts0 = int(time.time())
        prepend_turn(db_path, paper_id=int(paper_id), user_id=user_id, role="assistant", content=op, before_created_at=ts0)
        return

def get_turn(db_path: str, *, turn_id: int, user_id: int) -> dict[str, Any] | None:
    """Load a single turn by row id (feedback attribution looks turns up this way)."""
    ensure_tables(db_path)
    with _conn(db_path, row_factory=sqlite3.Row) as conn:
        row = conn.execute(
            "SELECT id, paper_id, user_id, role, content, created_at, metadata "
            "FROM paper_reader_turns WHERE id = ? AND user_id = ?",
            (int(turn_id), int(user_id)),
        ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "paper_id": int(row["paper_id"]),
        "role": str(row["role"] or ""),
        "content": str(row["content"] or ""),
        "metadata": str(row["metadata"] or ""),
    }


def list_turns(
    db_path: str,
    *,
    paper_id: int,
    user_id: int,
    limit: int = 200,
    recent: bool = False,
) -> list[dict[str, Any]]:
    """Return turns for a paper.

    By default the earliest ``limit`` turns are returned (ASC), which
    ``ensure_opening_turn`` relies on to inspect the opening. Pass
    ``recent=True`` for the chat history view: it fetches the most recent
    ``limit`` turns (DESC) and reverses to chronological order, so papers with
    more than ``limit`` turns do not silently lose the latest conversation.
    """
    ensure_tables(db_path)
    order = "created_at DESC, id DESC" if recent else "created_at ASC, id ASC"
    with _conn(db_path, row_factory=sqlite3.Row) as conn:
        rows = conn.execute(
            f"SELECT id,role,content,created_at FROM paper_reader_turns WHERE paper_id=? AND user_id=? ORDER BY {order} LIMIT ?",
            (int(paper_id), int(user_id), int(limit)),
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append({
                "id": int(r["id"]),
                "role": (r["role"] or "").strip(),
                "content": (r["content"] or "").strip(),
                "created_at": int(r["created_at"] or 0),
            })
        if recent:
            out.reverse()
        return out
