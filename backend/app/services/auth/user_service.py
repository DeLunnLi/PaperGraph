"""User authentication: registration, login, JWT token management.

Simple JWT-based auth with bcrypt password hashing.
Users table stored in the same papers.db SQLite.
"""
from __future__ import annotations

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
    if secret:
        return secret
    # Fallback: derive from data_dir path (not ideal for production, but works for single-instance)
    return f"papergraph_default_{hashlib.sha256(get_settings().data_dir.encode()).hexdigest()[:32]}"


JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = 72


def _ensure_users_table(conn: sqlite3.Connection) -> None:
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
        return json.dumps(data, separators=(",", ":")).encode("utf-8").hex()

    h = _b64(header)
    p = _b64(payload)
    sig = hmac.new(_get_jwt_secret().encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{h}.{p}.{sig}"


def _verify_jwt(token: str) -> dict[str, Any] | None:
    """Verify a JWT token, return payload dict or None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    h, p, sig = parts
    expected_sig = hmac.new(_get_jwt_secret().encode("utf-8"), f"{h}.{p}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    try:
        payload = json.loads(bytes.fromhex(p).decode("utf-8"))
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
    except Exception as e:
        return {"success": False, "message": f"注册失败: {e}"}
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
    except Exception as e:
        return {"success": False, "message": f"登录失败: {e}"}
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
    """Get the default user ID (for backwards compat with existing single-user data).

    Creates a default user if none exists. All existing papers/memories belong to this user.
    """
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_users_table(conn)
        row = conn.execute("SELECT id FROM users WHERE username='default'").fetchone()
        if row:
            return row[0]
        pw_hash = _hash_password("default")
        now = int(time.time())
        cursor = conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES('default', ?, ?)",
            (pw_hash, now),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        # If users table fails, return 1 as fallback
        return 1
    finally:
        conn.close()
