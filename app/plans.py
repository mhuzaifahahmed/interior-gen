"""Subscription plan definitions and quota enforcement - see
future-plans/subscription-and-access-roadmap.md for the full approved
pricing/access design this implements (Chunk 1 of the build sequence: the
logged-in Free-tier quota gate only - anonymous pre-login trial and
per-request Kaggle/OpenAI routing are separate, later chunks).

Deliberately processor-agnostic: a plan is just a string on UserPlan.plan.
Whichever payment processor is eventually chosen only needs to flip that
field (e.g. from a webhook handler), never touch this module's math.
"""

import math
from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.config import settings
from app.models import UserPlan

# Rolling window, not a shared calendar-month cutover - see the roadmap doc's
# "Quota reset" decision. Anchored per-user to UserPlan.quota_window_start.
QUOTA_WINDOW_DAYS = 30

FREE = "free"
PRO = "pro"
STUDIO = "studio"
VALID_PLANS = (FREE, PRO, STUDIO)

ROOM_KAGGLE = "room_kaggle"
ROOM_OPENAI = "room_openai"
HOUSE_KAGGLE = "house_kaggle"
HOUSE_OPENAI = "house_openai"

# One entry per (generation kind, backend) pair used as both the PLAN_QUOTAS
# key and the UserPlan counter-column name - see _usage_field()/_limit_for().
_QUOTA_KEYS = (ROOM_KAGGLE, ROOM_OPENAI, HOUSE_KAGGLE, HOUSE_OPENAI)

# Numbers straight from the roadmap doc's approved pricing table
# (2026-09-03, "Approve as proposed"). math.inf for "unlimited fair-use" -
# exact and self-documenting, same convention room_specs.py already uses for
# its own "no real limit" case.
PLAN_QUOTAS: dict[str, dict[str, float]] = {
    FREE: {
        ROOM_KAGGLE: 5,
        ROOM_OPENAI: 0,  # Free tier has no OpenAI access at all, not just a zero allowance
        HOUSE_KAGGLE: 3,
        HOUSE_OPENAI: 0,
    },
    PRO: {
        ROOM_KAGGLE: 150,
        ROOM_OPENAI: 30,
        HOUSE_KAGGLE: 60,
        HOUSE_OPENAI: 10,
    },
    STUDIO: {
        ROOM_KAGGLE: math.inf,
        ROOM_OPENAI: 100,
        HOUSE_KAGGLE: math.inf,
        HOUSE_OPENAI: 40,
    },
}


# Real monthly PKR prices - the same numbers documented in CLAUDE.md's
# approved pricing table, now a real value app/payments.py's Safepay checkout
# reads (previously these only existed as hardcoded strings in
# static/index.html's pricing cards and the JazzCash modal's JS constants -
# this is the single source of truth a real checkout call now uses too).
PLAN_PRICES_PKR: dict[str, int] = {PRO: 2499, STUDIO: 6999}

# Materials-only retry (app/main.py's POST /api/projects/{id}/materials/retry) -
# how many times a single project's materials/pricing lookup may be manually
# re-run per plan, WITHOUT consuming a new generation credit. Same numbers as
# CLAUDE.md's "Free retries/generation" pricing-table row (previously
# documented as "planned, not built") - this materials-only retry is the
# first real instantiation of that row, deliberately scoped narrower than the
# full "regenerate this exact project" concept the roadmap doc originally
# described (which needs re-running the paid image generation too, and stays
# deferred - see future-plans/subscription-and-access-roadmap.md).
MATERIALS_RETRY_LIMITS: dict[str, int] = {FREE: 0, PRO: 2, STUDIO: 5}


def materials_retry_limit(plan: str) -> int:
    return MATERIALS_RETRY_LIMITS.get(plan, 0)


