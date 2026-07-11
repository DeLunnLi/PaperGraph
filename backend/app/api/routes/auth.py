"""Auth API routes: register, login, verify."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...services.auth.user_service import register_user, login_user, get_user_from_token

router = APIRouter(prefix="/auth", tags=["认证"])


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    success: bool
    message: str | None = None
    user_id: int | None = None
    username: str | None = None
    token: str | None = None


@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest):
    result = register_user(req.username, req.password)
    return AuthResponse(**result)


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest):
    result = login_user(req.username, req.password)
    return AuthResponse(**result)


@router.get("/verify")
async def verify_token(token: str):
    """Verify a JWT token. Returns user info or 401."""
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="无效或过期的 token")
    return {"success": True, **user}
