
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

class SQLiteDocumentStore:

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._ensure_tables()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
                        metadata TEXT DEFAULT '{}',
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_memories_user_type ON memories(user_id, memory_type)"
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
    ) -> str:
        import json

        memory_id = uuid.uuid4().hex
        now = time.time()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO memories (memory_id, user_id, content, memory_type, importance, properties, timestamp, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (memory_id, user_id, content, memory_type, importance, meta_json, now, now, now),
                )
                conn.commit()
            finally:
                conn.close()
        return memory_id

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock:
            conn = self._get_conn()
            try:
                cur = conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
                conn.commit()
                return cur.rowcount > 0
            finally:
                conn.close()

    def search_memories(
        self,
        user_id: str,
        memory_type: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        import json

        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    """SELECT memory_id, user_id, content, memory_type, importance, properties as metadata, created_at, updated_at
                       FROM memories
                       WHERE user_id = ? AND memory_type = ?
                       ORDER BY updated_at DESC
                       LIMIT ?""",
                    (user_id, memory_type, limit),
                ).fetchall()
                return [
                    {
                        "memory_id": r[0],
                        "user_id": r[1],
                        "content": r[2],
                        "memory_type": r[3],
                        "importance": r[4],
                        "metadata": json.loads(r[5]) if r[5] else {},
                        "created_at": r[6],
                        "updated_at": r[7],
                    }
                    for r in rows
                ]
            finally:
                conn.close()
