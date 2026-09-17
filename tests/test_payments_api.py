"""Integration tests for the Safepay checkout/callback endpoints
(app/main.py) and the materials-only retry endpoint - see CLAUDE.md's
"Subscription plans, quotas, admin panel, and payments" section. Follows
test_api.py's established pattern: no DB isolation (hits the real configured
DATABASE_URL), so every id is a random uuid to avoid collisions with
leftover rows from previous runs. app.payments' httpx calls are monkeypatched
at the module level (never hit the real Safepay sandbox in tests, same
convention as every other vendor - see CLAUDE.md's "Testing convention").
"""

import hashlib
import hmac
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import payments
from app.db import engine
from app.main import app
from app.models import PaymentIntent
from app.plans import get_or_create_user_plan
from tests.conftest import login_as


def _fake_create_checkout_session(amount_pkr, order_id, redirect_url, cancel_url):
    return {"tracker": f"track_{order_id}", "checkout_url": f"https://sandbox.api.getsafepay.com/checkout/pay?beacon=track_{order_id}"}


def test_create_safepay_checkout_creates_a_pending_intent_and_returns_a_url(monkeypatch):
    monkeypatch.setattr(payments, "create_checkout_session", _fake_create_checkout_session)

    with TestClient(app) as client:
        user_id = login_as(client)
        res = client.post("/api/payments/safepay/checkout", data={"plan": "pro"})

    assert res.status_code == 200
    body = res.json()
    assert body["checkout_url"].startswith("https://sandbox.api.getsafepay.com/checkout/pay?")

    from sqlmodel import select

    with Session(engine) as session:
        intent = session.exec(select(PaymentIntent).where(PaymentIntent.user_id == user_id)).one()
    assert intent.plan == "pro"
    assert intent.amount_pkr == 2499
    assert intent.status == "pending"
    assert intent.tracker == f"track_{intent.id}"


def test_create_safepay_checkout_rejects_an_invalid_plan(monkeypatch):
    monkeypatch.setattr(payments, "create_checkout_session", _fake_create_checkout_session)
    with TestClient(app) as client:
        login_as(client)
        res = client.post("/api/payments/safepay/checkout", data={"plan": "free"})
    assert res.status_code == 400


def test_create_safepay_checkout_requires_login(monkeypatch):
    monkeypatch.setattr(payments, "create_checkout_session", _fake_create_checkout_session)
    with TestClient(app) as client:
        res = client.post("/api/payments/safepay/checkout", data={"plan": "pro"})
    assert res.status_code == 401


def test_create_safepay_checkout_surfaces_a_safepay_error_as_502(monkeypatch):
    def raising(*a, **k):
        raise payments.SafepayError("sandbox is down")

    monkeypatch.setattr(payments, "create_checkout_session", raising)
    with TestClient(app) as client:
        login_as(client)
        res = client.post("/api/payments/safepay/checkout", data={"plan": "pro"})
    assert res.status_code == 502


def _make_intent(user_id: str, plan: str = "pro", tracker: str = "") -> PaymentIntent:
    intent = PaymentIntent(user_id=user_id, plan=plan, amount_pkr=2499, tracker=tracker or f"track_{uuid.uuid4().hex}")
    with Session(engine) as session:
        session.add(intent)
        session.commit()
        session.refresh(intent)
    return intent


def test_safepay_callback_upgrades_the_plan_on_a_completed_payment(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="pro")
    signature = hmac.new(b"test-secret", intent.tracker.encode("utf-8"), hashlib.sha256).hexdigest()

    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    monkeypatch.setattr(payments, "fetch_order_state", lambda tracker: "TRACKER_COMPLETED")

    with TestClient(app) as client:
        res = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": signature},
            follow_redirects=False,
        )

    assert res.status_code in (302, 307)
    assert "payment=success" in res.headers["location"]

    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "pro"
        refreshed = session.get(PaymentIntent, intent.id)
        assert refreshed.status == "completed"


def test_safepay_callback_does_not_upgrade_on_an_incomplete_payment(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="studio")
    signature = hmac.new(b"test-secret", intent.tracker.encode("utf-8"), hashlib.sha256).hexdigest()

    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    monkeypatch.setattr(payments, "fetch_order_state", lambda tracker: "TRACKER_CANCELLED")

    with TestClient(app) as client:
        res = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": signature},
            follow_redirects=False,
        )

    assert "payment=failed" in res.headers["location"]
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "free"


