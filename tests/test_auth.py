from fastapi.testclient import TestClient

import app.main as main_module
from app.auth import get_current_user, require_user
from app.main import app
from tests.conftest import login_as


class FakeStorage:
    """Empty-list endpoints (GET /api/projects with no projects) still call
    get_storage() once even though they never touch it - must be mocked so a
    real S3 client is never constructed in tests, see CLAUDE.md's testing
    convention."""

    def put(self, key, data, content_type="image/png"):
        raise AssertionError("not expected to be called in these tests")

    def get(self, key):
        raise AssertionError("not expected to be called in these tests")

    def url(self, key):
        raise AssertionError("not expected to be called in these tests")


class _FakeRequest:
    def __init__(self, headers: dict):
        self.headers = headers


def test_get_current_user_returns_none_without_authorization_header():
    assert get_current_user(_FakeRequest({})) is None


def test_get_current_user_returns_none_for_non_bearer_header():
    assert get_current_user(_FakeRequest({"authorization": "Basic dXNlcjpwYXNz"})) is None


def test_get_current_user_returns_none_for_invalid_token():
    # No "test|" prefix - the fake verify_token (tests/conftest.py) rejects
    # this exactly like a real Clerk verify_token() would reject a garbage
    # token.
    assert get_current_user(_FakeRequest({"authorization": "Bearer not-a-real-token"})) is None


def test_get_current_user_returns_auth_user_for_valid_token():
    user = get_current_user(_FakeRequest({"authorization": "Bearer test|user_abc123"}))
    assert user is not None
    assert user.id == "user_abc123"


def test_require_user_raises_401_when_get_current_user_returns_none():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        require_user(None)
    assert exc_info.value.status_code == 401


def test_require_user_passes_through_a_real_user():
    user = get_current_user(_FakeRequest({"authorization": "Bearer test|user_xyz"}))
    assert require_user(user).id == "user_xyz"


def test_protected_endpoint_401s_without_a_token(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        res = client.get("/api/projects")
        assert res.status_code == 401


def test_protected_endpoint_401s_with_a_malformed_token(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        res = client.get("/api/projects", headers={"Authorization": "Bearer garbage"})
        assert res.status_code == 401


def test_protected_endpoint_succeeds_with_a_valid_token(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        login_as(client)
        res = client.get("/api/projects")
        assert res.status_code == 200
        assert res.json() == []


def test_login_and_signup_pages_serve():
    with TestClient(app) as client:
        assert client.get("/login").status_code == 200
        assert client.get("/signup").status_code == 200
