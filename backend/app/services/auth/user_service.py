"""User authentication: registration, login, JWT token management.

Simple JWT-based auth with bcrypt password hashing.
Users table stored in the same papers.db SQLite.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import time
from typing import Any


def _get_db_path() -> str:
    from ...settings import get_settings
    return os.path.join(get_settings().data_dir, "papers.db")


def _get_jwt_secret() -> str:
    from ...settings import get_settings
    secret = os.getenv("PAPERGRAPH_JWT_SECRET", "").strip()
    if len(secret) < 32:
        raise RuntimeError("PAPERGRAPH_JWT_SECRET 必须配置且至少包含 32 个字符")
    return secret


JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72
PDF_TICKET_EXPIRY_SEC = 300


def _ensure_users_table(conn: sqlite3.Connection) -> None:
    """Create auth users while preserving a legacy hello-agents users table."""
    existing = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    if existing:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(users)")}
        if not {"username", "password_hash"}.issubset(columns):
            suffix = 0
            legacy_name = "legacy_memory_users"
            while conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (legacy_name,)
            ).fetchone():
                suffix += 1
                legacy_name = f"legacy_memory_users_{suffix}"
            # SQLite updates inbound foreign-key targets when renaming a table.
            conn.execute(f'ALTER TABLE users RENAME TO "{legacy_name}"')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)")
    conn.commit()


def _hash_password(password: str) -> str:
    import bcrypt
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, password_hash: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def _create_jwt(user_id: int, username: str) -> str:
    """Create a JWT token (header.payload.signature, HS256, no external deps)."""
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "username": username,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
    }

    def _b64(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    h = _b64(header)
    p = _b64(payload)
    signature = hmac.new(
        _get_jwt_secret().encode("utf-8"),
        f"{h}.{p}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return f"{h}.{p}.{sig}"


def create_pdf_access_ticket(*, user_id: int, paper_id: int, expires_in: int = PDF_TICKET_EXPIRY_SEC) -> str:
    """Create a short-lived, paper-scoped ticket for PDF.js Range requests."""
    exp = int(time.time()) + max(30, min(900, int(expires_in)))
    payload = f"pdf:{int(user_id)}:{int(paper_id)}:{exp}"
    signature = hmac.new(_get_jwt_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    sig = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    raw = f"{payload}:{sig}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def verify_pdf_access_ticket(ticket: str, *, paper_id: int) -> dict[str, int] | None:
    """Verify a short-lived PDF ticket and bind it to the requested paper."""
    try:
        padded = str(ticket or "") + "=" * (-len(str(ticket or "")) % 4)
        raw = base64.urlsafe_b64decode(padded).decode("utf-8")
        purpose, user_raw, paper_raw, exp_raw, sig = raw.split(":", 4)
        if purpose != "pdf" or int(paper_raw) != int(paper_id) or int(exp_raw) < int(time.time()):
            return None
        payload = f"{purpose}:{int(user_raw)}:{int(paper_raw)}:{int(exp_raw)}"
        expected = hmac.new(_get_jwt_secret().encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
        expected_sig = base64.urlsafe_b64encode(expected).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(sig, expected_sig):
            return None
        return {"user_id": int(user_raw), "paper_id": int(paper_raw), "exp": int(exp_raw)}
    except Exception:
        return None


def _verify_jwt(token: str) -> dict[str, Any] | None:
    """Verify a JWT token, return payload dict or None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h, p, sig = parts
    expected_raw = hmac.new(
        _get_jwt_secret().encode("utf-8"),
        f"{h}.{p}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_sig = base64.urlsafe_b64encode(expected_raw).rstrip(b"=").decode("ascii")
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        padded = p + "=" * (-len(p) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None


def register_user(username: str, password: str) -> dict[str, Any]:
    """Register a new user. Returns {success, user_id, token} or {success:False, message}."""
    username = (username or "").strip()
    if len(username) < 2:
        return {"success": False, "message": "用户名至少 2 个字符"}
    if len(password) < 6:
        return {"success": False, "message": "密码至少 6 个字符"}

    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_users_table(conn)
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return {"success": False, "message": "用户名已存在"}

        pw_hash = _hash_password(password)
        now = int(time.time())
        cursor = conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES(?,?,?)",
            (username, pw_hash, now),
        )
        user_id = cursor.lastrowid
        conn.commit()
        token = _create_jwt(user_id, username)
        return {"success": True, "user_id": user_id, "username": username, "token": token}
    except Exception:
        return {"success": False, "message": "注册失败，请稍后重试"}
    finally:
        conn.close()


def login_user(username: str, password: str) -> dict[str, Any]:
    """Login a user. Returns {success, user_id, token} or {success:False, message}."""
    username = (username or "").strip()
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_users_table(conn)
        row = conn.execute(
            "SELECT id, username, password_hash FROM users WHERE username=?",
            (username,),
        ).fetchone()
        if not row:
            return {"success": False, "message": "用户名不存在"}
        user_id, uname, pw_hash = row
        if not _verify_password(password, pw_hash):
            return {"success": False, "message": "密码错误"}
        token = _create_jwt(user_id, uname)
        return {"success": True, "user_id": user_id, "username": uname, "token": token}
    except Exception:
        return {"success": False, "message": "登录失败，请稍后重试"}
    finally:
        conn.close()


def get_user_from_token(token: str) -> dict[str, Any] | None:
    """Extract user info from JWT token. Returns {user_id, username} or None."""
    payload = _verify_jwt(token)
    if not payload:
        return None
    return {
        "user_id": int(payload.get("sub", 0)),
        "username": str(payload.get("username", "")),
    }


def get_or_create_default_user() -> int:
    """Legacy compatibility shim; automatic default accounts are disabled."""
    raise RuntimeError("默认账号已禁用，请先注册或登录")
