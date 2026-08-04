from __future__ import annotations

import threading

#: db_paths whose schema has already been initialized in this process.
#: Guarded by _lock so concurrent first-callers on the same path only run once.
_ensured: set[str] = set()
_lock = threading.Lock()


def ensure_once(db_path: str, create_fn) -> None:
    """Run ``create_fn(db_path)`` once per ``db_path`` per process.

    Each module-level ``ensure_tables`` previously ran ``CREATE TABLE IF NOT
    EXISTS`` + ``PRAGMA table_info`` (and sometimes ``ALTER TABLE`` migrations)
    on *every* store call — ``append_turn``/``list_turns``/``get_cache``/etc.
    opened a fresh connection and re-checked the schema each time. For a 50-turn
    reader chat that was 100+ redundant schema round-trips.

    ``create_fn`` must be idempotent (``CREATE TABLE IF NOT EXISTS`` /
    PRAGMA-guarded ``ALTER``), so skipping it after the first successful run is
    safe. A fresh db_path (e.g. a new temp dir in tests) gets a distinct cache
    key, so there is no cross-database contamination.
    """
    if not db_path:
        return
    if db_path in _ensured:
        return
    with _lock:
        if db_path in _ensured:
            return
        create_fn(db_path)
        _ensured.add(db_path)
