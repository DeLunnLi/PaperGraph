"""Auth API routes: register, login, verify."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...services.auth.user_service import register_user, login_user
from ..deps import check_rate_limit, require_user

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
async def register(req: RegisterRequest, request: Request):
    check_rate_limit(request.client.host if request.client else "unknown", max_requests=5)
    result = register_user(req.username, req.password)
    return AuthResponse(**result)


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, request: Request):
    check_rate_limit(request.client.host if request.client else "unknown", max_requests=10)
    result = login_user(req.username, req.password)
    return AuthResponse(**result)


@router.get("/verify")
async def verify_token(user: dict = Depends(require_user)):
    """Verify the bearer token without exposing it in URLs or access logs."""
    return {"success": True, **user}
