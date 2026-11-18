"""Instagram Platform API (Instagram Login, graph.instagram.com).

* OAuth: Instagram Login authorization code -> short-lived token -> long-lived
  token (≈60 days), refreshed automatically before expiry.
* Source access: the connected professional account's *own* media is listed
  with the Inoue-AI SDK; a Reel/post that belongs to the account exposes an
  authorized ``media_url``. Other accounts' media is never downloaded.
* Publishing Reels: resumable upload to ``rupload.facebook.com`` (no public
  URL needed) or, when a public media URL exists, the SDK's ``video_url``
  container; then container polling, ``media_publish`` and verification.
* Insights are requested in groups and metric-by-metric on failure, so a
  metric the API no longer offers is reported as *unavailable*, never invented.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.base import utcnow
from ..db.models import OAuthState, SocialAccount
from ..security.crypto import read_secret_json, update_secret
from .http import PlatformError, meta_error, request

AUTHORIZE_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
#: Least privilege: profile, publishing and insights only (no comments or messages access).
SCOPES = ["instagram_business_basic", "instagram_business_content_publish", "instagram_business_manage_insights"]
REEL_METRICS = ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions",
                "ig_reels_avg_watch_time", "ig_reels_video_view_total_time"]

log = logging.getLogger(__name__)


def configured() -> bool:
    settings = get_settings()
    return bool(settings.meta_app_id and settings.meta_app_secret)


def _base() -> str:
    settings = get_settings()
    return f"{settings.instagram_graph_base.rstrip('/')}/{settings.instagram_api_version}"


def redirect_uri() -> str:
    return get_settings().public_base_url.rstrip("/") + "/api/connections/instagram/callback"


def start(db: Session, user_id: uuid.UUID) -> str:
    if not configured():
        raise PlatformError("instagram", "Meta app is not configured (CLIPFORGE_META_APP_ID/SECRET).")
    state = f"instagram:{secrets.token_urlsafe(24)}"
    db.add(OAuthState(state=state, provider="instagram", user_id=user_id, redirect_uri=redirect_uri()))
    params = {"client_id": get_settings().meta_app_id, "redirect_uri": redirect_uri(), "response_type": "code",
              "scope": ",".join(SCOPES), "state": state}
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def finish(db: Session, state: str, code: str, user_id: uuid.UUID) -> dict:
    row = db.get(OAuthState, state)
    if row is None or row.provider != "instagram" or row.user_id != user_id or row.created_at < utcnow() - timedelta(minutes=15):
        raise PlatformError("instagram", "This sign-in link expired or was not started from this account. Please try again.")
    db.delete(row)
    settings = get_settings()
    code = code.split("#")[0]
    short = request("instagram", "POST", TOKEN_URL, data={
        "client_id": settings.meta_app_id, "client_secret": settings.meta_app_secret.get_secret_value(),
        "grant_type": "authorization_code", "redirect_uri": row.redirect_uri, "code": code})
    if short.status_code != 200:
        raise meta_error(short)
    short_data = short.json()
    long = request("instagram", "GET", f"{settings.instagram_graph_base.rstrip('/')}/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": settings.meta_app_secret.get_secret_value(),
        "access_token": short_data["access_token"]})
    if long.status_code != 200:
        raise meta_error(long)
    data = long.json()
    return {"access_token": data["access_token"], "user_id": str(short_data.get("user_id", "")),
            "expires_at": (utcnow() + timedelta(seconds=int(data.get("expires_in", 5_184_000)))).isoformat(),
            "permissions": short_data.get("permissions", [])}


def me(token: str) -> dict:
    response = request("instagram", "GET", f"{_base()}/me", params={
        "fields": "user_id,username,account_type,name,profile_picture_url", "access_token": token})
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()


def access_token(db: Session, account: SocialAccount) -> str:
    from datetime import datetime

    from ..db.models import Secret

    tokens = read_secret_json(db, account.secret_id)
    expires_at = datetime.fromisoformat(tokens["expires_at"]) if tokens.get("expires_at") else utcnow()
    if expires_at <= utcnow():
        account.status = "reauth_required"
        raise PlatformError("instagram", "Instagram access expired. Reconnect the account.", reason="token")
    if expires_at - utcnow() < timedelta(days=10):
        response = request("instagram", "GET", f"{get_settings().instagram_graph_base.rstrip('/')}/refresh_access_token",
                           params={"grant_type": "ig_refresh_token", "access_token": tokens["access_token"]})
        if response.status_code == 200:
            data = response.json()
            tokens.update({"access_token": data["access_token"],
                           "expires_at": (utcnow() + timedelta(seconds=int(data.get("expires_in", 5_184_000)))).isoformat()})
            update_secret(db, db.get(Secret, account.secret_id), tokens, hint="oauth tokens")
            account.token_expires_at = datetime.fromisoformat(tokens["expires_at"])
    return tokens["access_token"]


# ------------------------------------------------------------ source access
def shortcode_from_permalink(permalink: str | None) -> str | None:
    match = re.search(r"/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", permalink or "")
    return match.group(1) if match else None


def find_own_media(token: str, ig_user_id: str, shortcode: str, *, max_items: int = 500) -> dict | None:
    """Look the shortcode up among the connected account's own media (Inoue-AI SDK)."""
    from instagram import InstagramClient

    settings = get_settings()

    async def _search() -> dict | None:
        async with InstagramClient(access_token=token, ig_user_id=ig_user_id, base_url=settings.instagram_graph_base,
                                   api_version=settings.instagram_api_version) as client:
            seen = 0
            async for media in client.media.iter_media(limit=50):
                seen += 1
                if (media.shortcode == shortcode) or shortcode_from_permalink(media.permalink) == shortcode:
                    return media.model_dump(mode="json")
                if seen >= max_items:
                    return None
        return None

    return asyncio.run(_search())


