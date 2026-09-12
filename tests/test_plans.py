import math
from datetime import timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.models import UserPlan
from app.plans import (
    FREE,
    PRO,
    QUOTA_WINDOW_DAYS,
    QuotaExceededError,
    backend_bucket,
    consume_quota,
    get_or_create_user_plan,
    plan_status,
    roll_quota_window_if_needed,
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
