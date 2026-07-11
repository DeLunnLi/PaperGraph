"""Add user_id column to existing tables for multi-user data isolation.

Migration is idempotent: checks if column exists before adding.
All existing data gets user_id=1 (default user) for backwards compat.
"""
from __future__ import annotations

import sqlite3
import logging

logger = logging.getLogger(__name__)


def migrate_add_user_id(db_path: str) -> None:
    """Add user_id INTEGER DEFAULT 1 to papers, paper_reader_turns, daily_recommend_feedback."""
    conn = sqlite3.connect(db_path)
    try:
        _add_column_if_missing(conn, "papers", "user_id", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "paper_reader_turns", "user_id", "INTEGER DEFAULT 1")
        _add_column_if_missing(conn, "daily_recommend_feedback", "user_id", "INTEGER DEFAULT 1")
        conn.commit()
        logger.info("user_id migration complete")
    except Exception as e:
        logger.warning("user_id migration failed (non-fatal): %s", e)
    finally:
        conn.close()


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        logger.info("Added column %s.%s", table, column)
    else:
        logger.debug("Column %s.%s already exists", table, column)