def oembed(url: str) -> dict | None:
    """Public metadata for any public post — requires the app's oEmbed Read feature."""
    settings = get_settings()
    if not (settings.meta_app_id and settings.meta_client_token):
        return None
    response = request("instagram", "GET", f"https://graph.facebook.com/{settings.instagram_api_version}/instagram_oembed",
                       params={"url": url, "access_token": f"{settings.meta_app_id}|{settings.meta_client_token.get_secret_value()}",
                               "fields": "author_name,thumbnail_url,thumbnail_width,thumbnail_height,provider_name"},
                       max_attempts=2)
    if response.status_code == 200:
        return response.json()
    return None


# ---------------------------------------------------------------- publishing
def publishing_limit(token: str, ig_user_id: str) -> dict:
    response = request("instagram", "GET", f"{_base()}/{ig_user_id}/content_publishing_limit",
                       params={"fields": "quota_usage,config", "access_token": token})
    if response.status_code != 200:
        raise meta_error(response)
    data = response.json().get("data", [{}])
    return data[0] if data else {}


def create_reel_container(token: str, ig_user_id: str, *, caption: str, share_to_feed: bool = True,
                          thumb_offset_ms: int | None = None, cover_url: str | None = None,
                          video_url: str | None = None) -> dict:
    params = {"media_type": "REELS", "caption": caption, "share_to_feed": str(share_to_feed).lower(),
              "access_token": token}
    if video_url:
        params["video_url"] = video_url
    else:
        params["upload_type"] = "resumable"
    if thumb_offset_ms is not None:
        params["thumb_offset"] = str(int(thumb_offset_ms))
    if cover_url:
        params["cover_url"] = cover_url
    response = request("instagram", "POST", f"{_base()}/{ig_user_id}/media", data=params)
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()


def upload_resumable(token: str, upload_uri: str, path: Path, *, offset: int = 0) -> dict:
    size = path.stat().st_size
    with open(path, "rb") as handle:
        handle.seek(offset)
        body = handle.read()
    response = request("instagram", "POST", upload_uri, timeout=600,
                       headers={"Authorization": f"OAuth {token}", "offset": str(offset), "file_size": str(size)},
                       content=body)
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()


def container_status(token: str, container_id: str) -> dict:
    response = request("instagram", "GET", f"{_base()}/{container_id}",
                       params={"fields": "id,status_code,status", "access_token": token})
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()


def wait_for_container(token: str, container_id: str, *, timeout_s: float = 900, interval_s: float = 10,
                       cancelled=None) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = container_status(token, container_id)
        code = status.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return status
        if code in ("ERROR", "EXPIRED"):
            raise PlatformError("instagram", f"Instagram could not process the video: {status.get('status') or code}",
                                reason=code)
        if cancelled and cancelled():
            raise PlatformError("instagram", "Publishing cancelled", reason="cancelled")
        time.sleep(interval_s)
    raise PlatformError("instagram", "Instagram processing did not finish in time", retryable=True, reason="timeout")


def publish_container(token: str, ig_user_id: str, container_id: str) -> str:
    response = request("instagram", "POST", f"{_base()}/{ig_user_id}/media_publish",
                       data={"creation_id": container_id, "access_token": token})
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()["id"]


def media_info(token: str, media_id: str) -> dict:
    response = request("instagram", "GET", f"{_base()}/{media_id}", params={
        "fields": "id,permalink,media_type,media_product_type,timestamp,caption,shortcode", "access_token": token})
    if response.status_code != 200:
        raise meta_error(response)
    return response.json()


def reel_insights(token: str, media_id: str, metrics: list[str] | None = None) -> tuple[dict, dict]:
    """Returns (available metrics, unavailable metric -> reason)."""
    wanted = metrics or REEL_METRICS
    values: dict[str, float] = {}
    unavailable: dict[str, str] = {}

    def fetch(names: list[str]):
        return request("instagram", "GET", f"{_base()}/{media_id}/insights",
                       params={"metric": ",".join(names), "access_token": token})

    response = fetch(wanted)
    if response.status_code == 200:
        rows = response.json().get("data", [])
    else:
        rows = []
        for name in wanted:  # isolate unsupported metrics
            single = fetch([name])
            if single.status_code == 200:
                rows.extend(single.json().get("data", []))
            else:
                unavailable[name] = meta_error(single).args[0][:200]
    for row in rows:
        series = row.get("values") or [{}]
        value = series[0].get("value") if series else None
        if value is None and "total_value" in row:
            value = row["total_value"].get("value")
        if isinstance(value, (int, float)):
            values[row["name"]] = float(value)
    for name in wanted:
        if name not in values and name not in unavailable:
            unavailable[name] = "not returned by the API"
    return values, unavailable