_LABELS = {
    ROOM_KAGGLE: "our model Room Redesign",
    ROOM_OPENAI: "OpenAI Room Redesign",
    HOUSE_KAGGLE: "our model Build a House",
    HOUSE_OPENAI: "OpenAI Build a House",
}
_PLAN_LABELS = {FREE: "Free", PRO: "Pro", STUDIO: "Studio"}


def backend_bucket(provider_setting: str) -> str:
    """Maps a settings.image_provider/house_image_provider value onto the
    quota system's two buckets. Only "openai" is billed differently - every
    self-hosted backend (kaggle, modal) shares the "kaggle" bucket, since
    they're all the free/cheap self-hosted option from a cost/quota
    standpoint, matching the roadmap doc's own "Kaggle/'our model'" framing
    (modal is documented elsewhere as dormant, not a live default, but this
    keeps the mapping correct if it's ever reactivated)."""
    return "openai" if provider_setting == "openai" else "kaggle"


# Chunk 3 (future-plans/subscription-and-access-roadmap.md): which plans may
# pick OpenAI explicitly for a single generation, instead of always getting
# whichever backend the app is configured to use by default. Matches the
# approved pricing table's "Model access" row exactly - Free is "our model
# only", Pro/Studio get "Kaggle + OpenAI toggle".
PLAN_ALLOWS_MODEL_CHOICE: dict[str, bool] = {FREE: False, PRO: True, STUDIO: True}


def resolve_preferred_backend(plan: str | None, requested: str | None) -> str | None:
    """Decides whether a generation request's requested backend should
    actually override the app's configured default.

    Returns "kaggle"/"openai" when the override should apply, or None when it
    should NOT (the caller should then fall back to whatever the app is
    already configured to use - see app/main.py's callers, which pass None
    straight through to run_pipeline()/run_house_pipeline() so an unaffected
    request's provider call is byte-for-byte identical to before this
    parameter existed).

    `plan=None` means an ANONYMOUS (pre-login trial) caller - always allowed
    to choose, per the roadmap's literal pre-login spec ("user may choose
    OpenAI or Kaggle for each"). A real plan that isn't in
    PLAN_ALLOWS_MODEL_CHOICE (i.e. Free) - or a `requested` value that isn't
    exactly "kaggle"/"openai" - never overrides; client input is never
    trusted to bypass a plan restriction, same "silently correct nonsense
    rather than error the whole request" treatment already used elsewhere in
    app/main.py's form validation.
    """
    if requested not in ("kaggle", "openai"):
        return None
    if plan is not None and not PLAN_ALLOWS_MODEL_CHOICE.get(plan, False):
        return None
    return requested


