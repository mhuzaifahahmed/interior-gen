"""Google OAuth 2.0 Authorization Code flow ("Continue with Google" on
login/signup, app/main.py's /api/auth/google/login + /callback). Plain httpx
calls to Google's own endpoints - no authlib/oauth library dependency, since
the flow is exactly three requests (build a URL, exchange a code, fetch
userinfo) and this project already depends on httpx for the Gemini/SerpApi
providers. Kept in its own module (mirrors app/providers/serpapi.py's shape)
so tests can monkeypatch exchange_code_for_token/fetch_userinfo without
mocking httpx globally - see tests/test_google_oauth.py.
"""

from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"

SCOPES = "openid email profile"


def is_configured() -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def build_authorize_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(code: str) -> str:
    """Returns the access_token. Raises httpx.HTTPStatusError on failure
    (a bad/expired code, redirect_uri mismatch, etc.) - the callback endpoint
    turns that into a user-facing redirect, not a 500.
    """
    res = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    res.raise_for_status()
    return res.json()["access_token"]


def fetch_userinfo(access_token: str) -> dict:
    """Returns Google's userinfo payload - at minimum "sub" (stable per-account
    id) and "email"; "name" is best-effort (present whenever the Google
    account has one public).
    """
    res = httpx.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    res.raise_for_status()
    return res.json()
