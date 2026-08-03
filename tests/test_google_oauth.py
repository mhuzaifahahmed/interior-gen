import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app import google_oauth
from app.main import app


def _configure_google(monkeypatch):
    monkeypatch.setattr(main_module.settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(main_module.settings, "google_client_secret", "test-client-secret")


def _start_login(client) -> str:
    """Hits /api/auth/google/login and returns the `state` value it stashed in
    the session (read back off the redirect URL to Google), so a test can
    complete the round trip with a state the callback will actually accept.
    """
    res = client.get("/api/auth/google/login", follow_redirects=False)
    assert res.status_code in (302, 307)
    location = res.headers["location"]
    assert location.startswith(google_oauth.AUTHORIZE_URL)
    state = parse_qs(urlparse(location).query)["state"][0]
    return state


def test_google_login_503_when_not_configured(monkeypatch):
    # Explicitly cleared, not relying on default settings being empty - a real
    # .env (this machine's included) can have real Google credentials set,
    # which would make an assumption-based version of this test flaky/wrong.
    # google_login() must fail closed when unconfigured, not redirect to a
    # broken Google URL.
    monkeypatch.setattr(main_module.settings, "google_client_id", "")
    monkeypatch.setattr(main_module.settings, "google_client_secret", "")
    with TestClient(app) as client:
        res = client.get("/api/auth/google/login", follow_redirects=False)
        assert res.status_code == 503


def test_google_login_redirects_to_google_when_configured(monkeypatch):
    _configure_google(monkeypatch)
    with TestClient(app) as client:
        state = _start_login(client)
        assert state  # a real, non-empty CSRF token was generated


def test_google_callback_creates_new_user(monkeypatch):
    _configure_google(monkeypatch)
    sub = f"google-{uuid.uuid4().hex[:10]}"
    email = f"newgoogleuser_{uuid.uuid4().hex[:8]}@example.com"

    monkeypatch.setattr(google_oauth, "exchange_code_for_token", lambda code: "fake-access-token")
    monkeypatch.setattr(
        google_oauth,
        "fetch_userinfo",
        lambda token: {"sub": sub, "email": email, "name": "New Google User"},
    )

    with TestClient(app) as client:
        state = _start_login(client)
        res = client.get(
            f"/api/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
        )
        assert res.status_code in (302, 307)
        assert res.headers["location"] == "/"

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        data = me.json()
        assert data["email"] == email
        assert data["full_name"] == "New Google User"
        # Username was auto-derived, not left blank/None.
        assert data["username"]


def test_google_callback_links_existing_password_account(monkeypatch):
    _configure_google(monkeypatch)
    email = f"linkme_{uuid.uuid4().hex[:8]}@example.com"
    sub = f"google-{uuid.uuid4().hex[:10]}"

    with TestClient(app) as client:
        signup_body = {
            "username": f"linkme_{uuid.uuid4().hex[:8]}",
            "email": email,
            "password": "correct-horse-battery-staple",
        }
        assert client.post("/api/auth/signup", json=signup_body).status_code == 200
        client.post("/api/auth/logout")

        monkeypatch.setattr(google_oauth, "exchange_code_for_token", lambda code: "fake-access-token")
        monkeypatch.setattr(
            google_oauth, "fetch_userinfo", lambda token: {"sub": sub, "email": email, "name": None}
        )

        state = _start_login(client)
        res = client.get(
            f"/api/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
        )
        assert res.status_code in (302, 307)

        me = client.get("/api/auth/me")
        assert me.status_code == 200
        # Logged into the SAME pre-existing account (same username), not a
        # newly-created duplicate for the same email.
        assert me.json()["username"] == signup_body["username"]


def test_google_callback_second_login_reuses_linked_account(monkeypatch):
    _configure_google(monkeypatch)
    email = f"repeatgoogle_{uuid.uuid4().hex[:8]}@example.com"
    sub = f"google-{uuid.uuid4().hex[:10]}"
    monkeypatch.setattr(google_oauth, "exchange_code_for_token", lambda code: "fake-access-token")
    monkeypatch.setattr(
        google_oauth, "fetch_userinfo", lambda token: {"sub": sub, "email": email, "name": "Repeat User"}
    )

    with TestClient(app) as client:
        state = _start_login(client)
        client.get(f"/api/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)
        first_username = client.get("/api/auth/me").json()["username"]
        client.post("/api/auth/logout")

        state = _start_login(client)
        client.get(f"/api/auth/google/callback?code=fake-code&state={state}", follow_redirects=False)
        second_username = client.get("/api/auth/me").json()["username"]

        assert first_username == second_username


def test_google_callback_rejects_state_mismatch(monkeypatch):
    _configure_google(monkeypatch)
    with TestClient(app) as client:
        _start_login(client)  # sets a real oauth_state in the session
        res = client.get(
            "/api/auth/google/callback?code=fake-code&state=wrong-state", follow_redirects=False
        )
        assert res.status_code in (302, 307)
        assert res.headers["location"] == "/login?error=google_auth_failed"


def test_google_callback_redirects_on_google_error_param(monkeypatch):
    _configure_google(monkeypatch)
    with TestClient(app) as client:
        state = _start_login(client)
        res = client.get(
            f"/api/auth/google/callback?error=access_denied&state={state}", follow_redirects=False
        )
        assert res.headers["location"] == "/login?error=google_auth_failed"


def test_google_callback_redirects_on_token_exchange_failure(monkeypatch):
    _configure_google(monkeypatch)

    def _boom(code):
        raise httpx.HTTPStatusError("bad code", request=None, response=None)

    monkeypatch.setattr(google_oauth, "exchange_code_for_token", _boom)

    with TestClient(app) as client:
        state = _start_login(client)
        res = client.get(
            f"/api/auth/google/callback?code=fake-code&state={state}", follow_redirects=False
        )
        assert res.headers["location"] == "/login?error=google_auth_failed"
