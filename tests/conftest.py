import os

# MUST run before app.main (and therefore app.config's `settings = Settings()`
# singleton) is ever imported by any test module - pydantic-settings resolves
# a real OS environment variable ahead of the .env file, so setting this here,
# at conftest.py's own module level (pytest always loads a directory's
# conftest.py before collecting/importing test files in it), reliably wins
# over whatever FRONTEND_ORIGIN actually happens to be in the developer's real
# .env. Real bug this fixes: once a real Vercel URL was set in .env for the
# split-hosting deploy, EVERY test using TestClient started 401ing on any
# request made after login, because app/main.py builds its SessionMiddleware
# with a Secure-only cookie whenever settings.frontend_origin is set at
# import time - and TestClient's default plain http://testserver transport
# never sends Secure cookies back, silently losing the session on the very
# next request. Monkeypatching settings.frontend_origin from inside a fixture
# is NOT sufficient here (unlike most other settings) - app/main.py reads it
# once, at module import, to build the middleware stack, so anything patched
# afterward has no effect. A test that specifically wants to exercise the
# cross-origin path sets this env var back itself before importing app.main
# fresh (see tests/test_cross_origin.py).
os.environ["FRONTEND_ORIGIN"] = ""

import uuid

import pytest
from clerk_backend_api.security.types import TokenVerificationError, TokenVerificationErrorReason
from fastapi.testclient import TestClient

from app import auth as auth_module
from app.providers import analysis_cache


def _fake_verify_token(token, options):
    """Stands in for a real Clerk network call - accepts only tokens shaped
    like "test|<clerk_user_id>" (see login_as() below) and rejects
    everything else, the same shape a real Clerk verify_token() failure
    takes. Clerk is never called for real in tests, same convention as every
    other provider in this project - see CLAUDE.md's "Testing convention"."""
    if isinstance(token, str) and token.startswith("test|"):
        return {"sub": token.split("|", 1)[1]}
    raise TokenVerificationError(TokenVerificationErrorReason.TOKEN_INVALID)


@pytest.fixture(autouse=True)
def _fake_clerk_verification(monkeypatch):
    monkeypatch.setattr(auth_module, "verify_token", _fake_verify_token)


def login_as(client: TestClient, user_id: str | None = None) -> str:
    """Logs a TestClient in as a fake Clerk user by attaching a Bearer token
    every subsequent request on this client will send (TestClient persists
    headers set via client.headers.update() across requests). Returns the
    user id (a random Clerk-shaped id if none given) - used directly as the
    S3 path segment in storage-key assertions, same role the old `username`
    return value played before the Clerk migration.
    """
    user_id = user_id or f"user_{uuid.uuid4().hex[:24]}"
    client.headers.update({"Authorization": f"Bearer test|{user_id}"})
    return user_id


@pytest.fixture(autouse=True)
def _reset_analysis_cache():
    """The Gemini analysis cache (app/providers/analysis_cache.py) is
    deliberately module/process-global, not per-provider-instance - see its
    module docstring for why. That means it persists across tests in the
    same pytest process unless reset, which silently breaks any test that
    reuses the same fixed sample image bytes another test already cached a
    result for (a real failure this fixture fixes - two GeminiProvider
    tests using the same tiny sample PNG were seeing each other's cached
    results). Autouse so no test has to remember to do this itself.
    """
    analysis_cache.clear()
    yield
    analysis_cache.clear()