def test_safepay_callback_rejects_a_forged_signature(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="pro")

    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    fetch_called = []
    monkeypatch.setattr(payments, "fetch_order_state", lambda tracker: fetch_called.append(tracker))

    with TestClient(app) as client:
        res = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": "forged"},
            follow_redirects=False,
        )

    assert "payment=invalid" in res.headers["location"]
    assert fetch_called == []  # never reaches the server-to-server status check
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "free"


def test_safepay_callback_ignores_an_unknown_order_id(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    with TestClient(app) as client:
        res = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": "does-not-exist", "tracker": "track_x", "signature": "whatever"},
            follow_redirects=False,
        )
    assert "payment=error" in res.headers["location"]


def test_safepay_callback_is_idempotent_on_a_replayed_request(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="pro")
    signature = hmac.new(b"test-secret", intent.tracker.encode("utf-8"), hashlib.sha256).hexdigest()

    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    monkeypatch.setattr(payments, "fetch_order_state", lambda tracker: "TRACKER_COMPLETED")

    with TestClient(app) as client:
        first = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": signature},
            follow_redirects=False,
        )
        assert "payment=success" in first.headers["location"]

        # Replaying the same callback finds the intent no longer "pending" -
        # must not re-process/double-upgrade.
        second = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": signature},
            follow_redirects=False,
        )
    assert "payment=error" in second.headers["location"]


def _webhook_signature(body: bytes) -> str:
    return hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()


def test_safepay_webhook_upgrades_the_plan_on_a_completed_event(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="studio")
    monkeypatch.setattr(payments.settings, "safepay_webhook_secret", "test-webhook-secret")

    body = f'{{"type":"payment.completed","data":{{"token":"{intent.tracker}","state":"TRACKER_COMPLETED"}}}}'.encode()
    signature = _webhook_signature(body)

    with TestClient(app) as client:
        res = client.post(
            "/api/payments/safepay/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-SFPY-Signature": signature},
        )

    assert res.status_code == 200
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "studio"
        refreshed = session.get(PaymentIntent, intent.id)
        assert refreshed.status == "completed"


def test_safepay_webhook_rejects_a_bad_signature(monkeypatch):
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="pro")
    monkeypatch.setattr(payments.settings, "safepay_webhook_secret", "test-webhook-secret")

    body = f'{{"data":{{"token":"{intent.tracker}","state":"TRACKER_COMPLETED"}}}}'.encode()

    with TestClient(app) as client:
        res = client.post(
            "/api/payments/safepay/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-SFPY-Signature": "forged"},
        )

    assert res.status_code == 400
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "free"


def test_safepay_webhook_ignores_an_unknown_tracker(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_webhook_secret", "test-webhook-secret")
    body = b'{"data":{"token":"track_does_not_exist","state":"TRACKER_COMPLETED"}}'
    signature = _webhook_signature(body)

    with TestClient(app) as client:
        res = client.post(
            "/api/payments/safepay/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-SFPY-Signature": signature},
        )
    assert res.status_code == 200
    assert res.json()["status"] == "ignored"


def test_safepay_webhook_and_callback_are_mutually_idempotent(monkeypatch):
    """The whole point of two independent confirmation paths is that
    whichever arrives first wins and the other is a safe no-op - simulates
    the callback landing first, then the webhook for the same tracker."""
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    intent = _make_intent(user_id, plan="pro")

    monkeypatch.setattr(payments.settings, "safepay_secret_key", "test-secret")
    monkeypatch.setattr(payments.settings, "safepay_webhook_secret", "test-webhook-secret")
    monkeypatch.setattr(payments, "fetch_order_state", lambda tracker: "TRACKER_COMPLETED")

    callback_signature = hmac.new(b"test-secret", intent.tracker.encode("utf-8"), hashlib.sha256).hexdigest()
    body = f'{{"data":{{"token":"{intent.tracker}","state":"TRACKER_COMPLETED"}}}}'.encode()
    webhook_signature = _webhook_signature(body)

    with TestClient(app) as client:
        callback_res = client.get(
            "/api/payments/safepay/callback",
            params={"order_id": intent.id, "tracker": intent.tracker, "signature": callback_signature},
            follow_redirects=False,
        )
        assert "payment=success" in callback_res.headers["location"]

        webhook_res = client.post(
            "/api/payments/safepay/webhook",
            content=body,
            headers={"Content-Type": "application/json", "X-SFPY-Signature": webhook_signature},
        )

    assert webhook_res.status_code == 200
    assert webhook_res.json()["status"] == "ignored"
    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        assert plan_row.plan == "pro"  # upgraded exactly once, not double-processed
