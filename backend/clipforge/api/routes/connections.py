"""Social and storage account connections via official OAuth flows.

Passwords are never handled: the owner signs in on Google's or Instagram's own
page; CLIPFORGE receives an authorization code (bound to a single-use,
user-bound, 15-minute ``state``) and stores the resulting tokens encrypted.

Connecting is one click once the OAuth app is known (built into this copy, or
saved once in Settings → Accounts — see platforms/apps.py). The sign-in runs in
a popup that closes itself and tells the Settings page the result.
"""

from __future__ import annotations

import uuid
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db
from ...db.models import OAuthState, SocialAccount
from ...platforms import apps, drive, google, instagram, youtube
from ...platforms.http import PlatformError
from ...security.audit import audit
from ...security.crypto import delete_secret, store_secret
from ..deps import Principal, admin, client_ip, current_principal, viewer

router = APIRouter(prefix="/connections", tags=["connections"])


class GoogleStart(BaseModel):
    purpose: Literal["youtube", "google_drive"] = "youtube"
    #: The browser's origin (window.location.origin): a Desktop-app client returns there.
    origin: str | None = Field(None, max_length=100)


class InstagramStart(BaseModel):
    origin: str | None = Field(None, max_length=100)


class GoogleAppIn(BaseModel):
    client_id: str = Field(..., max_length=200)
    client_secret: str = Field(..., max_length=200)
    kind: Literal["web", "desktop"] = "desktop"


class MetaAppIn(BaseModel):
    app_id: str = Field(..., max_length=40)
    app_secret: str = Field(..., max_length=200)
    relay_url: str | None = Field(None, max_length=500)


def _account(a: SocialAccount) -> dict:
    return {"id": str(a.id), "platform": a.platform, "display_name": a.display_name, "external_id": a.external_account_id,
            "status": a.status, "scopes": a.scopes, "connected_at": a.connected_at, "token_expires_at": a.token_expires_at,
            "meta": {k: v for k, v in (a.meta or {}).items() if k in ("username", "account_type", "channel_title", "email")}}


