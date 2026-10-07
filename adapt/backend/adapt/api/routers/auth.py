"""Login, logout and the current session (spec §9.5). Rules and storage live in adapt/api/auth.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from adapt.api.auth import COOKIE, DEMO_USER, SESSION_SECONDS, Auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginBody(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class SessionUser(BaseModel):
    user_id: str
    display_name: str
    role: str


class SessionOut(BaseModel):
    auth_enabled: bool
    user: SessionUser
    csrf_token: str


def _auth(request: Request) -> Auth | None:
    return request.app.state.auth


@router.post("/login", response_model=SessionOut)
def login(body: LoginBody, request: Request, response: Response):
    auth = _auth(request)
    if auth is None:
        return {"auth_enabled": False, "user": DEMO_USER.public(), "csrf_token": ""}
    try:
        user = auth.login(body.user_id, body.password)
    except PermissionError as exc:
        raise HTTPException(429, detail=str(exc)) from exc
    if user is None:
        raise HTTPException(401, detail="Wrong user name or password.")
    cookie, csrf = auth.issue(user)
    response.set_cookie(COOKIE, cookie, max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                        secure=auth.secure_cookie, path="/")
    auth.audit(request.headers.get("X-Request-ID"), user, "POST", "/api/v1/auth/login", 200)
    return {"auth_enabled": True, "user": user.public(), "csrf_token": csrf}


@router.post("/logout")
def logout(request: Request, response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=SessionOut)
def me(request: Request):
    auth = _auth(request)
    if auth is None:
        return {"auth_enabled": False, "user": DEMO_USER.public(), "csrf_token": ""}
    sess = auth.session(request.cookies.get(COOKIE))
    if sess is None:
        raise HTTPException(401, detail="AUTH_REQUIRED: sign in to continue")
    user, sid = sess
    return {"auth_enabled": True, "user": user.public(), "csrf_token": auth.csrf_for(sid)}
