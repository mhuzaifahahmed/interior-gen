"""Auth: verifies Clerk-issued session JWTs and exposes the require_user()
dependency that gates the generator endpoints. Clerk (https://clerk.com)
owns the entire identity system now - signup, login, Google sign-in, session
issuance - this module's only job is to verify the token a Clerk-
authenticated frontend sends and extract the Clerk user id from it. See
CLAUDE.md's "Authentication" section for the full migration story (this
replaced a hand-written bcrypt + Starlette-session + Google OAuth stack).
"""

from dataclasses import dataclass

from clerk_backend_api.security import verify_token
from clerk_backend_api.security.types import TokenVerificationError, VerifyTokenOptions
from fastapi import Depends, HTTPException, Request

from app.config import settings


@dataclass(frozen=True)
class AuthUser:
    """Just the Clerk user id (e.g. "user_2abc...") - Clerk's own JWT session
    tokens don't carry email/name by default, and nothing server-side needs
    them: the frontend reads profile info (name, email, avatar) directly off
    Clerk's own `Clerk.user` object client-side, never through this backend.
    `id` doubles as the S3 path segment for this user's uploads/renders
    (users/{id}/...) - Clerk ids are alphanumeric + underscore, already a
    safe path segment with no separate validation needed, unlike the old
    hand-picked `username` field this replaced.
    """

    id: str


def get_current_user(request: Request) -> AuthUser | None:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        return None

    try:
        payload = verify_token(token, VerifyTokenOptions(secret_key=settings.clerk_secret_key))
    except TokenVerificationError:
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None
    return AuthUser(id=user_id)


def require_user(user: AuthUser | None = Depends(get_current_user)) -> AuthUser:
    if user is None:
        raise HTTPException(401, "login required")
    return user
