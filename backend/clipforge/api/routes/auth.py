from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db
from ...db.models import User, UserSession
from ...security.passwords import WeakPasswordError
from ...services import auth
from ..deps import SESSION_COOKIE, Principal, client_ip, current_principal, owner

router = APIRouter(prefix="/auth", tags=["auth"])


class SetupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field("", max_length=120)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=256)
    new_password: str = Field(max_length=256)


class NewUser(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    role: str = "editor"


def _set_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, secure=settings.secure_cookies, samesite="lax",
        max_age=settings.session_ttl_hours * 3600, path="/",
    )


def _me(user: User, session: UserSession) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "csrf_token": session.csrf_token,
    }


@router.get("/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"setup_required": not auth.users_exist(db)}


@router.post("/setup")
def setup(body: SetupRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    try:
        user = auth.create_owner(db, email=body.email, password=body.password, display_name=body.display_name, ip=ip)
    except WeakPasswordError as exc:
        raise HTTPException(400, {"code": "weak_password", "message": str(exc)}) from exc
    except auth.AuthError as exc:
        raise HTTPException(409, {"code": exc.code, "message": str(exc)}) from exc
    token, session = auth.create_session(db, user, ip=ip, user_agent=request.headers.get("user-agent"))
    _set_cookie(response, token)
    return _me(user, session)


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    ip = client_ip(request)
    try:
        user = auth.authenticate(db, email=body.email, password=body.password, ip=ip)
    except auth.AuthError as exc:
        db.commit()  # keep the audit record of the failure
        status = 429 if exc.code == "locked" else 401
        raise HTTPException(status, {"code": exc.code, "message": str(exc)}) from exc
    token, session = auth.create_session(db, user, ip=ip, user_agent=request.headers.get("user-agent"))
    _set_cookie(response, token)
    return _me(user, session)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db),
           principal: Principal = Depends(current_principal)) -> dict:
    auth.revoke_session(db, request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return _me(principal.user, principal.session)


@router.post("/password")
def change_password(body: PasswordChange, request: Request, db: Session = Depends(get_db),
                    principal: Principal = Depends(current_principal)) -> dict:
    try:
        auth.change_password(db, principal.user, current=body.current_password, new=body.new_password,
                             keep_session_id=principal.session.id, ip=client_ip(request))
    except (auth.AuthError, WeakPasswordError) as exc:
        raise HTTPException(400, {"code": "password", "message": str(exc)}) from exc
    return {"ok": True}


@router.get("/users")
def list_users(db: Session = Depends(get_db), principal: Principal = Depends(owner)) -> list[dict]:
    users = db.execute(select(User).order_by(User.created_at)).scalars()
    return [{"id": str(u.id), "email": u.email, "role": u.role, "active": u.is_active,
             "last_login_at": u.last_login_at} for u in users]


@router.post("/users")
def add_user(body: NewUser, request: Request, db: Session = Depends(get_db), principal: Principal = Depends(owner)) -> dict:
    try:
        user = auth.create_user(db, email=body.email, password=body.password, role=body.role,
                                created_by=principal.id, ip=client_ip(request))
    except (auth.AuthError, WeakPasswordError) as exc:
        raise HTTPException(400, {"code": "user", "message": str(exc)}) from exc
    return {"id": str(user.id), "email": user.email, "role": user.role}