@router.get("")
def list_connections(origin: str | None = None, db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    settings = get_settings()
    accounts = db.execute(select(SocialAccount).order_by(SocialAccount.connected_at)).scalars()
    return {
        "accounts": [_account(a) for a in accounts],
        "apps": {**apps.status(db, origin), "youtube_api_key": settings.youtube_api_key is not None},
        "youtube_quota_remaining": google.quota_remaining(db),
    }


# ------------------------------------------------------------ app set-up (once)
@router.put("/apps/google")
def save_google_app(body: GoogleAppIn, request: Request, db: Session = Depends(get_db),
                    principal: Principal = Depends(admin)) -> dict:
    """Save the Google OAuth client in CLIPFORGE (encrypted); used right away, no restart."""
    try:
        app = apps.save_google(db, client_id=body.client_id, client_secret=body.client_secret, kind=body.kind)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    verdict, detail = google.check_app(app)
    if verdict is False:
        db.rollback()
        raise HTTPException(422, {"code": "rejected", "message": detail})
    audit(db, "connection.app_saved", user_id=principal.id, target_type="oauth_app", target_id=None,
          details={"provider": "google", "kind": body.kind, "verified": verdict}, ip=client_ip(request))
    return {"ok": True, "verified": verdict, "detail": detail}


@router.put("/apps/instagram")
def save_instagram_app(body: MetaAppIn, request: Request, db: Session = Depends(get_db),
                       principal: Principal = Depends(admin)) -> dict:
    try:
        apps.save_meta(db, app_id=body.app_id, app_secret=body.app_secret, relay_url=body.relay_url)
    except ValueError as exc:
        raise HTTPException(422, {"code": "invalid", "message": str(exc)}) from exc
    audit(db, "connection.app_saved", user_id=principal.id, target_type="oauth_app", target_id=None,
          details={"provider": "instagram", "relay": bool(body.relay_url)}, ip=client_ip(request))
    return {"ok": True, "verified": None, "detail": "Saved. Instagram checks it when you connect."}


@router.delete("/apps/{provider}")
def forget_app(provider: Literal["google", "instagram"], request: Request, db: Session = Depends(get_db),
               principal: Principal = Depends(admin)) -> dict:
    apps.forget(db, provider)
    audit(db, "connection.app_removed", user_id=principal.id, target_type="oauth_app", target_id=None,
          details={"provider": provider}, ip=client_ip(request))
    return {"ok": True}


# --------------------------------------------------------------- landing pages
def _landing(db: Session, state: str) -> str:
    """Where the browser goes after the sign-in: the address it started from."""
    row = db.get(OAuthState, state) if state else None
    if row is not None:
        parts = urlsplit(row.redirect_uri)
        if apps.loopback_origin(f"{parts.scheme}://{parts.netloc}"):
            return f"{parts.scheme}://{parts.netloc}"
        target = state.rsplit(".", 1)[-1] if state.count(".") == 2 else ""
        if target[:1] in ("l", "i") and target[1:].isdigit():
            return f"http://{'localhost' if target[0] == 'l' else '127.0.0.1'}:{target[1:]}"
    return get_settings().public_base_url.rstrip("/")


def _done(origin: str, *, connected: str | None = None, error: str | None = None) -> RedirectResponse:
    query = f"connected={connected}" if connected else f"error={quote((error or 'cancelled')[:160])}"
    return RedirectResponse(f"{origin}/connected?{query}")


@router.post("/google/start")
def google_start(body: GoogleStart, db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    try:
        return {"auth_url": google.start(db, principal.id, body.purpose, origin=body.origin)}
    except google.OAuthNotConfigured as exc:
        raise HTTPException(400, {"code": "not_configured", "message": str(exc)}) from exc


@router.get("/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = "",
                    db: Session = Depends(get_db), principal: Principal = Depends(current_principal)):
    base = _landing(db, state)
    if error or not code:
        return _done(base, error=error or "cancelled")
    try:
        purpose, tokens = google.finish(db, state, code, principal.id)
        if purpose == "youtube":
            channel = youtube.my_channel(youtube.Auth(access_token=tokens["access_token"]))
            if channel is None:
                raise PlatformError("google", "This Google account has no YouTube channel.")
            account = google.save_account(db, platform="youtube", external_id=channel["id"],
                                          display_name=channel["snippet"]["title"], tokens=tokens,
                                          meta={"channel_title": channel["snippet"]["title"]})
        else:
            user = drive.about(tokens["access_token"])
            account = google.save_account(db, platform="google_drive", external_id=user.get("emailAddress", "drive"),
                                          display_name=user.get("displayName", "Google Drive"), tokens=tokens,
                                          meta={"email": user.get("emailAddress")})
    except PlatformError as exc:
        return _done(base, error=str(exc))
    audit(db, "connection.add", user_id=principal.id, target_type="social_account", target_id=account.id,
          details={"platform": account.platform}, ip=client_ip(request))
    return _done(base, connected=account.platform)


@router.post("/instagram/start")
def instagram_start(body: InstagramStart | None = None, db: Session = Depends(get_db),
                    principal: Principal = Depends(admin)) -> dict:
    try:
        return {"auth_url": instagram.start(db, principal.id, origin=body.origin if body else None)}
    except PlatformError as exc:
        raise HTTPException(400, {"code": "not_configured", "message": str(exc)}) from exc


@router.get("/instagram/callback")
def instagram_callback(request: Request, state: str = "", code: str = "", error: str = "",
                       db: Session = Depends(get_db), principal: Principal = Depends(current_principal)):
    base = _landing(db, state)
    if error or not code:
        return _done(base, error=error or "cancelled")
    try:
        tokens = instagram.finish(db, state, code, principal.id)
        profile = instagram.me(tokens["access_token"])
        if profile.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR"):
            raise PlatformError("instagram", "Publishing needs an Instagram Business or Creator account.")
        account = db.execute(select(SocialAccount).where(SocialAccount.platform == "instagram",
                                                         SocialAccount.external_account_id == str(profile["user_id"]))).scalar_one_or_none()
        if account is None:
            account = SocialAccount(platform="instagram", external_account_id=str(profile["user_id"]))
            db.add(account)
            db.flush()
        if account.secret_id:
            delete_secret(db, account.secret_id)
        account.secret_id = store_secret(db, f"oauth:instagram:{account.id}", tokens, hint="oauth tokens").id
        account.display_name = profile.get("username", "")
        account.scopes = tokens.get("permissions") or instagram.SCOPES
        account.status = "connected"
        account.meta = {"username": profile.get("username"), "account_type": profile.get("account_type")}
    except PlatformError as exc:
        return _done(base, error=str(exc))
    audit(db, "connection.add", user_id=principal.id, target_type="social_account", target_id=account.id,
          details={"platform": "instagram"}, ip=client_ip(request))
    return _done(base, connected="instagram")


@router.delete("/{account_id}")
def disconnect(account_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
               principal: Principal = Depends(admin)) -> dict:
    account = db.get(SocialAccount, account_id)
    if account is None:
        raise HTTPException(404, {"code": "not_found", "message": "Connection not found."})
    if account.platform in ("youtube", "google_drive") and account.secret_id:
        google.revoke(db, account)
    secret_id = account.secret_id
    db.delete(account)
    db.flush()
    delete_secret(db, secret_id)
    audit(db, "connection.remove", user_id=principal.id, target_type="social_account", target_id=account_id,
          details={"platform": account.platform}, ip=client_ip(request))
    return {"ok": True}
