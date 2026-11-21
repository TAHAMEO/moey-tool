"""Google OAuth 2.0 (authorization code flow with PKCE) for YouTube and Google Drive.

The OAuth app comes from :mod:`.apps` (saved in CLIPFORGE, built into this
copy, or environment). A *desktop* app returns to CLIPFORGE on this computer
(``http://localhost:<port>``) without any redirect registration; a *web* app
returns to the registered public address. CLIPFORGE never sees the Google
password. Tokens are stored encrypted; refresh tokens are used automatically
and revoked on disconnect.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import utcnow
from ..db.models import OAuthState, PlatformQuotaUsage, SocialAccount
from ..security.crypto import read_secret_json, store_secret, update_secret
from . import apps
from .http import PlatformError, request

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPES = {
    "youtube": [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ],
    "google_drive": ["https://www.googleapis.com/auth/drive.readonly"],
}
STATE_TTL = timedelta(minutes=15)


class OAuthNotConfigured(RuntimeError):
    pass


def configured(db: Session | None = None) -> bool:
    return apps.google_app(db) is not None


def _app(db: Session) -> apps.GoogleApp:
    app = apps.google_app(db)
    if app is None:
        raise PlatformError("google", "Google sign-in isn't set up any more. Set it up again in Settings → Accounts, "
                                      "then reconnect.", reason="reauth")
    return app


def start(db: Session, user_id: uuid.UUID, purpose: str, *, origin: str | None = None) -> str:
    app = apps.google_app(db)
    if app is None:
        raise OAuthNotConfigured("Google sign-in isn't set up yet.")
    if purpose not in SCOPES:
        raise ValueError("Unknown Google connection purpose")
    redirect, problem = apps.google_redirect(app, origin)
    if problem:
        raise OAuthNotConfigured(problem)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = f"google:{purpose}:{secrets.token_urlsafe(24)}"
    db.add(OAuthState(state=state, provider="google", user_id=user_id, code_verifier=verifier, redirect_uri=redirect))
    params = {
        "client_id": app.client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES[purpose]),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_url() -> str:
    return get_settings().google_oauth_base.rstrip("/") + "/token"


def finish(db: Session, state: str, code: str, user_id: uuid.UUID) -> tuple[str, dict]:
    """Exchange the authorization code. Returns (purpose, token payload)."""
    row = db.get(OAuthState, state)
    if row is None or row.provider != "google" or row.user_id != user_id or row.created_at < utcnow() - STATE_TTL:
        raise PlatformError("google", "This sign-in link expired or was not started from this account. Please try again.")
    db.delete(row)
    app = _app(db)
    response = request("google", "POST", _token_url(), data={
        "code": code, "client_id": app.client_id, "client_secret": app.client_secret, "redirect_uri": row.redirect_uri,
        "grant_type": "authorization_code", "code_verifier": row.code_verifier,
    })
    if response.status_code != 200:
        raise PlatformError("google", f"Google rejected the sign-in ({response.json().get('error', response.status_code)}).")
    tokens = response.json()
    tokens["expires_at"] = (utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))).isoformat()
    return state.split(":")[1], tokens


def check_app(app: apps.GoogleApp) -> tuple[bool | None, str]:
    """Ask Google whether it knows this client (a deliberately invalid code: 'invalid_client' means it doesn't)."""
    try:
        response = request("google", "POST", _token_url(), max_attempts=1, data={
            "code": "clipforge-credential-check", "client_id": app.client_id, "client_secret": app.client_secret,
            "redirect_uri": "http://localhost", "grant_type": "authorization_code"})
        error = response.json().get("error") if "json" in response.headers.get("content-type", "") else None
    except (PlatformError, OSError, ValueError) as exc:
        return None, f"Saved, but Google couldn't be reached to check it ({exc.__class__.__name__})."
    if error in ("invalid_client", "unauthorized_client"):
        return False, "Google doesn't recognise this client ID and secret. Copy both again from Google Cloud → Credentials."
    if error in ("invalid_grant", "redirect_uri_mismatch", "invalid_request"):
        return True, "Google recognised the app."
    return None, f"Saved; Google's answer couldn't be interpreted ({error or response.status_code})."


def save_account(db: Session, *, platform: str, external_id: str, display_name: str, tokens: dict, meta: dict) -> SocialAccount:
    account = db.execute(select(SocialAccount).where(SocialAccount.platform == platform,
                                                     SocialAccount.external_account_id == external_id)).scalar_one_or_none()
    scopes = tokens.get("scope", "").split()
    if account is None:
        account = SocialAccount(platform=platform, external_account_id=external_id)
        db.add(account)
        db.flush()
    if account.secret_id:
        from ..db.models import Secret

        previous = read_secret_json(db, account.secret_id)
        if not tokens.get("refresh_token") and previous.get("refresh_token"):
            tokens["refresh_token"] = previous["refresh_token"]
        update_secret(db, db.get(Secret, account.secret_id), tokens, hint="oauth tokens")
    else:
        account.secret_id = store_secret(db, f"oauth:{platform}:{account.id}", tokens, hint="oauth tokens").id
    account.display_name = display_name
    account.scopes = scopes
    account.status = "connected"
    account.meta = meta
    account.token_expires_at = utcnow() + timedelta(seconds=int(tokens.get("expires_in", 3600)))
    db.flush()
    return account


def access_token(db: Session, account: SocialAccount) -> str:
    """Valid access token, refreshing it when it expires within a minute."""
    from datetime import datetime

    from ..db.models import Secret

    tokens = read_secret_json(db, account.secret_id)
    expires_at = datetime.fromisoformat(tokens["expires_at"]) if tokens.get("expires_at") else utcnow()
    if expires_at > utcnow() + timedelta(seconds=60):
        return tokens["access_token"]
    if not tokens.get("refresh_token"):
        account.status = "reauth_required"
        raise PlatformError("google", "Google access expired and no refresh token is stored. Reconnect the account.", reason="reauth")
    app = _app(db)
    response = request("google", "POST", _token_url(), data={
        "client_id": app.client_id, "client_secret": app.client_secret,
        "refresh_token": tokens["refresh_token"], "grant_type": "refresh_token",
    })
    if response.status_code != 200:
        error = response.json().get("error") if response.headers.get("content-type", "").startswith("application/json") else None
        if error in ("invalid_grant", "invalid_client", "unauthorized_client"):
            account.status = "reauth_required"
            raise PlatformError("google", "Google access was revoked or expired. Reconnect the account.", reason="reauth")
        raise PlatformError("google", f"Token refresh failed ({response.status_code})", retryable=response.status_code >= 500)
    fresh = response.json()
    tokens.update({"access_token": fresh["access_token"],
                   "expires_at": (utcnow() + timedelta(seconds=int(fresh.get("expires_in", 3600)))).isoformat()})
    update_secret(db, db.get(Secret, account.secret_id), tokens, hint="oauth tokens")
    account.token_expires_at = utcnow() + timedelta(seconds=int(fresh.get("expires_in", 3600)))
    account.status = "connected"
    return tokens["access_token"]


def revoke(db: Session, account: SocialAccount) -> None:
    try:
        tokens = read_secret_json(db, account.secret_id)
        token = tokens.get("refresh_token") or tokens.get("access_token")
        if token:
            request("google", "POST", get_settings().google_oauth_base.rstrip("/") + "/revoke", params={"token": token},
                    max_attempts=2)
    except Exception as exc:  # noqa: BLE001 - revocation is best effort; local deletion always proceeds
        log.warning("Token revocation at Google failed (tokens are deleted locally anyway): %s", exc)


# YouTube quota resets at midnight Pacific Time.
QUOTA_COST = {"videos.list": 1, "channels.list": 1, "videos.insert": 1600, "videos.update": 50, "captions.insert": 400,
              "captions.list": 50,
              "captions.download": 200, "captions.update": 450, "thumbnails.set": 50, "playlistItems.insert": 50,
              "search.list": 100}
DAILY_QUOTA = 10_000


def record_quota(db: Session, operation: str) -> int:
    day = utcnow().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    row = db.execute(select(PlatformQuotaUsage).where(PlatformQuotaUsage.platform == "youtube",
                                                      PlatformQuotaUsage.day == day)).scalar_one_or_none()
    if row is None:
        row = PlatformQuotaUsage(platform="youtube", day=day, units=0, calls=0)
        db.add(row)
    row.units += QUOTA_COST.get(operation, 1)
    row.calls += 1
    db.flush()
    return row.units


def next_quota_reset() -> datetime:
    """A few minutes after the next midnight Pacific Time, when the daily quota is restored."""
    pacific = ZoneInfo("America/Los_Angeles")
    tomorrow = utcnow().astimezone(pacific).date() + timedelta(days=1)
    return datetime.combine(tomorrow, time(0, 10), pacific).astimezone(UTC)


def quota_remaining(db: Session) -> int:
    day = utcnow().astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    row = db.execute(select(PlatformQuotaUsage).where(PlatformQuotaUsage.platform == "youtube",
                                                      PlatformQuotaUsage.day == day)).scalar_one_or_none()
    return DAILY_QUOTA - (row.units if row else 0)
