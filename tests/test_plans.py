import math
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import UserPlan
from app.plans import (
    FREE,
    PRO,
    QUOTA_WINDOW_DAYS,
    STUDIO,
    VALID_PLANS,
    QuotaExceededError,
    backend_bucket,
    capture_identity,
    consume_quota,
    get_or_create_user_plan,
    list_all_user_plans,
    plan_status,
    reset_usage,
    resolve_preferred_backend,
    roll_quota_window_if_needed,
    set_plan,
)


def make_test_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_get_or_create_user_plan_creates_a_free_row():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")

    assert plan_row.plan == FREE
    assert plan_row.room_kaggle_used == 0


def test_get_or_create_user_plan_returns_the_same_row_on_second_call():
    engine = make_test_engine()
    with Session(engine) as session:
        first = get_or_create_user_plan(session, "user_1")
        first.plan = PRO
        session.add(first)
        session.commit()

        second = get_or_create_user_plan(session, "user_1")

    assert second.plan == PRO


def test_consume_quota_increments_the_matching_counter():
    engine = make_test_engine()
    with Session(engine) as session:
        consume_quota(session, "user_1", "room", "kaggle")
        plan_row = get_or_create_user_plan(session, "user_1")

    assert plan_row.room_kaggle_used == 1
    assert plan_row.room_openai_used == 0
    assert plan_row.house_kaggle_used == 0


def test_consume_quota_raises_once_free_tier_room_kaggle_limit_is_reached():
    engine = make_test_engine()
    with Session(engine) as session:
        for _ in range(5):
            consume_quota(session, "user_1", "room", "kaggle")

        with pytest.raises(QuotaExceededError) as exc_info:
            consume_quota(session, "user_1", "room", "kaggle")

    assert exc_info.value.quota_key == "room_kaggle"
    assert exc_info.value.limit == 5
    assert "Upgrade" in exc_info.value.user_message()


def test_consume_quota_raises_immediately_for_free_tier_openai_with_zero_allowance():
    engine = make_test_engine()
    with Session(engine) as session:
        with pytest.raises(QuotaExceededError) as exc_info:
            consume_quota(session, "user_1", "room", "openai")

    assert exc_info.value.limit == 0
    assert "isn't included" in exc_info.value.user_message()


def test_consume_quota_studio_plan_kaggle_is_unlimited():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.plan = "studio"
        session.add(plan_row)
        session.commit()

        for _ in range(500):
            consume_quota(session, "user_1", "room", "kaggle")

        final = get_or_create_user_plan(session, "user_1")

    assert final.room_kaggle_used == 500


def test_consume_quota_different_kinds_have_independent_counters():
    engine = make_test_engine()
    with Session(engine) as session:
        consume_quota(session, "user_1", "room", "kaggle")
        consume_quota(session, "user_1", "house", "kaggle")
        plan_row = get_or_create_user_plan(session, "user_1")

    assert plan_row.room_kaggle_used == 1
    assert plan_row.house_kaggle_used == 1


def test_roll_quota_window_if_needed_resets_counters_after_the_window_passes():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.room_kaggle_used = 5
        plan_row.quota_window_start = plan_row.quota_window_start - timedelta(days=QUOTA_WINDOW_DAYS + 1)
        session.add(plan_row)
        session.commit()

        rolled = roll_quota_window_if_needed(session, plan_row)

    assert rolled.room_kaggle_used == 0


def test_roll_quota_window_if_needed_leaves_counters_alone_within_the_window():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.room_kaggle_used = 3
        session.add(plan_row)
        session.commit()

        rolled = roll_quota_window_if_needed(session, plan_row)

    assert rolled.room_kaggle_used == 3


def test_plan_status_reports_remaining_and_unlimited_as_none():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.plan = "studio"
        session.add(plan_row)
        session.commit()
        consume_quota(session, "user_1", "room", "kaggle")

        status = plan_status(session, "user_1")

    assert status["plan"] == "studio"
    assert status["used"]["room_kaggle"] == 1
    assert status["remaining"]["room_kaggle"] is None  # unlimited
    assert status["remaining"]["room_openai"] == 100 - 0


def test_consume_quota_bypasses_the_limit_for_a_configured_unlimited_test_user(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "unlimited_test_user_ids", "user_exempt")
    engine = make_test_engine()
    with Session(engine) as session:
        for _ in range(20):
            consume_quota(session, "user_exempt", "room", "kaggle")
        plan_row = get_or_create_user_plan(session, "user_exempt")

    assert plan_row.room_kaggle_used == 20  # still tracked, just never blocked


def test_consume_quota_still_enforces_for_a_non_exempt_user(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "unlimited_test_user_ids", "user_exempt")
    engine = make_test_engine()
    with Session(engine) as session:
        for _ in range(5):
            consume_quota(session, "user_normal", "room", "kaggle")
        with pytest.raises(QuotaExceededError):
            consume_quota(session, "user_normal", "room", "kaggle")


def test_backend_bucket_maps_openai_and_everything_else():
    assert backend_bucket("openai") == "openai"
    assert backend_bucket("kaggle") == "kaggle"
    assert backend_bucket("modal") == "kaggle"


