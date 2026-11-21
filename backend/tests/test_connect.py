"""One-click account connection (CONTRACT tests against a local fake of Google's and Instagram's OAuth endpoints).

Real sign-ins need a registered Google/Meta app and a person at the consent screen, so they are
BLOCKED here; these tests check every request CLIPFORGE makes and every way it finds the app.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from clipforge.config import get_settings
from clipforge.db.base import session_scope
from clipforge.db.models import OAuthState, Secret, SocialAccount
from clipforge.platforms import apps, google, instagram

pytestmark = pytest.mark.contract

GOOGLE_ID = "123456789-abcdefghij.apps.googleusercontent.com"


@pytest.fixture
def no_env_apps(monkeypatch, tmp_path):
    for key in ("CLIPFORGE_GOOGLE_CLIENT_ID", "CLIPFORGE_GOOGLE_CLIENT_SECRET", "CLIPFORGE_META_APP_ID", "CLIPFORGE_META_APP_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLIPFORGE_OAUTH_APPS_FILE", str(tmp_path / "none.json"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_app_sources_saved_beats_built_in_beats_environment(platforms, monkeypatch, tmp_path):
    with session_scope() as db:
        env = apps.google_app(db)
        assert (env.client_id, env.source, env.kind) == ("cid", "env", "web")
    built = tmp_path / "oauth-apps.json"
    built.write_text(json.dumps({"google": {"client_id": GOOGLE_ID, "client_secret": "GOCSPX-built", "kind": "desktop"},
                                 "instagram": {"app_id": "1234567890", "app_secret": "s" * 32,
                                               "relay_url": "https://relay.example/instagram.html"}}))
    monkeypatch.setenv("CLIPFORGE_OAUTH_APPS_FILE", str(built))
    get_settings.cache_clear()
    with session_scope() as db:
        assert apps.google_app(db).source == "built_in" and apps.google_app(db).kind == "desktop"
        assert apps.meta_app(db).relay_url == "https://relay.example/instagram.html"
        apps.save_google(db, client_id="987654321-zyxwvut.apps.googleusercontent.com", client_secret="GOCSPX-saved", kind="web")
    with session_scope() as db:
        saved = apps.google_app(db)
        assert saved.source == "saved" and saved.client_secret == "GOCSPX-saved"
        row = db.execute(select(Secret).where(Secret.purpose == apps.GOOGLE_PURPOSE)).scalar_one()
        assert b"GOCSPX-saved" not in row.ciphertext  # encrypted at rest
        apps.forget(db, "google")
    with session_scope() as db:
        assert apps.google_app(db).source == "built_in"


def test_saving_an_app_checks_it_with_google(platforms, owner_client, no_env_apps):
    bad_format = owner_client.put("/api/connections/apps/google", json={"client_id": "nope", "client_secret": "x" * 20})
    assert bad_format.status_code == 422 and "apps.googleusercontent.com" in bad_format.json()["error"]["message"]
    wrong = owner_client.put("/api/connections/apps/google", json={"client_id": GOOGLE_ID, "client_secret": "GOCSPX-wrong-one"})
    assert wrong.status_code == 422 and "doesn't recognise" in wrong.json()["error"]["message"]
    with session_scope() as db:
        assert apps.google_app(db) is None  # a rejected app is not kept
    platforms.google_clients.add((GOOGLE_ID, "GOCSPX-right-one"))
    ok = owner_client.put("/api/connections/apps/google", json={"client_id": GOOGLE_ID, "client_secret": "GOCSPX-right-one",
                                                                "kind": "desktop"})
    assert ok.status_code == 200 and ok.json()["verified"] is True
    status = owner_client.get("/api/connections?origin=http://localhost:3000").json()["apps"]["google"]
    assert status["configured"] and status["source"] == "saved" and status["kind"] == "desktop"
    assert status["redirect_uri"] == "http://localhost:3000/api/connections/google/callback"
    elsewhere = owner_client.get("/api/connections?origin=http://192.168.1.20:3000").json()["apps"]["google"]
    assert elsewhere["redirect_uri"] is None and "Desktop app" in elsewhere["problem"]


def test_desktop_app_one_click_youtube_connection(platforms, owner_client, no_env_apps):
    platforms.google_clients.add((GOOGLE_ID, "GOCSPX-right-one"))
    owner_client.put("/api/connections/apps/google", json={"client_id": GOOGLE_ID, "client_secret": "GOCSPX-right-one",
                                                           "kind": "desktop"})
    start = owner_client.post("/api/connections/google/start", json={"purpose": "youtube", "origin": "http://localhost:3000"})
    assert start.status_code == 200, start.text
    query = parse_qs(urlsplit(start.json()["auth_url"]).query)
    assert query["client_id"] == [GOOGLE_ID] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://localhost:3000/api/connections/google/callback"]
    assert "https://www.googleapis.com/auth/youtube.upload" in query["scope"][0] and query["access_type"] == ["offline"]
    state = query["state"][0]
    # Google sends the browser back to CLIPFORGE on this computer with a code.
    done = owner_client.get(f"/api/connections/google/callback?state={state}&code={platforms.google_code}", follow_redirects=False)
    assert done.status_code in (302, 307)
    assert done.headers["location"] == "http://localhost:3000/connected?connected=youtube"
    exchange = platforms.token_requests[-1]
    assert exchange["redirect_uri"] == "http://localhost:3000/api/connections/google/callback"
    assert exchange["code_verifier"] and exchange["client_secret"] == "GOCSPX-right-one"
    with session_scope() as db:
        account = db.execute(select(SocialAccount).where(SocialAccount.platform == "youtube")).scalar_one()
        assert account.status == "connected" and account.display_name == "Owner Channel"
        assert db.get(OAuthState, state) is None  # single use
    replay = owner_client.get(f"/api/connections/google/callback?state={state}&code={platforms.google_code}", follow_redirects=False)
    assert "error=" in replay.headers["location"]


def test_cancelled_sign_in_lands_back_with_an_error(platforms, owner_client):
    start = owner_client.post("/api/connections/google/start", json={"purpose": "youtube"})
    state = parse_qs(urlsplit(start.json()["auth_url"]).query)["state"][0]
    back = owner_client.get(f"/api/connections/google/callback?state={state}&error=access_denied", follow_redirects=False)
    assert back.headers["location"].endswith("/connected?error=access_denied")


def test_instagram_through_the_relay_page(platforms, owner_client, monkeypatch, no_env_apps):
    monkeypatch.setattr(instagram, "TOKEN_URL", f"{platforms.base}/oauth/access_token")
    insecure = owner_client.put("/api/connections/apps/instagram", json={"app_id": "1234567890", "app_secret": "a" * 32,
                                                                         "relay_url": "http://relay.example/ig.html"})
    assert insecure.status_code == 422 and "https" in insecure.json()["error"]["message"]
    saved = owner_client.put("/api/connections/apps/instagram", json={"app_id": "1234567890", "app_secret": "a" * 32,
                                                                      "relay_url": "https://relay.example/ig.html"})
    assert saved.status_code == 200
    start = owner_client.post("/api/connections/instagram/start", json={"origin": "http://localhost:3000"})
    query = parse_qs(urlsplit(start.json()["auth_url"]).query)
    assert query["redirect_uri"] == ["https://relay.example/ig.html"] and query["client_id"] == ["1234567890"]
    state = query["state"][0]
    assert state.startswith("instagram.") and state.endswith(".l3000")
    # The relay page forwards code+state to CLIPFORGE on this computer (see deploy/oauth-relay).
    done = owner_client.get(f"/api/connections/instagram/callback?state={state}&code={platforms.instagram_code}",
                            follow_redirects=False)
    assert done.headers["location"] == "http://localhost:3000/connected?connected=instagram"
    assert platforms.token_requests[-1]["redirect_uri"] == "https://relay.example/ig.html"  # must equal the authorize request's
    with session_scope() as db:
        account = db.execute(select(SocialAccount).where(SocialAccount.platform == "instagram")).scalar_one()
        assert account.display_name == "owner_reels" and account.meta["account_type"] == "BUSINESS"
    # Without a relay, http:// copies can't use Instagram's sign-in at all — said plainly.
    owner_client.put("/api/connections/apps/instagram", json={"app_id": "1234567890", "app_secret": "a" * 32})
    blocked = owner_client.post("/api/connections/instagram/start", json={"origin": "http://localhost:3000"})
    assert blocked.status_code == 400 and "https://" in blocked.json()["error"]["message"]


def test_loopback_origin_is_strict():
    assert apps.loopback_origin("http://localhost:3000") == "http://localhost:3000"
    assert apps.loopback_origin("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    for bad in ("https://localhost:3000", "http://localhost.evil.com:3000", "http://evil.com", "http://localhost:3000/x",
                "http://user@localhost:3000", "javascript:alert(1)", None, ""):
        assert apps.loopback_origin(bad) is None
    assert instagram.relay_target_code("http://localhost:3000") == "l3000"
    assert instagram.relay_target_code("http://127.0.0.1:8080") == "i8080"


def test_refresh_uses_the_app_that_issued_the_token(platforms, no_env_apps):
    from clipforge.security.crypto import store_secret

    with session_scope() as db:
        platforms.google_clients.add((GOOGLE_ID, "GOCSPX-right-one"))
        apps.save_google(db, client_id=GOOGLE_ID, client_secret="GOCSPX-right-one", kind="desktop")
        account = SocialAccount(platform="youtube", external_account_id="UC_X", display_name="x")
        db.add(account)
        db.flush()
        account.secret_id = store_secret(db, f"oauth:youtube:{account.id}", {"access_token": "old", "refresh_token": "r",
                                                                              "expires_at": "2000-01-01T00:00:00+00:00"}).id
        assert google.access_token(db, account) == "ya29.refreshed"
        apps.forget(db, "google")
        account.secret_id = store_secret(db, f"oauth:youtube:{account.id}:2", {"access_token": "old", "refresh_token": "r",
                                                                                "expires_at": "2000-01-01T00:00:00+00:00"}).id
        with pytest.raises(Exception, match="isn't set up any more"):
            google.access_token(db, account)
