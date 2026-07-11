"""Auth dependencies for FastAPI routes.

Usage:
    from .deps import require_user
    @router.get("/protected", dependencies=[Depends(require_user)])
    async def handler(user: dict = Depends(require_user)):
        user_id = user["user_id"]
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, Header, HTTPException

from ..services.auth.user_service import get_user_from_token, get_or_create_default_user


async def require_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Extract and verify the JWT token from Authorization header.

    Returns {"user_id": int, "username": str}.
    Raises 401 if token is missing or invalid.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录：请先登录获取 token")

    # Strip "Bearer " prefix
    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="token 无效或已过期，请重新登录")

    return user


async def optional_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Like require_user but returns a default user instead of raising 401.

    Used for endpoints that should work in single-user mode (no auth configured)
    but also support multi-user isolation when auth is enabled.
    """
    if not authorization:
        # No token → use default user (backwards compat)
        uid = get_or_create_default_user()
        return {"user_id": uid, "username": "default"}

    token = authorization
    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    user = get_user_from_token(token)
    if not user:
        # Invalid token → also fall back to default user (don't block)
        uid = get_or_create_default_user()
        return {"user_id": uid, "username": "default"}

    return user


# Simple in-memory rate limiter (no Redis needed)
_rate_store: dict[str, list[float]] = {}
_RATE_WINDOW = 60  # seconds
_RATE_MAX = 30  # max requests per window per IP


def check_rate_limit(ip: str, max_requests: int = _RATE_MAX, window: int = _RATE_WINDOW) -> None:
    """Simple sliding-window rate limiter. Raises 429 if exceeded."""
    now = time.time()
    key = ip
    if key not in _rate_store:
        _rate_store[key] = []

    # Remove old entries
    _rate_store[key] = [t for t in _rate_store[key] if now - t < window]

    if len(_rate_store[key]) >= max_requests:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")

    _rate_store[key].append(now)
