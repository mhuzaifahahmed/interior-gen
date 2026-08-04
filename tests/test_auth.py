import uuid

from fastapi.testclient import TestClient

from app.main import app


def _unique_signup_body(**overrides):
    # Random suffix so repeated test runs against the real dev DB (this
    # project's tests don't isolate the DB - see CLAUDE.md's testing
    # convention) never collide on a previously-created username/email.
    suffix = uuid.uuid4().hex[:10]
    body = {
        "username": f"testuser_{suffix}",
        "email": f"testuser_{suffix}@example.com",
        "password": "correct-horse-battery-staple",
        "full_name": "Test User",
        "role": "architect",
    }
    body.update(overrides)
    return body


def test_signup_creates_user_and_logs_in():
    with TestClient(app) as client:
        body = _unique_signup_body()
        res = client.post("/api/auth/signup", json=body)
        assert res.status_code == 200
        data = res.json()
        assert data["username"] == body["username"]
        assert data["email"] == body["email"]
        assert "password" not in data
        assert "password_hash" not in data

        # Session cookie set by signup - /me should now resolve without a
        # separate login call.
        me_res = client.get("/api/auth/me")
        assert me_res.status_code == 200
        assert me_res.json()["username"] == body["username"]


def test_signup_rejects_duplicate_username():
    with TestClient(app) as client:
        body = _unique_signup_body()
        assert client.post("/api/auth/signup", json=body).status_code == 200

        dup = _unique_signup_body(username=body["username"])  # same username, different email
        res = client.post("/api/auth/signup", json=dup)
        assert res.status_code == 409


def test_signup_rejects_duplicate_email():
    with TestClient(app) as client:
        body = _unique_signup_body()
        assert client.post("/api/auth/signup", json=body).status_code == 200

        dup = _unique_signup_body(email=body["email"])  # same email, different username
        res = client.post("/api/auth/signup", json=dup)
        assert res.status_code == 409


def test_signup_rejects_invalid_username():
    with TestClient(app) as client:
        res = client.post("/api/auth/signup", json=_unique_signup_body(username="Has Spaces!"))
        assert res.status_code == 400


def test_signup_rejects_short_password():
    with TestClient(app) as client:
        res = client.post("/api/auth/signup", json=_unique_signup_body(password="short"))
        assert res.status_code == 400


def test_signup_rejects_email_without_tld():
    # Real bug: "xyz@gmail" (no ".com"/TLD at all) used to pass the old
    # "@" in email check and create a real account.
    with TestClient(app) as client:
        res = client.post("/api/auth/signup", json=_unique_signup_body(email="xyz@gmail"))
        assert res.status_code == 400


def test_login_with_username_and_with_email():
    with TestClient(app) as client:
        body = _unique_signup_body()
        assert client.post("/api/auth/signup", json=body).status_code == 200
        client.post("/api/auth/logout")

        res = client.post(
            "/api/auth/login", json={"identifier": body["username"], "password": body["password"]}
        )
        assert res.status_code == 200

        client.post("/api/auth/logout")
        res = client.post(
            "/api/auth/login", json={"identifier": body["email"], "password": body["password"]}
        )
        assert res.status_code == 200


def test_login_rejects_wrong_password():
    with TestClient(app) as client:
        body = _unique_signup_body()
        assert client.post("/api/auth/signup", json=body).status_code == 200
        client.post("/api/auth/logout")

        res = client.post(
            "/api/auth/login", json={"identifier": body["username"], "password": "wrong-password"}
        )
        assert res.status_code == 401


def test_login_rejects_unknown_identifier():
    with TestClient(app) as client:
        res = client.post(
            "/api/auth/login", json={"identifier": "no-such-user-at-all", "password": "whatever123"}
        )
        assert res.status_code == 401


def test_logout_clears_session():
    with TestClient(app) as client:
        body = _unique_signup_body()
        client.post("/api/auth/signup", json=body)
        assert client.get("/api/auth/me").status_code == 200

        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").status_code == 401


def test_me_requires_login():
    with TestClient(app) as client:
        res = client.get("/api/auth/me")
        assert res.status_code == 401


def test_login_and_signup_pages_serve():
    with TestClient(app) as client:
        assert client.get("/login").status_code == 200
        assert client.get("/signup").status_code == 200
