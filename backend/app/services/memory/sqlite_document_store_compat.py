from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# Column list shared by every memories-table query (see _select_memories).
# ``deleted_at`` (Karpathy-style soft delete) is the 13th column: 0 = live,
# >0 = epoch seconds when the memory was retired. Reads filter ``deleted_at=0``
# by default; pass ``include_deleted=True`` to ``_select_memories`` for the
# knowledge-base admin view.
_MEMORY_COLUMNS = (
    "memory_id, user_id, content, memory_type, importance, properties, "
    "timestamp, created_at, updated_at, utility, access_count, last_used, deleted_at"
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
                        last_used REAL DEFAULT 0,
                        deleted_at REAL DEFAULT 0
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
                # Karpathy-style soft delete: retired memories keep their row
                # (recoverable, surfaces in KB admin view) but are filtered out
                # of every default read. ``purge_deleted`` reaps them after a TTL.
                if "deleted_at" not in columns:
                    conn.execute("ALTER TABLE memories ADD COLUMN deleted_at REAL DEFAULT 0")
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
                        timestamp, created_at, updated_at, utility, access_count, last_used, deleted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                       ON CONFLICT(memory_id) DO UPDATE SET
                         user_id = excluded.user_id,
                         content = excluded.content,
                         memory_type = excluded.memory_type,
                         importance = excluded.importance,
                         properties = excluded.properties,
                         timestamp = excluded.timestamp,
                         updated_at = excluded.updated_at,
                         deleted_at = 0,
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
        """Soft-delete: mark ``deleted_at`` instead of removing the row.

        Karpathy's append-and-review never truly discards a note — it sinks.
        A retired memory stays recoverable (KB admin view, evolution-log
        traceability) and is reaped only by ``purge_deleted`` after a TTL.
        The embedding vector is dropped immediately (no point recomputing
        recall for a retired memory); links are left in place and naturally
        filtered because both endpoints become invisible to default reads.
        Returns True if a live row was retired (idempotent on already-deleted).
        """
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE memories SET deleted_at = ?, updated_at = ? "
                    "WHERE memory_id = ? AND deleted_at = 0",
                    (time.time(), time.time(), memory_id),
                )
                if cur.rowcount > 0:
                    conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def archive_with_summary(
        self, docs: list[dict[str, Any]], *, user_id: str, content: str,
        properties: dict[str, Any],
    ) -> str:
        """Persist a summary and retire its unchanged inputs atomically.

        The model runs outside the transaction. Recheck its source snapshots
        under a write lock so a concurrent edit cannot be lost to an obsolete
        summary. Any insert/archive/log failure rolls back the entire batch.
        """
        originals = {str(d["memory_id"]): str(d.get("content") or "") for d in docs}
        if not originals or not content.strip():
            return ""
        ids = list(originals)
        placeholders = ",".join("?" * len(ids))
        now, mid = time.time(), uuid.uuid4().hex
        props = dict(properties, compressed_memory_ids=ids)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    f"SELECT memory_id, content, utility, importance, access_count FROM memories "
                    f"WHERE memory_id IN ({placeholders}) AND user_id = ? "
                    "AND memory_type = 'working' AND deleted_at = 0",
                    (*ids, user_id),
                ).fetchall()
                if {str(r[0]): str(r[1]) for r in rows} != originals:
                    conn.rollback()
                    return ""
                conn.execute(
                    "INSERT INTO memories (memory_id, user_id, content, memory_type, importance, "
                    "properties, timestamp, created_at, updated_at) VALUES (?, ?, ?, 'long', 0.6, ?, ?, ?, ?)",
                    (mid, user_id, content, json.dumps(props, ensure_ascii=False), now, now, now),
                )
                conn.execute(
                    f"UPDATE memories SET deleted_at = ?, updated_at = ? WHERE memory_id IN ({placeholders})",
                    (now, now, *ids),
                )
                conn.execute(f"DELETE FROM memory_embeddings WHERE memory_id IN ({placeholders})", ids)
                conn.executemany(
                    "INSERT INTO memory_evolution_log (memory_id, event, utility_after, importance_after, "
                    "access_count_after, reason, created_at) VALUES (?, 'prune', ?, ?, ?, ?, ?)",
                    [(r[0], r[2], r[3], r[4], "working 压缩为长期记忆", now) for r in rows],
                )
                conn.commit()
                return mid
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def purge_deleted(self, before_days: int = 30) -> int:
        """Hard-delete memories soft-deleted more than ``before_days`` ago.

        Last-resort reaping to bound DB growth — Karpathy's review never
        prunes, but a completely unbounded store is impractical. Runs in the
        evolution loop tail, never in a user-facing path. Returns count purged.
        """
        cutoff = time.time() - max(1, int(before_days)) * 86400
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT memory_id FROM memories WHERE deleted_at > 0 AND deleted_at < ?",
                    (cutoff,),
                ).fetchall()
                if not rows:
                    return 0
                ids = [str(r[0]) for r in rows]
                # SQLite has a compile-time limit on bound variables
                # (SQLITE_MAX_VARIABLE_NUMBER, historically 999 / 32766).
                # The memory_links query uses 2× len(ids), so batch to stay
                # well within the limit.
                PURGE_BATCH = 500
                for i in range(0, len(ids), PURGE_BATCH):
                    batch = ids[i:i + PURGE_BATCH]
                    placeholders = ",".join("?" * len(batch))
                    conn.execute(
                        f"DELETE FROM memories WHERE memory_id IN ({placeholders})",
                        batch,
                    )
                    conn.execute(
                        f"DELETE FROM memory_embeddings WHERE memory_id IN ({placeholders})",
                        batch,
                    )
                    conn.execute(
                        f"DELETE FROM memory_links WHERE src IN ({placeholders}) "
                        f"OR dst IN ({placeholders})",
                        (*batch, *batch),
                    )
                conn.commit()
                return len(ids)
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
        """Bidirectional 1-hop lookup: for each id, its linked neighbor ids.

        Retired (soft-deleted) neighbors are filtered out — link expansion
        must never surface a memory that's been retired from default reads.
        """
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
                # JOIN memories twice to drop links whose endpoint is retired.
                rows = conn.execute(
                    f"""SELECT l.src, l.dst FROM memory_links l
                        JOIN memories ms ON ms.memory_id = l.src AND ms.deleted_at = 0
                        JOIN memories md ON md.memory_id = l.dst AND md.deleted_at = 0
                        WHERE {src_clause} OR {dst_clause}""",
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
        limit: int | None = 10,
        *,
        include_deleted: bool = False,
        oldest_first: bool = False,
    ) -> List[Dict[str, Any]]:
        where = "WHERE user_id = ?"
        params: list[Any] = [user_id]
        if memory_type:
            where += " AND memory_type = ?"
            params.append(memory_type)
        return self._select_memories(
            where, params, order_by=("timestamp ASC, updated_at ASC" if oldest_first else "timestamp DESC, updated_at DESC"), limit=limit,
            include_deleted=include_deleted,
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
            "deleted_at": row[12] if len(row) > 12 else 0.0,
        }

    def _select_memories(
        self,
        where: str,
        params: list[Any],
        *,
        order_by: str = "",
        limit: int | None = None,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        """Single chokepoint for memories-table reads.

        ``order_by`` is always an internal constant (never user input), so
        interpolating it is safe; values go through ``params`` as usual.
        By default retired memories (``deleted_at > 0``) are filtered out;
        the KB admin view passes ``include_deleted=True`` to surface them.
        """
        # Soft-delete filter: default reads exclude retired rows. Handle the
        # empty-WHERE case (list_all_memories with no memory_type) so we emit
        # "WHERE deleted_at = 0" rather than a dangling "AND".
        if include_deleted:
            deleted_clause = ""
        elif where:
            deleted_clause = " AND deleted_at = 0"
        else:
            deleted_clause = "WHERE deleted_at = 0"
        sql = f"SELECT {_MEMORY_COLUMNS} FROM memories {where}{deleted_clause}"
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

    def list_all_memories(
        self, *, memory_type: str = "", limit: int | None = 1000, include_deleted: bool = False,
        scope: str = "all", paper_id: int | None = None, sort: str = "recency",
    ) -> List[Dict[str, Any]]:
        """List memories across all user scopes (knowledge-base page)."""
        where, params = self._memory_filters(scope=scope, paper_id=paper_id, memory_type=memory_type)
        column = {"recency": "timestamp", "access": "access_count", "utility": "utility", "importance": "importance"}[sort]
        default = 0.5 if sort == "importance" else 0
        return self._select_memories(
            where, params, order_by=f"COALESCE({column}, {default}) DESC, timestamp DESC, memory_id DESC",
            limit=limit, include_deleted=include_deleted,
        )

    @staticmethod
    def _memory_filters(*, scope: str = "all", paper_id: int | None = None, memory_type: str = "") -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if scope == "global":
            clauses.append("user_id = 'papergraph:global'")
        elif scope == "paper":
            if paper_id is None:
                clauses.append("user_id LIKE 'papergraph:paper:%'")
            else:
                clauses.append("user_id = ?")
                params.append(f"papergraph:paper:{int(paper_id)}")
        elif scope == "agent":
            clauses.append("user_id != 'papergraph:global' AND user_id NOT LIKE 'papergraph:paper:%'")
        elif scope != "all":
            raise ValueError(f"Unknown memory scope: {scope}")
        return ("WHERE " + " AND ".join(clauses) if clauses else ""), params

    def count_memories(self, *, scope: str = "all", paper_id: int | None = None, memory_type: str = "") -> int:
        where, params = self._memory_filters(scope=scope, paper_id=paper_id, memory_type=memory_type)
        where += " AND deleted_at = 0" if where else "WHERE deleted_at = 0"
        with self._lock:
            conn = self._get_conn()
            try:
                return int(conn.execute(f"SELECT COUNT(*) FROM memories {where}", params).fetchone()[0])
            finally:
                conn.close()

    def memory_statistics(self) -> Dict[str, Any]:
        """Aggregate every live memory without loading/truncating content."""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*), COALESCE(SUM(utility), 0), COALESCE(SUM(access_count), 0), "
                    "COALESCE(SUM(utility < -0.05), 0), "
                    "COALESCE(SUM(COALESCE(utility, 0) BETWEEN -0.05 AND 0.05), 0), "
                    "COALESCE(SUM(utility > 0.05), 0) FROM memories WHERE deleted_at = 0"
                ).fetchone()
                kinds = conn.execute(
                    "SELECT memory_type, COUNT(*) FROM memories WHERE deleted_at = 0 GROUP BY memory_type"
                ).fetchall()
            finally:
                conn.close()
        return {
            "total": int(row[0]), "utility_sum": float(row[1]), "total_accesses": int(row[2]),
            "by_kind": {str(kind): int(n) for kind, n in kinds},
            "utility_distribution": dict(zip(("negative", "neutral", "positive"), map(int, row[3:]))),
        }

    def evolution_day_counts(self, day_ranges: list[tuple[str, float, float]]) -> Dict[str, int]:
        """Count timestamp ranges in SQL; callers supply timezone-aware days."""
        with self._lock:
            conn = self._get_conn()
            try:
                return {
                    label: int(conn.execute(
                        "SELECT COUNT(*) FROM memory_evolution_log WHERE created_at >= ? AND created_at < ?",
                        (start, end),
                    ).fetchone()[0])
                    for label, start, end in day_ranges
                }
            finally:
                conn.close()

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

    def judge_scorecard(self, *, lookback_days: int = 30) -> Dict[str, Any]:
        """Self-iteration signal: how did past judge decisions turn out?

        Joins decision events (rescue/reflection/prune) against later outcome
        events (feedback_up/citation_click/engaged_read) on the same memory.
        Returns counts AND rates — the judge prompt shows both so the model
        doesn't overreact to small N (single-user traffic is low). All signals
        come from ``memory_evolution_log`` (self-contained, no memories-table
        join), so the scorecard is cheap and replayable.

        - ``rescue``: rescued memories that later drew a positive outcome event.
        - ``reflection``: meta-memories that later drew a positive outcome event.
        - ``prune_of_accessed``: pruned memories that had been accessed before
          retirement (access_count_after > 0) — a potential over-pruning signal.
        """
        cutoff = time.time() - max(1, int(lookback_days)) * 86400
        positive = ("feedback_up", "citation_click", "engaged_read")
        with self._lock:
            conn = self._get_conn()
            try:
                # Distinct memory_ids that drew a positive outcome in the window.
                pos_rows = conn.execute(
                    "SELECT DISTINCT memory_id FROM memory_evolution_log "
                    f"WHERE event IN ({','.join('?' * len(positive))}) AND created_at >= ?",
                    (*positive, cutoff),
                ).fetchall()
                positive_mids = {str(r[0]) for r in pos_rows}

                def _hit_rate(event: str) -> tuple[int, int]:
                    rows = conn.execute(
                        "SELECT memory_id FROM memory_evolution_log "
                        "WHERE event = ? AND created_at >= ?",
                        (event, cutoff),
                    ).fetchall()
                    mids = [str(r[0]) for r in rows]
                    hits = sum(1 for m in mids if m in positive_mids)
                    return hits, len(mids)

                rescue_hits, rescue_total = _hit_rate("rescue")
                refl_hits, refl_total = _hit_rate("reflection")
                prune_rows = conn.execute(
                    "SELECT access_count_after FROM memory_evolution_log "
                    "WHERE event = 'prune' AND created_at >= ?",
                    (cutoff,),
                ).fetchall()
                prune_total = len(prune_rows)
                prune_of_accessed = sum(1 for (acc,) in prune_rows if int(acc or 0) > 0)
            finally:
                conn.close()

        def _rate(num: int, den: int) -> float:
            return round(num / den, 2) if den > 0 else 0.0

        return {
            "rescue": {"hits": rescue_hits, "total": rescue_total, "rate": _rate(rescue_hits, rescue_total)},
            "reflection": {"hits": refl_hits, "total": refl_total, "rate": _rate(refl_hits, refl_total)},
            "prune_of_accessed": {"count": prune_of_accessed, "total": prune_total, "rate": _rate(prune_of_accessed, prune_total)},
            "lookback_days": int(lookback_days),
        }

    def list_stale_memories(
        self,
        *,
        min_age_days: int,
        exclude_types: tuple[str, ...] = (),
        limit: int = 200,
        include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        """Gather candidates for the periodic LLM evolution review.

        This SQL filter is candidate *gathering* (instrumentation-based:
        age + never retrieved), not scoring — the keep/merge/prune decision
        itself is made by the LLM judge. Stale candidates are always live
        (``deleted_at=0``) unless ``include_deleted`` is set.
        """
        cutoff = time.time() - max(1, int(min_age_days)) * 86400
        where = "WHERE timestamp < ? AND access_count = 0"
        params: list[Any] = [cutoff]
        if exclude_types:
            where += f" AND memory_type NOT IN ({','.join('?' * len(exclude_types))})"
            params.extend(exclude_types)
        return self._select_memories(
            where, params, order_by="timestamp ASC", limit=limit, include_deleted=include_deleted,
        )

    def touch_last_used(self, memory_id: str) -> bool:
        """Bump ``last_used`` to now without incrementing access_count.

        Implements Karpathy's "rescue to the top": a review that decides a
        forgotten memory still deserves attention floats it above its
        creation recency via the attention sort (``attention_key`` uses
        ``last_used``). Unlike ``increment_access`` (which records a real
        injection), this is a review decision — it repositions but doesn't
        claim the memory was used. Returns True if a live row was touched.
        """
        now = time.time()
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute(
                    "UPDATE memories SET last_used = ?, updated_at = ? "
                    "WHERE memory_id = ? AND deleted_at = 0",
                    (now, now, memory_id),
                )
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

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

    def get_memories_by_ids(
        self, memory_ids: list[str], *, include_deleted: bool = False,
    ) -> List[Dict[str, Any]]:
        ids = [str(m) for m in (memory_ids or []) if m]
        if not ids:
            return []
        from ...utils.common import build_in_clause

        in_clause, in_params = build_in_clause("memory_id", ids)
        return self._select_memories(
            f"WHERE {in_clause}", list(in_params), include_deleted=include_deleted,
        )

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
                        WHERE {in_clause} AND deleted_at = 0""",
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
                    "SELECT utility, importance, access_count FROM memories "
                    "WHERE memory_id = ? AND deleted_at = 0",
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
