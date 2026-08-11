
from __future__ import annotations

import asyncio
import functools
import sqlite3
import logging
import re
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

def safe_http_500(op_name: str, exc: Exception) -> HTTPException:
    logger.exception("%s failed", op_name, exc_info=exc)
    return HTTPException(status_code=500, detail="服务暂时不可用，请稍后重试")

@contextmanager
def route_errors(op_name: str):
    """Wrap a route body: re-raise ``HTTPException`` as-is, convert anything else to a 500.

    Replaces the repeated ``except HTTPException: raise / except Exception: raise safe_http_500(...)``
    boilerplate in route handlers.
    """
    try:
        yield
    except HTTPException:
        raise
    except Exception as exc:
        raise safe_http_500(op_name, exc)

def normalize_arxiv_id(arxiv_id: str | None) -> str | None:
    if not arxiv_id:
        return None
    s = str(arxiv_id).strip()
    if not s:
        return None
    from app.core.search.normalize import strip_arxiv_version

    return strip_arxiv_version(s).lower()

def parse_llm_json(text: str) -> dict[str, Any | None]:
    from app.services.search_intent.parsing import extract_json_object

    return extract_json_object(text)

def truncate_text(text: str, max_length: int, suffix: str = "…") -> str:
    t = (text or "").strip()
    if len(t) <= max_length:
        return t
    return t[: max_length - len(suffix)] + suffix

def tokenize_for_keywords(text: str, min_len: int = 3, max_len: int = 26) -> set[str]:
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", t)
    out: set[str] = set()
    for x in t.split():
        s = x.strip()
        if min_len <= len(s) <= max_len:
            out.add(s)
    return out

def text_has_cjk(s: str) -> bool:
    return any("\u4e00" <= c <= "\u9fff" for c in s)

def dedupe_strings_preserve_order(items: list[str | None], *, max_n: int) -> list[str]:
    if not items:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for raw in items:
        t = str(raw).strip()
        if not t:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
        if len(out) >= max_n:
            break
    return out

def suppress_exceptions(default_return=None, log_level="debug", log_message=None):
    """Catch sync/async exceptions and return a default value."""
    def decorator(func):
        is_async = asyncio.iscoroutinefunction(func)
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception:
                if log_level == "warning":
                    logger.warning(log_message or f"{func.__name__} failed", exc_info=True)
                else:
                    logger.debug(log_message or f"{func.__name__} failed", exc_info=True)
                return default_return

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                if log_level == "warning":
                    logger.warning(log_message or f"{func.__name__} failed", exc_info=True)
                else:
                    logger.debug(log_message or f"{func.__name__} failed", exc_info=True)
                return default_return

        return async_wrapper if is_async else sync_wrapper
    return decorator

suppress_exceptions_async = suppress_exceptions

#: Matches ``PaperDatabase._get_connection``. The stdlib default is 5s, which a
#: slow migration or a batch save can exceed — and papers.db is shared by
#: storage, auth, export and the reader history, so a plain
#: ``sqlite3.connect(path)`` there raises "database is locked" instead of
#: waiting. Keep both the connect timeout and the PRAGMA: the former covers
#: acquiring the connection, the latter covers statements on it.
SQLITE_BUSY_TIMEOUT_SEC = 10.0


def sqlite_connect(db_path: str, *, row_factory: bool = False) -> sqlite3.Connection:
    """``sqlite3.connect`` with a sane busy timeout applied consistently."""
    conn = sqlite3.connect(db_path, timeout=SQLITE_BUSY_TIMEOUT_SEC)
    conn.execute(f"PRAGMA busy_timeout = {int(SQLITE_BUSY_TIMEOUT_SEC * 1000)}")
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def exec_sql(db_path: str, *statements: str) -> None:
    conn = sqlite_connect(db_path)
    try:
        for stmt in statements:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()

def build_in_clause(column: str, values: list[Any]) -> tuple[str, tuple[Any, ...]]:
    if not values:
        return f"{column} IN (NULL)", ()
    placeholders = ",".join(["?"] * len(values))
    return f"{column} IN ({placeholders})", tuple(values)
