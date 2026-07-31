"""Auth: bcrypt password hashing, cookie-session lookup, and the
require_user() dependency that gates the generator endpoints. Real, not a
placeholder - see CLAUDE.md's "Authentication" section for the full design
(why Starlette SessionMiddleware over JWTs, why bcrypt directly instead of
passlib, the username-as-S3-path-segment constraint).
"""

import re

import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlmodel import Session, select

from app.db import get_session
from app.models import User

USERNAME_PATTERN = re.compile(r"^[a-z0-9_]{3,32}$")


def validate_username(username: str) -> str:
    """Raises ValueError with a user-facing message if invalid. Deliberately
    strict (lowercase letters/digits/underscore, 3-32 chars) since username is
    used verbatim as an S3 key path segment (users/{username}/...) - this
    charset can never produce a path-traversal or otherwise unsafe key.
    """
    username = username.strip().lower()
    if not USERNAME_PATTERN.match(username):
        raise ValueError(
            "Username must be 3-32 characters, lowercase letters/numbers/underscore only."
        )
    return username


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash - never a match, never a 500.
        return False


def get_current_user(request: Request, session: Session = Depends(get_session)) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return session.get(User, user_id)


def require_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(401, "login required")
    return user


def get_user_by_username_or_email(session: Session, identifier: str) -> User | None:
    identifier = identifier.strip().lower()
    return session.exec(
        select(User).where((User.username == identifier) | (User.email == identifier))
    ).first()
