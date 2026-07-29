from __future__ import annotations
import datetime as _dt, sqlite3, time
from ...utils.common import exec_sql

def ensure_tables(db_path: str) -> None:
    exec_sql(db_path,
        """CREATE TABLE IF NOT EXISTS paper_reading_sessions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          paper_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
          duration_sec INTEGER NOT NULL, day_key TEXT NOT NULL, created_at INTEGER NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_prs_day ON paper_reading_sessions(day_key, created_at)",
        "CREATE INDEX IF NOT EXISTS idx_prs_paper ON paper_reading_sessions(paper_id, created_at)")
    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(paper_reading_sessions)").fetchall()]
        if "user_id" not in cols:
            conn.execute("ALTER TABLE paper_reading_sessions ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_prs_user_day ON paper_reading_sessions(user_id, day_key)")
        conn.commit()
    finally:
        conn.close()

def append_session(db_path: str, *, paper_id: int, user_id: int, duration_sec: int, client_ts: int | None = None) -> None:
    if not db_path or int(duration_sec or 0) <= 0:
        return
    ensure_tables(db_path)
    dur = min(int(duration_sec), 86400)
    ts = int(client_ts) if client_ts else int(time.time())
    day = _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("INSERT INTO paper_reading_sessions(paper_id,user_id,duration_sec,day_key,created_at) VALUES(?,?,?,?,?)",
                     (int(paper_id), int(user_id), dur, day, int(time.time())))
        conn.commit()
    finally:
        conn.close()

def list_daily_aggregate(db_path: str, *, user_id: int, days: int = 180) -> list[dict[str, int | str]]:
    if not db_path:
        return []
    ensure_tables(db_path)
    d = max(7, min(int(days or 180), 366))
    start = _dt.datetime.fromtimestamp(int(time.time()) - (d - 1) * 86400).strftime("%Y-%m-%d")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT day_key, SUM(duration_sec) AS seconds, COUNT(*) AS sessions FROM paper_reading_sessions WHERE user_id=? AND day_key>=? GROUP BY day_key ORDER BY day_key",
            (int(user_id), start)).fetchall()
        return [{"date": r["day_key"], "seconds": int(r["seconds"] or 0), "sessions": int(r["sessions"] or 0)} for r in rows]
    finally:
        conn.close()
