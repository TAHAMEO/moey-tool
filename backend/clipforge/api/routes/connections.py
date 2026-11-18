"""Social and storage account connections via official OAuth flows.

Passwords are never handled: the owner signs in on Google's or Instagram's own
page; CLIPFORGE receives an authorization code (bound to a single-use,
user-bound, 15-minute ``state``) and stores the resulting tokens encrypted.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...db.base import get_db
from ...db.models import SocialAccount
from ...platforms import drive, google, instagram, youtube
from ...platforms.http import PlatformError
from ...security.audit import audit
from ...security.crypto import delete_secret, store_secret
from ..deps import Principal, admin, client_ip, current_principal, viewer

router = APIRouter(prefix="/connections", tags=["connections"])


class GoogleStart(BaseModel):
    purpose: Literal["youtube", "google_drive"] = "youtube"


def _account(a: SocialAccount) -> dict:
    return {"id": str(a.id), "platform": a.platform, "display_name": a.display_name, "external_id": a.external_account_id,
            "status": a.status, "scopes": a.scopes, "connected_at": a.connected_at, "token_expires_at": a.token_expires_at,
            "meta": {k: v for k, v in (a.meta or {}).items() if k in ("username", "account_type", "channel_title", "email")}}


@router.get("")
def list_connections(db: Session = Depends(get_db), _: Principal = Depends(viewer)) -> dict:
    settings = get_settings()
    accounts = db.execute(select(SocialAccount).order_by(SocialAccount.connected_at)).scalars()
    return {
        "accounts": [_account(a) for a in accounts],
        "apps": {
            "google": {"configured": google.configured(), "redirect_uri": google.redirect_uri()},
            "instagram": {"configured": instagram.configured(), "redirect_uri": instagram.redirect_uri()},
            "youtube_api_key": settings.youtube_api_key is not None,
        },
        "youtube_quota_remaining": google.quota_remaining(db),
    }


@router.post("/google/start")
def google_start(body: GoogleStart, db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    try:
        return {"auth_url": google.start(db, principal.id, body.purpose)}
    except google.OAuthNotConfigured as exc:
        raise HTTPException(400, {"code": "not_configured", "message": str(exc)}) from exc


@router.get("/google/callback")
def google_callback(request: Request, state: str = "", code: str = "", error: str = "",
                    db: Session = Depends(get_db), principal: Principal = Depends(current_principal)):
    base = get_settings().public_base_url.rstrip("/")
    if error or not code:
        return RedirectResponse(f"{base}/settings?connection_error={error or 'cancelled'}#connections")
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
        return RedirectResponse(f"{base}/settings?connection_error={str(exc)[:120]}#connections")
    audit(db, "connection.add", user_id=principal.id, target_type="social_account", target_id=account.id,
          details={"platform": account.platform}, ip=client_ip(request))
    return RedirectResponse(f"{base}/settings?connected={account.platform}#connections")


@router.post("/instagram/start")
def instagram_start(db: Session = Depends(get_db), principal: Principal = Depends(admin)) -> dict:
    try:
        return {"auth_url": instagram.start(db, principal.id)}
    except PlatformError as exc:
        raise HTTPException(400, {"code": "not_configured", "message": str(exc)}) from exc


@router.get("/instagram/callback")
def instagram_callback(request: Request, state: str = "", code: str = "", error: str = "",
                       db: Session = Depends(get_db), principal: Principal = Depends(current_principal)):
    base = get_settings().public_base_url.rstrip("/")
    if error or not code:
        return RedirectResponse(f"{base}/settings?connection_error={error or 'cancelled'}#connections")
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
        return RedirectResponse(f"{base}/settings?connection_error={str(exc)[:120]}#connections")
    audit(db, "connection.add", user_id=principal.id, target_type="social_account", target_id=account.id,
          details={"platform": "instagram"}, ip=client_ip(request))
    return RedirectResponse(f"{base}/settings?connected=instagram#connections")


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