def test_resolve_preferred_backend_anonymous_caller_can_choose_either():
    assert resolve_preferred_backend(None, "openai") == "openai"
    assert resolve_preferred_backend(None, "kaggle") == "kaggle"


def test_resolve_preferred_backend_free_plan_is_never_overridden():
    assert resolve_preferred_backend(FREE, "openai") is None
    assert resolve_preferred_backend(FREE, "kaggle") is None


def test_resolve_preferred_backend_pro_and_studio_can_choose():
    assert resolve_preferred_backend(PRO, "openai") == "openai"
    assert resolve_preferred_backend(PRO, "kaggle") == "kaggle"
    assert resolve_preferred_backend(STUDIO, "openai") == "openai"


def test_resolve_preferred_backend_ignores_an_invalid_or_missing_request():
    assert resolve_preferred_backend(PRO, None) is None
    assert resolve_preferred_backend(PRO, "modal") is None
    assert resolve_preferred_backend(PRO, "") is None
    assert resolve_preferred_backend(None, "modal") is None


def test_consume_quota_increments_lifetime_generations():
    engine = make_test_engine()
    with Session(engine) as session:
        consume_quota(session, "user_1", "room", "kaggle")
        consume_quota(session, "user_1", "house", "kaggle")
        plan_row = get_or_create_user_plan(session, "user_1")

    assert plan_row.lifetime_generations == 2


def test_consume_quota_lifetime_generations_survives_a_window_roll():
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.lifetime_generations = 10
        plan_row.quota_window_start = plan_row.quota_window_start - timedelta(days=QUOTA_WINDOW_DAYS + 1)
        session.add(plan_row)
        session.commit()

        consume_quota(session, "user_1", "room", "kaggle")
        final = get_or_create_user_plan(session, "user_1")

    # The window rolled (room_kaggle_used reset to 0 then incremented to 1),
    # but lifetime_generations must never be zeroed by a window roll.
    assert final.room_kaggle_used == 1
    assert final.lifetime_generations == 11


def test_set_plan_changes_the_plan_and_timestamp():
    engine = make_test_engine()
    with Session(engine) as session:
        before = get_or_create_user_plan(session, "user_1")
        updated = set_plan(session, "user_1", PRO)

    assert updated.plan == PRO
    assert updated.plan_updated_at >= before.plan_updated_at


def test_set_plan_rejects_an_invalid_plan_string():
    engine = make_test_engine()
    with Session(engine) as session:
        with pytest.raises(ValueError):
            set_plan(session, "user_1", "enterprise")


def test_set_plan_creates_the_row_if_it_does_not_exist_yet():
    engine = make_test_engine()
    with Session(engine) as session:
        updated = set_plan(session, "brand_new_user", STUDIO)

    assert updated.plan == STUDIO
    assert updated.plan in VALID_PLANS


def test_reset_usage_zeroes_used_counters_but_keeps_lifetime_total():
    engine = make_test_engine()
    with Session(engine) as session:
        consume_quota(session, "user_1", "room", "kaggle")
        consume_quota(session, "user_1", "house", "kaggle")

        reset = reset_usage(session, "user_1")

    assert reset.room_kaggle_used == 0
    assert reset.house_kaggle_used == 0
    assert reset.lifetime_generations == 2


def test_capture_identity_writes_email_and_display_name():
    engine = make_test_engine()
    with Session(engine) as session:
        row = capture_identity(session, "user_1", email="jane@example.com", display_name="Jane Doe")

    assert row.email == "jane@example.com"
    assert row.display_name == "Jane Doe"


def test_capture_identity_does_not_overwrite_with_blank_values():
    engine = make_test_engine()
    with Session(engine) as session:
        capture_identity(session, "user_1", email="jane@example.com", display_name="Jane Doe")
        row = capture_identity(session, "user_1", email="", display_name="")

    assert row.email == "jane@example.com"
    assert row.display_name == "Jane Doe"


def test_capture_identity_updates_on_a_changed_value():
    engine = make_test_engine()
    with Session(engine) as session:
        capture_identity(session, "user_1", email="old@example.com")
        row = capture_identity(session, "user_1", email="new@example.com")

    assert row.email == "new@example.com"


def test_list_all_user_plans_returns_every_known_user():
    engine = make_test_engine()
    with Session(engine) as session:
        get_or_create_user_plan(session, "user_1")
        get_or_create_user_plan(session, "user_2")
        rows = list_all_user_plans(session)

    assert {row.user_id for row in rows} == {"user_1", "user_2"}


def test_plan_status_handles_a_naive_stored_quota_window_start():
    # Real SQLite round-trip drops tzinfo (same lesson as _utc_isoformat() in
    # app/main.py) - roll_quota_window_if_needed() must not crash comparing a
    # naive stored datetime against an aware now().
    engine = make_test_engine()
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, "user_1")
        plan_row.quota_window_start = plan_row.quota_window_start.replace(tzinfo=None)
        session.add(plan_row)
        session.commit()

        status = plan_status(session, "user_1")

    assert status["plan"] == FREE
