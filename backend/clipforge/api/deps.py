"""FastAPI dependencies: authentication, CSRF, roles, client info."""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import User, UserSession
from ..services import auth

SESSION_COOKIE = "cf_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass
class Principal:
    user: User
    session: UserSession

    @property
    def id(self):
        return self.user.id


def client_ip(request: Request) -> str | None:
    """Client address for throttling and audit logs.

    With ``n`` trusted proxy hops the client is the n-th entry from the *right* of
    X-Forwarded-For; entries further left were supplied by the client itself.
    """
    hops = request.app.state.trusted_proxy_hops
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and hops > 0:
        entries = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(entries) >= hops:
            return entries[-hops][:64]
    return request.client.host if request.client else None


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    session = auth.resolve_session(db, request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Please sign in."})
    if request.method not in SAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not supplied or not hmac.compare_digest(supplied, session.csrf_token):
            raise HTTPException(status_code=403, detail={"code": "csrf", "message": "Security token missing or invalid. Reload the page."})
    return Principal(user=session.user, session=session)


def require_role(minimum: str):
    def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        if not auth.role_at_least(principal.user.role, minimum):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "You do not have permission for this action."})
        return principal

    return dependency


viewer = require_role("viewer")
editor = require_role("editor")
admin = require_role("admin")
owner = require_role("owner")
