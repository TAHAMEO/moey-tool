"""OAuth app credentials for Google (YouTube, Google Drive) and Meta (Instagram).

Signing in with Google or Instagram needs an "app" registered with them once.
Where CLIPFORGE finds it, first match wins:

1. **Saved in CLIPFORGE** — Settings → Accounts → "Set up", stored encrypted in
   the database and used immediately (no files to edit, no restart).
2. **Built in** — ``oauth-apps.json`` shipped by whoever distributed this copy
   (see docs/ONE_CLICK_CONNECT.md). With it, connecting is one click for every
   user of that build.
3. **Environment** — ``CLIPFORGE_GOOGLE_CLIENT_ID``/``_SECRET`` and
   ``CLIPFORGE_META_APP_ID``/``_SECRET`` (the original way; still supported).

Google apps are either ``web`` (the redirect address is registered in Google
Cloud) or ``desktop`` (an installed-app client: Google accepts the loopback
address the app runs on, ``http://localhost:<port>``, without registration —
ideal for a copy running on the owner's own computer).

Instagram apps redirect either straight to CLIPFORGE's public HTTPS address
(``direct``) or through a small static relay page on HTTPS (``relay``) that only
ever forwards the sign-in result to CLIPFORGE on the same computer
(``http://localhost``/``127.0.0.1``) — Meta requires HTTPS redirect addresses.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import Secret
from ..security.crypto import delete_secret, read_secret_json, store_secret

log = logging.getLogger(__name__)

GOOGLE_PURPOSE = "oauth-app:google"
META_PURPOSE = "oauth-app:meta"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}
GOOGLE_CALLBACK = "/api/connections/google/callback"
INSTAGRAM_CALLBACK = "/api/connections/instagram/callback"


@dataclass
class GoogleApp:
    client_id: str
    client_secret: str
    kind: str  # web | desktop
    source: str  # saved | built_in | env


@dataclass
class MetaApp:
    app_id: str
    app_secret: str
    relay_url: str | None
    source: str


# ---------------------------------------------------------------- built in
def _built_in() -> dict:
    settings = get_settings()
    candidates = [settings.oauth_apps_file] if settings.oauth_apps_file else []
    candidates += [Path("/config/oauth-apps.json"), settings.data_dir / "oauth-apps.json"]
    for path in candidates:
        try:
            if path and Path(path).is_file():
                return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable OAuth app file %s: %s", path, exc)
    return {}


def _saved(db: Session, purpose: str) -> dict | None:
    row = db.execute(select(Secret).where(Secret.purpose == purpose).order_by(Secret.created_at.desc())).scalars().first()
    if row is None:
        return None
    try:
        return read_secret_json(db, row.id)
    except Exception as exc:  # noqa: BLE001 - a broken entry must not break connecting via the other sources
        log.warning("Saved %s could not be read: %s", purpose, exc)
        return None


# ------------------------------------------------------------------ google
def google_app(db: Session | None) -> GoogleApp | None:
    if db is not None and (saved := _saved(db, GOOGLE_PURPOSE)):
        return GoogleApp(saved["client_id"], saved["client_secret"], saved.get("kind", "web"), "saved")
    built = _built_in().get("google") or {}
    if built.get("client_id") and built.get("client_secret"):
        return GoogleApp(built["client_id"], built["client_secret"], built.get("kind", "desktop"), "built_in")
    settings = get_settings()
    if settings.google_client_id and settings.google_client_secret:
        return GoogleApp(settings.google_client_id, settings.google_client_secret.get_secret_value(),
                         settings.google_client_kind, "env")
    return None


def meta_app(db: Session | None) -> MetaApp | None:
    if db is not None and (saved := _saved(db, META_PURPOSE)):
        return MetaApp(saved["app_id"], saved["app_secret"], saved.get("relay_url") or None, "saved")
    built = _built_in().get("instagram") or {}
    if built.get("app_id") and built.get("app_secret"):
        return MetaApp(str(built["app_id"]), built["app_secret"], built.get("relay_url") or None, "built_in")
    settings = get_settings()
    if settings.meta_app_id and settings.meta_app_secret:
        return MetaApp(settings.meta_app_id, settings.meta_app_secret.get_secret_value(), settings.instagram_relay_url, "env")
    return None


GOOGLE_ID_RE = re.compile(r"^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$")
META_ID_RE = re.compile(r"^[0-9]{6,20}$")


def validate_relay_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("The relay page must be an https:// address.")
    return url.strip()


def save_google(db: Session, *, client_id: str, client_secret: str, kind: str) -> GoogleApp:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not GOOGLE_ID_RE.match(client_id):
        raise ValueError("That doesn't look like a Google OAuth client ID (it ends in .apps.googleusercontent.com).")
    if len(client_secret) < 10:
        raise ValueError("Paste the client secret shown next to the client ID.")
    if kind not in ("web", "desktop"):
        raise ValueError("Choose Web application or Desktop app.")
    forget(db, "google")
    store_secret(db, GOOGLE_PURPOSE, {"client_id": client_id, "client_secret": client_secret, "kind": kind}, hint=client_id[:12] + "…")
    return GoogleApp(client_id, client_secret, kind, "saved")


def save_meta(db: Session, *, app_id: str, app_secret: str, relay_url: str | None) -> MetaApp:
    app_id, app_secret = app_id.strip(), app_secret.strip()
    if not META_ID_RE.match(app_id):
        raise ValueError("The Instagram app ID is a number (Meta app dashboard → Instagram → API setup with Instagram login).")
    if len(app_secret) < 16:
        raise ValueError("Paste the Instagram app secret shown next to the app ID.")
    relay = validate_relay_url(relay_url)
    forget(db, "instagram")
    store_secret(db, META_PURPOSE, {"app_id": app_id, "app_secret": app_secret, "relay_url": relay}, hint=app_id)
    return MetaApp(app_id, app_secret, relay, "saved")


def forget(db: Session, provider: str) -> None:
    purpose = GOOGLE_PURPOSE if provider == "google" else META_PURPOSE
    for row in db.execute(select(Secret).where(Secret.purpose == purpose)).scalars().all():
        delete_secret(db, row.id)


# --------------------------------------------------------------- redirects
def loopback_origin(origin: str | None) -> str | None:
    """``http://localhost:3000`` when the browser is on this computer, else None."""
    if not origin:
        return None
    parts = urlsplit(origin)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in {h.strip("[]") for h in LOOPBACK_HOSTS}:
        return None
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname if ":" not in (parts.hostname or "") else f"[{parts.hostname}]"
    return f"http://{host}{f':{port}' if port else ''}"


