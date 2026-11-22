"""The single-person Windows app: no sign-in page, the browser on this PC is the owner."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def local_client(monkeypatch):
    from clipforge.api.app import create_app
    from clipforge.config import get_settings

    monkeypatch.setenv("CLIPFORGE_LOCAL_MODE", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        monkeypatch.delenv("CLIPFORGE_LOCAL_MODE")
        get_settings.cache_clear()


def test_no_sign_in_needed(local_client):
    status = local_client.get("/api/auth/status").json()
    assert status == {"setup_required": False, "local": True}
    me = local_client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    body = me.json()
    assert body["role"] == "owner" and body["local"] is True
    # The session works for changes (with its security token) like any signed-in session.
    local_client.headers["x-csrf-token"] = body["csrf_token"]
    assert local_client.get("/api/projects").status_code == 200
    # A second visit is the same person, not a new owner.
    again = local_client.get("/api/auth/me").json()
    assert again["id"] == body["id"]
    other_browser = TestClient(local_client.app)
    assert other_browser.get("/api/auth/me").json()["id"] == body["id"]


def test_changes_still_need_the_security_token(local_client):
    local_client.get("/api/auth/me")
    response = local_client.post("/api/batches", json={"name": "x"})
    assert response.status_code == 403


def test_server_installs_still_require_sign_in(client):
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/status").json()["local"] is False
