"""Runtime-editable settings that take effect without a Render redeploy -
currently just the three Kaggle Cloudflare tunnel URLs (room-redesign, Build
a House elevation, AI Concept Layout), all of which rotate every time their
notebook session restarts (see CLAUDE.md's "Kaggle has no permanent URL"
note). Previously the only way to update one was editing Render's env var and
waiting for a full redeploy - painful enough in practice that, once the
OpenAI reliability fallback was disabled (see Settings.room_openai_fallback_
enabled), a stale URL directly failed every real generation with no fast way
to fix it. Now stored in the DB (AppSetting) and editable from /admin (see
app/main.py's admin_get_kaggle_urls/admin_set_kaggle_urls) - takes effect
within _CACHE_TTL_SECONDS, not a redeploy.

A DB value, when set and non-blank, OVERRIDES the .env-configured Settings
default - never validated/parsed here, just an opaque string swap. Blank/
unset DB value means "use whatever .env has", so an install where nothing
has ever been set in /admin behaves identically to before this feature
existed.
"""

import logging
import threading
import time

from sqlmodel import Session

from app.db import engine
from app.models import AppSetting, _now

logger = logging.getLogger(__name__)

# Known keys - reused as both the AppSetting.key value and (not coincidentally)
# the matching Settings/.env field name, so the relationship between "the
# .env fallback" and "the DB override" stays obvious at every call site.
KAGGLE_API_URL_KEY = "kaggle_api_url"
KAGGLE_HOUSE_API_URL_KEY = "kaggle_house_api_url"
KAGGLE_AUTOCAD_API_URL_KEY = "kaggle_autocad_api_url"

KAGGLE_URL_KEYS = (KAGGLE_API_URL_KEY, KAGGLE_HOUSE_API_URL_KEY, KAGGLE_AUTOCAD_API_URL_KEY)

# Short TTL cache so a hot polling loop (kaggle.py's batch-job status poll,
# every 3s for up to 420s) doesn't hit the DB on every single iteration, while
# still picking up an admin's update quickly - far faster than a Render
# redeploy, the whole point of this feature. invalidate_cache() is also
# called right after every admin write, so in practice a fresh save is
# visible on the very next request, not just within this TTL.
_CACHE_TTL_SECONDS = 15
_cache: dict[str, tuple[float, str]] = {}
_cache_lock = threading.Lock()


def get_effective_url(key: str, fallback: str) -> str:
    """Returns the DB-stored override for `key` if one is set (and
    non-blank), else `fallback` (the .env/Settings value). Best-effort: any
    DB error (e.g. this table not yet migrated on an old running instance)
    degrades to `fallback` rather than breaking generation over a
    settings-lookup problem - this must never be the reason a real request
    fails.
    """
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1] or fallback

    value = ""
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, key)
            value = row.value if row else ""
    except Exception:
        logger.exception("failed to read dynamic setting %s from DB; using .env fallback", key)

    with _cache_lock:
        _cache[key] = (now, value)

    return value or fallback


def get_all_effective_urls(env_fallbacks: dict[str, str]) -> dict[str, dict[str, str]]:
    """Used by the admin panel to show, per key, both the effective value AND
    whether it's a DB override or falling back to .env - `env_fallbacks` is
    {key: settings_value} for the 3 known keys."""
    result = {}
    for key in KAGGLE_URL_KEYS:
        fallback = env_fallbacks.get(key, "")
        db_value = _get_raw_db_value(key)
        result[key] = {
            "value": db_value or fallback,
            "source": "database" if db_value else "env",
        }
    return result


def _get_raw_db_value(key: str) -> str:
    try:
        with Session(engine) as session:
            row = session.get(AppSetting, key)
            return row.value if row else ""
    except Exception:
        logger.exception("failed to read dynamic setting %s from DB", key)
        return ""


def set_effective_url(key: str, value: str) -> None:
    """Upserts `key` -> `value` (stripped; an empty string clears the
    override, reverting to whatever .env has). Invalidates the cache
    immediately so the change is visible on the very next request rather
    than waiting out _CACHE_TTL_SECONDS."""
    value = (value or "").strip()
    with Session(engine) as session:
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
        else:
            row.value = value
            row.updated_at = _now()
        session.add(row)
        session.commit()
    invalidate_cache()


def invalidate_cache() -> None:
    with _cache_lock:
        _cache.clear()
