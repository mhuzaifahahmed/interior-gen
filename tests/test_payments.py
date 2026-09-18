"""Unit tests for app/payments.py - httpx is monkeypatched throughout, no
real network calls (same "never hit a real vendor in tests" convention as
every other provider - see CLAUDE.md's "Testing convention"). The real
sandbox shape these mocks mirror (order/v1/init's response, the
/checkout/pay URL, the beacon/order_id/redirect_url/cancel_url query params)
was confirmed live against this project's real sandbox keys on 2026-09-17 -
see app/payments.py's module docstring."""

import hashlib
import hmac

import httpx
import pytest

from app import payments


class _FakeResponse:
    def __init__(self, status_code=200, json_body=None):
        self.status_code = status_code
        self._json_body = json_body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


def test_create_checkout_session_returns_tracker_and_checkout_url(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_client_id", "sec_test")
    monkeypatch.setattr(payments.settings, "safepay_environment", "sandbox")

    def fake_post(url, json, timeout):
        assert url == "https://sandbox.api.getsafepay.com/order/v1/init"
        assert json["client"] == "sec_test"
        assert json["amount"] == 2499
        assert json["currency"] == "PKR"
        return _FakeResponse(200, {"data": {"token": "track_abc123"}, "status": {"message": "success"}})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = payments.create_checkout_session(
        2499, "intent-1", "https://backend.example/callback?order_id=intent-1", "https://frontend.example/?payment=cancelled"
    )

    assert result["tracker"] == "track_abc123"
    assert result["checkout_url"].startswith("https://sandbox.api.getsafepay.com/checkout/pay?")
    assert "beacon=track_abc123" in result["checkout_url"]
    assert "env=sandbox" in result["checkout_url"]
    # redirect_url/cancel_url must be urlencoded, not raw (a raw "&"/"?"
    # inside them would corrupt the outer query string).
    assert "redirect_url=https%3A%2F%2Fbackend.example" in result["checkout_url"]
    assert "cancel_url=https%3A%2F%2Ffrontend.example" in result["checkout_url"]


def test_create_checkout_session_raises_when_not_configured(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_client_id", "")
    with pytest.raises(payments.SafepayError):
        payments.create_checkout_session(2499, "intent-1", "https://a", "https://b")


def test_create_checkout_session_raises_on_missing_token(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_client_id", "sec_test")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _FakeResponse(200, {"data": {}}))
    with pytest.raises(payments.SafepayError):
        payments.create_checkout_session(2499, "intent-1", "https://a", "https://b")


def test_create_checkout_session_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_client_id", "sec_test")

    def fake_post(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(payments.SafepayError):
        payments.create_checkout_session(2499, "intent-1", "https://a", "https://b")


def test_verify_callback_signature_accepts_a_real_hmac(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_secret_key", "shh")
    tracker = "track_abc123"
    signature = hmac.new(b"shh", tracker.encode("utf-8"), hashlib.sha256).hexdigest()
    assert payments.verify_callback_signature(tracker, signature) is True


def test_verify_callback_signature_rejects_a_forged_signature(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_secret_key", "shh")
    assert payments.verify_callback_signature("track_abc123", "not-the-real-signature") is False


def test_verify_callback_signature_rejects_when_unconfigured(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_secret_key", "")
    assert payments.verify_callback_signature("track_abc123", "anything") is False


def test_verify_callback_signature_rejects_empty_inputs(monkeypatch):
    monkeypatch.setattr(payments.settings, "safepay_secret_key", "shh")
    assert payments.verify_callback_signature("", "anything") is False
    assert payments.verify_callback_signature("track_abc123", "") is False


def test_fetch_order_details_returns_the_real_data_object(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda url, timeout: _FakeResponse(200, {"data": {"state": "TRACKER_STARTED", "transaction": None}})
    )
    assert payments.fetch_order_details("track_abc123") == {"state": "TRACKER_STARTED", "transaction": None}


def test_fetch_order_details_returns_none_on_request_failure(monkeypatch):
    def fake_get(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert payments.fetch_order_details("track_abc123") is None


# Real, live-verified shapes (2026-09-17) - a fresh tracker (right after
# order/v1/init, before any payment attempt) has transaction: null; a
# genuinely completed payment has state "TRACKER_ENDED" AND a fully
# populated transaction object (id/token/fees/net/reference/signature).
_FRESH_TRACKER = {"state": "TRACKER_STARTED", "transaction": None}
_CANCELLED_TRACKER = {"state": "TRACKER_ENDED", "state_reason": "cancelled_by_user", "transaction": None}
_COMPLETED_TRACKER = {
    "state": "TRACKER_ENDED",
    "transaction": {"id": 23845, "token": "trans_67dbc7eb", "reference": "917939", "fees": 443.39, "net": 6555.61},
}


@pytest.mark.parametrize(
    "order_details,expected",
    [
        (_COMPLETED_TRACKER, True),
        (_FRESH_TRACKER, False),
        (_CANCELLED_TRACKER, False),  # same terminal state as completed, but no transaction
        (None, False),
        ({}, False),
    ],
)
def test_is_completed_order(order_details, expected):
    assert payments.is_completed_order(order_details) is expected
