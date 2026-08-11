"""Modules sharing papers.db must connect with a busy timeout.

Regression: only ``PaperDatabase._get_connection`` passed ``timeout=`` /
``PRAGMA busy_timeout``. Every other module opened the same file with a bare
``sqlite3.connect(path)``, which uses the stdlib default of 5s. papers.db is
shared by storage, auth, export, the KG relation repo and the reader chat
history, so once any writer held the lock for >5s (a migration, a batch save,
an export) those callers raised "database is locked" instead of waiting.
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import pytest

from app.utils.common import SQLITE_BUSY_TIMEOUT_SEC, sqlite_connect


@pytest.fixture()
def db_path() -> str:
    path = str(Path(tempfile.mkdtemp()) / "t.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, v TEXT)")
    conn.commit()
    conn.close()
    return path


def test_busy_timeout_pragma_is_applied(db_path: str) -> None:
    conn = sqlite_connect(db_path)
    try:
        got = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert got == int(SQLITE_BUSY_TIMEOUT_SEC * 1000)
        assert got > 5000, "must exceed the 5s stdlib default this bug relied on"
    finally:
        conn.close()


def test_row_factory_opt_in(db_path: str) -> None:
    plain = sqlite_connect(db_path)
    rows = sqlite_connect(db_path, row_factory=True)
    try:
        assert plain.row_factory is None
        assert rows.row_factory is sqlite3.Row
        rows.execute("INSERT INTO t(v) VALUES('a')")
        assert rows.execute("SELECT v FROM t").fetchone()["v"] == "a"
    finally:
        plain.close()
        rows.close()


def test_waits_out_a_lock_held_past_the_stdlib_default(db_path: str) -> None:
    """The exact scenario that used to raise OperationalError."""
    hold_sec = 6.0  # > 5s stdlib default, < our 10s timeout
    result: dict[str, str] = {}

    def holder() -> None:
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT INTO t(v) VALUES('holder')")
        time.sleep(hold_sec)
        conn.commit()
        conn.close()

    def latecomer() -> None:
        time.sleep(0.3)
        try:
            conn = sqlite_connect(db_path)
            conn.execute("INSERT INTO t(v) VALUES('late')")
            conn.commit()
            conn.close()
            result["late"] = "ok"
        except Exception as exc:  # pragma: no cover - failure path
            result["late"] = f"{type(exc).__name__}: {exc}"

    threads = [threading.Thread(target=holder), threading.Thread(target=latecomer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert result["late"] == "ok", "writer should wait for the lock, not fail"


def test_exec_sql_uses_the_shared_helper() -> None:
    """exec_sql is the shared DDL/migration path; it must not regress to a
    bare sqlite3.connect()."""
    import inspect

    from app.utils import common

    src = inspect.getsource(common.exec_sql)
    assert "sqlite_connect(" in src
    assert "sqlite3.connect(" not in src


@pytest.mark.parametrize(
    "module_path",
    [
        "app.api.repo",
        "app.api.routes.export",
        "app.services.reader.paper_reader_history",
    ],
)
def test_papers_db_modules_do_not_use_bare_connect(module_path: str) -> None:
    """These all open papers.db and must go through the timeout-aware helper."""
    import importlib

    mod = importlib.import_module(module_path)
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "sqlite3.connect(" not in src, (
        f"{module_path} opens papers.db with a bare sqlite3.connect(); use "
        "sqlite_connect() so it waits for the lock instead of failing"
    )
