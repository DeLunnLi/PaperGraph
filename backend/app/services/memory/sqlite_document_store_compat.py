from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# Column list shared by every memories-table query (see _select_memories).
_MEMORY_COLUMNS = (
    "memory_id, user_id, content, memory_type, importance, properties, "
    "timestamp, created_at, updated_at, utility, access_count, last_used"
)


class SQLiteDocumentStore:
    """Small compatibility store for the subset of hello-agents memory APIs we use.

    Self-evolving memory extension: the ``memories`` table carries
    ``utility`` / ``access_count`` / ``last_used`` instrumentation columns and
    every DB also gets a ``memory_evolution_log`` sidecar table recording why
    a memory's utility/importance changed or why it was pruned. Both memory
    DBs (reader_memory.db, agent_memory.db) instantiate this class, so the
    PRAGMA-guarded ALTERs below migrate both automatically.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        # Schema DDL is idempotent — run once per db_path per process instead of
        # on every per-request instantiation (reader chat creates several stores
        # per turn; ensure_once is the codebase-wide pattern for this).
        from ...utils.db_init import ensure_once

        ensure_once(db_path, lambda _p: self._ensure_tables())

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_tables(self) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS memories (
                        memory_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        content TEXT NOT NULL,
                        memory_type TEXT NOT NULL DEFAULT 'working',
                        importance REAL DEFAULT 0.5,
                        properties TEXT DEFAULT '{}',
                        timestamp REAL NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        utility REAL DEFAULT 0.0,
                        access_count INTEGER DEFAULT 0,
                        last_used REAL DEFAULT 0
                    )"""
                )
                columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(memories)")}
                if "properties" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN properties TEXT DEFAULT '{}'")
                    if "metadata" in columns:
                        conn.execute("UPDATE memories SET properties = metadata WHERE metadata IS NOT NULL")
                if "timestamp" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN timestamp REAL DEFAULT 0")
                    conn.execute("UPDATE memories SET timestamp = COALESCE(updated_at, created_at, 0)")
                # Self-evolving memory instrumentation columns
                if "utility" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN utility REAL DEFAULT 0.0")
                if "access_count" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0")
                if "last_used" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN last_used REAL DEFAULT 0")
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_user_type ON memories(user_id, memory_type)"
                )
                # Sidecar log of every evolution event (feedback, access, decay,
                # prune, merge) for the knowledge-base page timeline.
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS memory_evolution_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        memory_id TEXT NOT NULL,
                        event TEXT NOT NULL,
                        utility_delta REAL DEFAULT 0.0,
                        utility_after REAL DEFAULT 0.0,
                        importance_after REAL DEFAULT 0.0,
                        access_count_after INTEGER DEFAULT 0,
                        reason TEXT,
                        created_at REAL NOT NULL
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evolution_log_time ON memory_evolution_log(created_at DESC)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_evolution_log_memory ON memory_evolution_log(memory_id)"
                )
                # Sidecar embedding vectors for semantic recall (one per memory,
                # lazily populated; invalidated when content changes).
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS memory_embeddings (
                        memory_id TEXT PRIMARY KEY,
                        vector TEXT NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
                # A-MEM-style associative links between memories (directed
                # storage, queried bidirectionally).
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS memory_links (
                        src TEXT NOT NULL,
                        dst TEXT NOT NULL,
                        reason TEXT,
                        created_at REAL NOT NULL,
                        PRIMARY KEY (src, dst)
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memory_links_dst ON memory_links(dst)"
                )
                conn.commit()
            finally:
                conn.close()

    def add_memory(
        self,
        user_id: str,
        content: str,
        memory_type: str = "working",
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        memory_id: str | None = None,
        timestamp: float | None = None,
        properties: Optional[Dict[str, Any]] = None,
        utility: float | None = None,
        access_count: int | None = None,
        last_used: float | None = None,
    ) -> str:
        """Insert a memory, or update it when ``memory_id`` already exists.

        On conflict, content/importance/properties/timestamp are replaced but
        the evolution stats (utility/access_count/last_used) are preserved
        unless explicitly passed — dedup re-adds and singleton upserts must
        not wipe accumulated feedback.
        """
        mid = str(memory_id or uuid.uuid4().hex)
        now = time.time()
        event_time = float(timestamp if timestamp is not None else now)
        values = properties if properties is not None else metadata
        props_json = json.dumps(values or {}, ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            try:
                old = conn.execute(
                    "SELECT content FROM memories WHERE memory_id = ?", (mid,)
                ).fetchone()
                if old and str(old[0]) != content:
                    # Content changed (dedup merge / supersede) — the cached
                    # embedding is stale; drop it so recall recomputes lazily.
                    conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (mid,))
                conn.execute(
                    """INSERT INTO memories
                       (memory_id, user_id, content, memory_type, importance, properties,
                        timestamp, created_at, updated_at, utility, access_count, last_used)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(memory_id) DO UPDATE SET
                         user_id = excluded.user_id,
                         content = excluded.content,
                         memory_type = excluded.memory_type,
                         importance = excluded.importance,
                         properties = excluded.properties,
                         timestamp = excluded.timestamp,
                         updated_at = excluded.updated_at,
                         utility = CASE WHEN ? IS NOT NULL THEN ? ELSE memories.utility END,
                         access_count = CASE WHEN ? IS NOT NULL THEN ? ELSE memories.access_count END,
                         last_used = CASE WHEN ? IS NOT NULL THEN ? ELSE memories.last_used END""",
                    (
                        mid, user_id, content, memory_type, importance, props_json,
                        event_time, now, now,
                        utility if utility is not None else 0.0,
                        access_count if access_count is not None else 0,
                        last_used if last_used is not None else 0.0,
                        utility, utility,
                        access_count, access_count,
                        last_used, last_used,
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        return mid

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
                conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
                conn.execute(
                    "DELETE FROM memory_links WHERE src = ? OR dst = ?", (memory_id, memory_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    # ---- associative links (A-MEM-style) ----

    def add_links(self, src: str, dst_ids: list[str], *, reason: str = "") -> int:
        src = str(src or "")
        ids = [str(d) for d in (dst_ids or []) if d and str(d) != src]
        if not src or not ids:
            return 0
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            try:
                for dst in ids:
                    conn.execute(
                        "INSERT OR IGNORE INTO memory_links (src, dst, reason, created_at) VALUES (?, ?, ?, ?)",
                        (src, dst, reason, now),
                    )
                conn.commit()
                return len(ids)
            finally:
                conn.close()

    def linked_ids(self, memory_ids: list[str]) -> Dict[str, list[str]]:
        """Bidirectional 1-hop lookup: for each id, its linked neighbor ids."""
        ids = [str(m) for m in (memory_ids or []) if m]
        if not ids:
            return {}
        from ...utils.common import build_in_clause

        src_clause, src_params = build_in_clause("src", ids)
        dst_clause, dst_params = build_in_clause("dst", ids)
        out: Dict[str, list[str]] = {m: [] for m in ids}
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    f"SELECT src, dst FROM memory_links WHERE {src_clause} OR {dst_clause}",
                    (*src_params, *dst_params),
                ).fetchall()
            finally:
                conn.close()
        for src, dst in rows:
            src, dst = str(src), str(dst)
            if src in out and dst not in out[src]:
                out[src].append(dst)
            if dst in out and src not in out[dst]:
                out[dst].append(src)
        return {k: v for k, v in out.items() if v}

    # ---- embedding sidecar (semantic recall) ----

    def upsert_embedding(self, memory_id: str, vector: list[float]) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO memory_embeddings (memory_id, vector, updated_at) VALUES (?, ?, ?)",
                    (str(memory_id), json.dumps(vector), time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def list_embeddings(self) -> Dict[str, list[float]]:
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute("SELECT memory_id, vector FROM memory_embeddings").fetchall()
            finally:
                conn.close()
        out: Dict[str, list[float]] = {}
        for mid, raw in rows:
            try:
                vec = json.loads(raw)
                if isinstance(vec, list) and vec:
                    out[str(mid)] = [float(v) for v in vec]
            except (TypeError, json.JSONDecodeError, ValueError):
                continue
        return out

    def search_memories(
        self,
        user_id: str,
        memory_type: str = "",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        where = "WHERE user_id = ?"
        params: list[Any] = [user_id]
        if memory_type:
            where += " AND memory_type = ?"
            params.append(memory_type)
        return self._select_memories(
            where, params, order_by="timestamp DESC, updated_at DESC", limit=limit,
        )

    @staticmethod
    def _row_to_doc(row: Any) -> Dict[str, Any]:
        try:
            props = json.loads(row[5]) if row[5] else {}
        except (TypeError, json.JSONDecodeError):
            props = {}
        return {
            "memory_id": row[0],
            "user_id": row[1],
            "content": row[2],
            "memory_type": row[3],
            "importance": row[4],
            "properties": props,
            "metadata": props,
            "timestamp": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "utility": row[9] if len(row) > 9 else 0.0,
            "access_count": row[10] if len(row) > 10 else 0,
            "last_used": row[11] if len(row) > 11 else 0.0,
        }

    def _select_memories(
        self,
        where: str,
        params: list[Any],
        *,
        order_by: str = "",
        limit: int | None = None,
    ) -> List[Dict[str, Any]]:
        """Single chokepoint for memories-table reads.

        ``order_by`` is always an internal constant (never user input), so
        interpolating it is safe; values go through ``params`` as usual.
        """
        sql = f"SELECT {_MEMORY_COLUMNS} FROM memories {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        final_params: list[Any] = list(params)
        if limit is not None:
            sql += " LIMIT ?"
            final_params.append(max(1, int(limit)))
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(sql, final_params).fetchall()
            finally:
                conn.close()
        return [self._row_to_doc(row) for row in rows]

    def list_all_memories(self, *, memory_type: str = "", limit: int = 1000) -> List[Dict[str, Any]]:
        """List memories across all user scopes (knowledge-base page)."""
        where = "WHERE memory_type = ?" if memory_type else ""
        params: list[Any] = [memory_type] if memory_type else []
        return self._select_memories(where, params, order_by="timestamp DESC", limit=limit)

    def count_evolution_events_since(self, since_ts: float) -> int:
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM memory_evolution_log WHERE created_at >= ?",
                    (float(since_ts),),
                ).fetchone()
                return int(row[0] or 0) if row else 0
            finally:
                conn.close()

    def evolution_event_counts(self, *, since_ts: float | None = None) -> Dict[str, int]:
        """Aggregate evolution events by type (knowledge-base health metrics)."""
        where = "WHERE created_at >= ?" if since_ts is not None else ""
        params: list[Any] = [float(since_ts)] if since_ts is not None else []
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    f"SELECT event, COUNT(*) FROM memory_evolution_log {where} GROUP BY event",
                    params,
                ).fetchall()
            finally:
                conn.close()
        return {str(ev): int(n) for ev, n in rows}

    def list_stale_memories(
        self,
        *,
        min_age_days: int,
        exclude_types: tuple[str, ...] = (),
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """Gather candidates for the periodic LLM evolution review.

        This SQL filter is candidate *gathering* (instrumentation-based:
        age + never retrieved), not scoring — the keep/merge/prune decision
        itself is made by the LLM judge.
        """
        cutoff = time.time() - max(1, int(min_age_days)) * 86400
        where = "WHERE timestamp < ? AND access_count = 0"
        params: list[Any] = [cutoff]
        if exclude_types:
            where += f" AND memory_type NOT IN ({','.join('?' * len(exclude_types))})"
            params.extend(exclude_types)
        return self._select_memories(where, params, order_by="timestamp ASC", limit=limit)

    def update_importance_utility(
        self,
        memory_id: str,
        *,
        importance: float | None = None,
        utility: float | None = None,
    ) -> bool:
        sets: list[str] = ["updated_at = ?"]
        params: list[Any] = [time.time()]
        if importance is not None:
            sets.append("importance = ?")
            params.append(max(0.0, min(1.0, float(importance))))
        if utility is not None:
            from .policy import clamp_utility

            sets.append("utility = ?")
            params.append(clamp_utility(float(utility)))
        params.append(str(memory_id))
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    f"UPDATE memories SET {', '.join(sets)} WHERE memory_id = ?",
                    params,
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def get_memories_by_ids(self, memory_ids: list[str]) -> List[Dict[str, Any]]:
        ids = [str(m) for m in (memory_ids or []) if m]
        if not ids:
            return []
        from ...utils.common import build_in_clause

        in_clause, in_params = build_in_clause("memory_id", ids)
        return self._select_memories(f"WHERE {in_clause}", list(in_params))

    def increment_access(self, memory_ids: list[str]) -> int:
        """Batch-increment access_count and refresh last_used for injected memories.

        Only memories actually injected into a prompt should be passed here —
        this is the implicit "was useful enough to retrieve" signal, not a
        scoring strategy.
        """
        ids = [str(m) for m in (memory_ids or []) if m]
        if not ids:
            return 0
        from ...utils.common import build_in_clause

        in_clause, in_params = build_in_clause("memory_id", ids)
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    f"""UPDATE memories
                        SET access_count = access_count + 1,
                            last_used = ?,
                            updated_at = ?
                        WHERE {in_clause}""",
                    (now, now, *in_params),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def adjust_utility(self, memory_id: str, *, delta: float, event: str = "feedback", reason: str = "") -> bool:
        """Apply a utility delta (clamped to [-2, 5]) and log the evolution event."""
        mid = str(memory_id or "")
        if not mid:
            return False
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT utility, importance, access_count FROM memories WHERE memory_id = ?",
                    (mid,),
                ).fetchone()
                if not row:
                    return False
                old_util, imp, acc = float(row[0] or 0.0), float(row[1] or 0.5), int(row[2] or 0)
                from .policy import clamp_utility

                new_util = clamp_utility(old_util + float(delta))
                conn.execute(
                    "UPDATE memories SET utility = ?, updated_at = ? WHERE memory_id = ?",
                    (new_util, now, mid),
                )
                conn.execute(
                    """INSERT INTO memory_evolution_log
                       (memory_id, event, utility_delta, utility_after, importance_after,
                        access_count_after, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (mid, event, new_util - old_util, new_util, imp, acc, reason, now),
                )
                conn.commit()
                return True
            finally:
                conn.close()

    def log_evolution(
        self,
        memory_id: str,
        event: str,
        *,
        utility_delta: float = 0.0,
        utility_after: float = 0.0,
        importance_after: float = 0.0,
        access_count_after: int = 0,
        reason: str = "",
    ) -> None:
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT INTO memory_evolution_log
                       (memory_id, event, utility_delta, utility_after, importance_after,
                        access_count_after, reason, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (memory_id, event, float(utility_delta), float(utility_after),
                     float(importance_after), int(access_count_after), reason, time.time()),
                )
                conn.commit()
            finally:
                conn.close()

    def list_evolution_log(
        self,
        *,
        limit: int = 50,
        memory_id: str | None = None,
    ) -> List[Dict[str, Any]]:
        where = "WHERE memory_id = ?" if memory_id else ""
        params: list[Any] = [memory_id] if memory_id else []
        params.extend([max(1, int(limit)), 0])
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    f"""SELECT id, memory_id, event, utility_delta, utility_after,
                               importance_after, access_count_after, reason, created_at
                        FROM memory_evolution_log {where}
                        ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                    params,
                ).fetchall()
            finally:
                conn.close()
        return [
            {
                "id": r[0],
                "memory_id": r[1],
                "event": r[2],
                "utility_delta": r[3],
                "utility_after": r[4],
                "importance_after": r[5],
                "access_count_after": r[6],
                "reason": r[7] or "",
                "created_at": r[8],
            }
            for r in rows
        ]
