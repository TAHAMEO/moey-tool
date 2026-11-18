from __future__ import annotations

from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD


def test_first_run_setup_flow(client):
    assert client.get("/api/auth/status").json() == {"setup_required": True}
    weak = client.post("/api/auth/setup", json={"email": OWNER_EMAIL, "password": "short"})
    assert weak.status_code == 400 and weak.json()["error"]["code"] == "weak_password"
    ok = client.post("/api/auth/setup", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert ok.status_code == 200
    body = ok.json()
    assert body["role"] == "owner" and body["csrf_token"]
    assert "cf_session" in client.cookies
    assert client.get("/api/auth/status").json() == {"setup_required": False}
    again = client.post("/api/auth/setup", json={"email": "x@example.com", "password": OWNER_PASSWORD})
    assert again.status_code == 409


def test_login_logout_and_csrf(owner_client):
    me = owner_client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["email"] == OWNER_EMAIL
    csrf = owner_client.headers.pop("x-csrf-token")
    blocked = owner_client.post("/api/control/pause")
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "csrf"
    owner_client.headers["x-csrf-token"] = csrf
    assert owner_client.post("/api/control/pause").status_code == 200
    assert owner_client.post("/api/auth/logout").status_code == 200
    assert owner_client.get("/api/auth/me").status_code == 401


def test_wrong_password_and_lockout(client, owner_client):
    owner_client.post("/api/auth/logout")
    for _ in range(8):
        response = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": "Wrong-Password-1"})
        assert response.status_code == 401
    locked = client.post("/api/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert locked.status_code == 429  # correct password is refused while locked out


def test_session_cookie_flags(client):
    response = client.post("/api/auth/setup", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


def test_roles_enforced(owner_client, client):
    created = owner_client.post("/api/auth/users", json={"email": "viewer@example.com", "password": "Viewer-Pass-77", "role": "viewer"})
    assert created.status_code == 200
    owner_client.post("/api/auth/logout")
    login = client.post("/api/auth/login", json={"email": "viewer@example.com", "password": "Viewer-Pass-77"})
    client.headers["x-csrf-token"] = login.json()["csrf_token"]
    assert client.get("/api/providers").status_code == 200  # viewers can read
    denied = client.post("/api/providers", json={"provider_type": "ollama", "base_url": "http://127.0.0.1:1/v1"})
    assert denied.status_code == 403


def test_security_headers(client):
    response = client.get("/api/health")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]


def test_audit_log_records_auth_events(owner_client):
    rows = owner_client.get("/api/audit").json()
    assert any(r["action"] == "auth.setup_owner" for r in rows)