class QuotaExceededError(Exception):
    """Raised by consume_quota() when the requested (kind, backend) generation
    would exceed the user's plan allowance. Carries enough detail for the
    caller to build a clear 403 response - see app/main.py's usage."""

    def __init__(self, plan: str, quota_key: str, limit: float):
        self.plan = plan
        self.quota_key = quota_key
        self.limit = limit
        super().__init__(f"plan {plan!r} quota exceeded for {quota_key!r} (limit={limit})")

    def user_message(self) -> str:
        plan_label = _PLAN_LABELS.get(self.plan, self.plan)
        generation_label = _LABELS.get(self.quota_key, self.quota_key)
        if self.limit == 0:
            return (
                f"{generation_label} generation isn't included in the {plan_label} plan. "
                "Upgrade to Pro or Studio to unlock it."
            )
        return (
            f"You've used all {int(self.limit)} {generation_label} generations included in the "
            f"{plan_label} plan this month. Upgrade for a higher quota, or wait for your quota to reset."
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _quota_key(kind: str, backend: str) -> str:
    key = f"{kind}_{backend}"
    if key not in _QUOTA_KEYS:
        raise ValueError(f"unknown (kind, backend) pair: {kind!r}, {backend!r}")
    return key


def get_or_create_user_plan(session: Session, user_id: str) -> UserPlan:
    """Lazily creates a Free-plan row the first time a user's plan is looked
    up - there's no signup hook to create this eagerly (Clerk owns signup;
    this backend only ever sees a user on their first authenticated request).
    """
    plan_row = session.get(UserPlan, user_id)
    if plan_row is None:
        plan_row = UserPlan(user_id=user_id)
        session.add(plan_row)
        session.commit()
        session.refresh(plan_row)
    return plan_row


def roll_quota_window_if_needed(session: Session, plan_row: UserPlan) -> UserPlan:
    """Advances the rolling 30-day window forward and zeroes every usage
    counter once `now` has passed it. Lazy (checked on each quota lookup),
    not a scheduled job - matches this project's existing preference for
    computed-on-read state over background schedulers (e.g. quota_window_start
    itself, not a cron, is the source of truth for whether a reset is due).

    Rolls forward from the STORED window_start by whole QUOTA_WINDOW_DAYS
    increments (not just `window_start = now`) so a user who is inactive for,
    say, 65 days doesn't get an extra-long "3rd window" - it lands on the
    same cadence a continuously-active user would have seen.
    """
    window = timedelta(days=QUOTA_WINDOW_DAYS)
    stored_start = plan_row.quota_window_start
    if stored_start.tzinfo is None:
        # Same SQLite-drops-tzinfo-on-round-trip reality documented in
        # app/main.py's _utc_isoformat() - every stored value IS UTC by
        # construction, this just makes comparisons against an aware `now()`
        # safe.
        stored_start = stored_start.replace(tzinfo=timezone.utc)

    now = _now()
    if now < stored_start + window:
        return plan_row

    elapsed_windows = (now - stored_start) // window
    plan_row.quota_window_start = stored_start + window * elapsed_windows
    plan_row.room_kaggle_used = 0
    plan_row.room_openai_used = 0
    plan_row.house_kaggle_used = 0
    plan_row.house_openai_used = 0
    session.add(plan_row)
    session.commit()
    session.refresh(plan_row)
    return plan_row


def consume_quota(session: Session, user_id: str, kind: str, backend: str) -> UserPlan:
    """The single entry point generator endpoints call before accepting a new
    generation request. Looks up (creating if needed) the user's plan, rolls
    the quota window if due, checks the requested (kind, backend) generation
    against their plan's limit, and - if allowed - increments the matching
    counter and returns the updated row.

    Raises QuotaExceededError (not a bare bool) so the caller has everything
    needed to build a specific "upgrade to Pro" message rather than a generic
    403. Increments at REQUEST time, not on successful pipeline completion -
    a deliberate simplification (same "every attempt counts" spirit the
    roadmap doc already applies to Free-tier retries) rather than requiring
    the background pipeline to write back to this table from its own thread.
    """
    quota_key = _quota_key(kind, backend)

    plan_row = get_or_create_user_plan(session, user_id)
    plan_row = roll_quota_window_if_needed(session, plan_row)

    # Dev/testing bypass (settings.unlimited_test_user_ids) - see
    # app/config.py's docstring. Usage is still tracked (so /api/plan stays
    # honest about real activity) - only the limit check is skipped.
    limit = math.inf if user_id in settings.unlimited_test_user_id_set else PLAN_QUOTAS[plan_row.plan][quota_key]
    used = getattr(plan_row, f"{quota_key}_used")
    if used >= limit:
        raise QuotaExceededError(plan_row.plan, quota_key, limit)

    setattr(plan_row, f"{quota_key}_used", used + 1)
    plan_row.lifetime_generations += 1
    session.add(plan_row)
    session.commit()
    session.refresh(plan_row)
    return plan_row


def set_plan(session: Session, user_id: str, plan: str) -> UserPlan:
    """The admin panel's plan-change action (POST /api/admin/users/{id}/plan)
    - the real, human-operated replacement for a payment webhook until a
    processor is chosen (see future-plans/subscription-and-access-roadmap.md).
    Does NOT touch usage counters or the quota window - only the plan label
    and its timestamp, so upgrading mid-window doesn't reset/grant a fresh
    allowance (a deliberate simplification; revisit if that's ever surprising
    in practice)."""
    if plan not in VALID_PLANS:
        raise ValueError(f"invalid plan {plan!r} - must be one of {VALID_PLANS}")
    plan_row = get_or_create_user_plan(session, user_id)
    plan_row.plan = plan
    plan_row.plan_updated_at = _now()
    session.add(plan_row)
    session.commit()
    session.refresh(plan_row)
    return plan_row


def reset_usage(session: Session, user_id: str) -> UserPlan:
    """Zeroes the four rolling *_used counters and restarts the quota window
    from now - an admin convenience (e.g. for a demo, or a goodwill reset).
    Deliberately does NOT touch lifetime_generations, which is meant to be a
    true all-time total regardless of any window reset."""
    plan_row = get_or_create_user_plan(session, user_id)
    plan_row.quota_window_start = _now()
    plan_row.room_kaggle_used = 0
    plan_row.room_openai_used = 0
    plan_row.house_kaggle_used = 0
    plan_row.house_openai_used = 0
    session.add(plan_row)
    session.commit()
    session.refresh(plan_row)
    return plan_row


def capture_identity(session: Session, user_id: str, email: str = "", display_name: str = "") -> UserPlan:
    """Best-effort upsert of a user's email/display name onto their
    UserPlan row, so the admin panel can find/label people - the backend
    otherwise never sees either (Clerk's session JWT carries only the user
    id, see app/auth.py's AuthUser docstring). Client-supplied, same trust
    posture already used for display_name in S3 keys
    (app/main.py::_storage_namespace) - fine for an admin *label*, never used
    for auth/ownership decisions. Only writes when a value is given AND
    actually changed, so a plain page load doesn't churn a write every time."""
    email = (email or "").strip()
    display_name = (display_name or "").strip()
    if not email and not display_name:
        return get_or_create_user_plan(session, user_id)

    plan_row = get_or_create_user_plan(session, user_id)
    changed = False
    if email and plan_row.email != email:
        plan_row.email = email
        changed = True
    if display_name and plan_row.display_name != display_name:
        plan_row.display_name = display_name
        changed = True
    if changed:
        session.add(plan_row)
        session.commit()
        session.refresh(plan_row)
    return plan_row


def list_all_user_plans(session: Session) -> list[UserPlan]:
    """Every known user, for the admin panel's user table. Small dataset by
    construction (one row per person who has ever logged in) - no pagination
    needed at this scale."""
    from sqlmodel import select

    return list(session.exec(select(UserPlan).order_by(UserPlan.created_at)))


def plan_status(session: Session, user_id: str) -> dict:
    """Read-only usage/limit snapshot for a user - used by the frontend to
    show quota remaining and by the upgrade-prompt UI (Chunk 4). Rolls the
    window first so a stale pre-reset snapshot is never shown."""
    plan_row = get_or_create_user_plan(session, user_id)
    plan_row = roll_quota_window_if_needed(session, plan_row)

    is_unlimited_test_user = user_id in settings.unlimited_test_user_id_set
    quotas = PLAN_QUOTAS[plan_row.plan]
    usage = {key: getattr(plan_row, f"{key}_used") for key in _QUOTA_KEYS}
    remaining = {
        key: (
            None
            if is_unlimited_test_user or quotas[key] == math.inf
            else max(0, quotas[key] - usage[key])
        )
        for key in _QUOTA_KEYS
    }
    window_reset_at = plan_row.quota_window_start + timedelta(days=QUOTA_WINDOW_DAYS)

    return {
        "plan": plan_row.plan,
        "quotas": {key: (None if quotas[key] == math.inf else quotas[key]) for key in _QUOTA_KEYS},
        "used": usage,
        "remaining": remaining,
        "quota_window_start": plan_row.quota_window_start,
        "quota_window_reset_at": window_reset_at,
    }