def public_origin() -> str:
    return get_settings().public_base_url.rstrip("/")


def google_redirect(app: GoogleApp | None, origin: str | None) -> tuple[str | None, str | None]:
    """(redirect URI, problem). Desktop clients only work from the computer CLIPFORGE runs on."""
    if app is not None and app.kind == "desktop":
        local = loopback_origin(origin)
        if local is None:
            return None, ("This Google app is a Desktop app, which only works on the computer CLIPFORGE runs on. "
                          "Open CLIPFORGE at http://localhost there to connect, or set up a Web application client.")
        return local + GOOGLE_CALLBACK, None
    return public_origin() + GOOGLE_CALLBACK, None


def instagram_redirect(app: MetaApp | None, origin: str | None) -> tuple[str | None, str | None, str | None]:
    """(redirect URI registered with Meta, where the relay forwards to, problem)."""
    if app is not None and app.relay_url:
        local = loopback_origin(origin)
        if local is None:
            return None, None, ("Connecting through the sign-in relay works from the computer CLIPFORGE runs on. "
                                "Open CLIPFORGE at http://localhost there to connect.")
        return app.relay_url, local + INSTAGRAM_CALLBACK, None
    base = public_origin()
    if not base.startswith("https://"):
        return None, None, ("Instagram only returns to https:// addresses. Use a sign-in relay page (see "
                            "docs/ONE_CLICK_CONNECT.md) or run CLIPFORGE on a server with HTTPS.")
    return base + INSTAGRAM_CALLBACK, None, None


def status(db: Session, origin: str | None = None) -> dict:
    google = google_app(db)
    meta = meta_app(db)
    g_redirect, g_problem = google_redirect(google, origin)
    i_redirect, i_target, i_problem = instagram_redirect(meta, origin)
    return {
        "google": {"configured": google is not None, "source": google.source if google else None,
                   "kind": google.kind if google else None, "client_id": google.client_id if google else None,
                   "redirect_uri": g_redirect, "problem": g_problem if google else None,
                   # what to register when setting up a Web client for this address
                   "web_redirect_uri": public_origin() + GOOGLE_CALLBACK},
        "instagram": {"configured": meta is not None, "source": meta.source if meta else None,
                      "app_id": meta.app_id if meta else None, "relay_url": meta.relay_url if meta else None,
                      "redirect_uri": i_redirect, "relay_target": i_target, "problem": i_problem if meta else None,
                      "direct_redirect_uri": public_origin() + INSTAGRAM_CALLBACK,
                      "https": public_origin().startswith("https://")},
    }


def new_state_id() -> str:
    return uuid.uuid4().hex
