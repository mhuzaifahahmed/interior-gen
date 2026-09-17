"""Safepay (https://getsafepay.com) payment processing - see CLAUDE.md's
"Payments" section for why Safepay was chosen over Stripe/PayFast/PayPro, and
app/config.py's safepay_* settings docstring for the full integration-shape
caveat. Deliberately isolated in its own module (mirrors app/plans.py's own
"a payment processor only ever needs to call set_plan()" isolation) - if
Safepay is ever swapped for another processor, only this file + its two
app/main.py endpoints should need to change.

REAL, CONFIRMED SHAPE (from Safepay's own public integration examples, not
guessed): POST {base}/order/v1/init creates a "Tracker" (their term for a
checkout session) and returns a `token` (e.g. "track_..."). The customer is
then sent to a hosted checkout URL built from that token. After payment,
Safepay redirects the browser back to a merchant-supplied `redirect_url` with
`Order ID` / `Reference Code` / `Tracker` / `Signature` query params, where
`Signature = HMAC_SHA256(tracker, secret_key)`.

LIVE-VERIFIED 2026-09-17 against this project's real sandbox keys (not just
docs): POST {base}/order/v1/init returns 200 with the exact {data:{token,
state:"TRACKER_STARTED",...}} shape assumed below, and GET
{base}/order/v1/{tracker} returns the same shape back. The hosted checkout
URL was NOT what Safepay's own older public examples show
(`{base}/components?...&beacon=...` 301-redirects to their marketing
homepage, getsafepay.pk - a dead/retired path) - the real one was found by
fetching the checkout SPA's own JS bundle
(sandbox.api.getsafepay.com/checkout/static/js/main.*.chunk.js) and reading
its actual React Router route table + query-param parsing code directly:
`{base}/checkout/pay?env=...&beacon=<tracker>&order_id=...&redirect_url=...
&cancel_url=...` (confirmed live: returns 200 with the real "Safepay
Checkout" SPA shell, no redirect). `beacon` is genuinely the tracker
param's name, not `token`/`tracker` - confirmed from the bundle's own
`fc(e.beacon)` -> `tracker` mapping.

STILL NOT LIVE-VERIFIED: an actual completed test-card payment through the
real checkout page (which needs a headless/real browser, not just HTTP
requests) - so _ORDER_COMPLETED_STATE_MARKERS below (the string(s) `state`
turns into on success) is still an educated guess from the state machine's
naming convention (TRACKER_STARTED -> ??? -> presumably something
containing COMPLETE/SUCCESS/PAID), not a confirmed value. Run one real
sandbox test-card payment through CHECKOUT_URL_TEMPLATE below and check the
real resulting `state` string before trusting this in front of a real user;
update only that constant if the real value differs.
"""

import hashlib
import hmac
import logging
import urllib.parse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

ORDER_INIT_TIMEOUT_SECONDS = 15
ORDER_STATUS_TIMEOUT_SECONDS = 15

# Real, confirmed state string for a fresh tracker (order/v1/init's own
# response shows this). The state a COMPLETED payment ends up in is one of
# the "NOT YET LIVE-VERIFIED" items above - every plausible variant is
# checked case-insensitively as a substring match, so this keeps working
# whichever exact spelling the real sandbox turns out to use.
_ORDER_COMPLETED_STATE_MARKERS = ("COMPLETE", "SUCCESS", "PAID")

# See this module's docstring - LIVE-VERIFIED path + param names (2026-09-17).
CHECKOUT_URL_TEMPLATE = (
    "{base}/checkout/pay?env={environment}&beacon={tracker}"
    "&order_id={order_id}&redirect_url={redirect_url}&cancel_url={cancel_url}"
)


class SafepayError(Exception):
    """Raised when Safepay's API itself returns an error or an unexpected
    shape - callers (app/main.py) turn this into a plain 502 rather than
    letting a raw httpx traceback leak to the frontend."""


def create_checkout_session(amount_pkr: int, order_id: str, redirect_url: str, cancel_url: str) -> dict:
    """Calls POST {base}/order/v1/init and returns
    {"tracker": "track_...", "checkout_url": "https://..."} for the caller to
    redirect the browser to. Raises SafepayError on any failure - there is no
    silent/best-effort fallback here (unlike this codebase's optional vendor
    slots), since a broken checkout call means the user literally cannot pay,
    which must surface as a clear error, not a quietly-degraded feature.
    """
    if not settings.safepay_client_id:
        raise SafepayError("Safepay is not configured (SAFEPAY_CLIENT_ID missing)")

    try:
        response = httpx.post(
            f"{settings.safepay_api_base}/order/v1/init",
            json={
                "client": settings.safepay_client_id,
                "amount": amount_pkr,
                "currency": "PKR",
                "environment": settings.safepay_environment,
            },
            timeout=ORDER_INIT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        logger.exception("Safepay order/v1/init request failed")
        raise SafepayError(f"Safepay checkout could not be created: {exc}") from exc

    tracker = (body.get("data") or {}).get("token")
    if not tracker:
        logger.error("Safepay order/v1/init returned no token: %r", body)
        raise SafepayError("Safepay checkout session did not return a token")

    # redirect_url/cancel_url are themselves full URLs (containing their own
    # "://", "?", "&") - urlencoded here so they survive as single opaque
    # values inside CHECKOUT_URL_TEMPLATE's outer query string instead of
    # corrupting it (e.g. a raw "&" in redirect_url would otherwise start a
    # new top-level param).
    checkout_url = CHECKOUT_URL_TEMPLATE.format(
        base=settings.safepay_api_base,
        environment=settings.safepay_environment,
        tracker=urllib.parse.quote(tracker, safe=""),
        order_id=urllib.parse.quote(order_id, safe=""),
        redirect_url=urllib.parse.quote(redirect_url, safe=""),
        cancel_url=urllib.parse.quote(cancel_url, safe=""),
    )
    return {"tracker": tracker, "checkout_url": checkout_url}


def verify_callback_signature(tracker: str, signature: str) -> bool:
    """HMAC-SHA256(tracker, secret_key) - the exact scheme shown in Safepay's
    own public integration examples for validating their redirect callback.
    Constant-time comparison (hmac.compare_digest), not `==`, so this can't
    be used as a timing side-channel to guess a valid signature."""
    if not settings.safepay_secret_key or not tracker or not signature:
        return False
    expected = hmac.new(
        settings.safepay_secret_key.encode("utf-8"), tracker.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def fetch_order_state(tracker: str) -> str | None:
    """Server-to-server double-check that a tracker's payment actually
    completed, not just that the redirect signature is genuine (the
    signature alone only proves Safepay generated this tracker, not that the
    customer actually finished paying - a valid signature can arrive on a
    cancelled/abandoned checkout too). Returns the raw `state` string, or
    None on any request failure - callers treat None as "could not confirm,
    do not upgrade the plan" rather than guessing."""
    try:
        response = httpx.get(
            f"{settings.safepay_api_base}/order/v1/{tracker}",
            timeout=ORDER_STATUS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError:
        logger.exception("Safepay order/v1/%s status check failed", tracker)
        return None
    return (body.get("data") or {}).get("state")


def is_completed_state(state: str | None) -> bool:
    if not state:
        return False
    upper = state.upper()
    return any(marker in upper for marker in _ORDER_COMPLETED_STATE_MARKERS)


# Candidate header names for the webhook's signature - Safepay's own public
# docs reference "the X_SFPY_SIGNATURE header" in underscore form (their PHP
# SDK example), but the ACTUAL HTTP header name real deliveries use hasn't
# been observed yet on this account (0 deliveries so far - no payment has
# completed through the real webhook endpoint yet). HTTP header names are
# case-insensitive and hyphens/underscores are sometimes normalized
# differently by different frameworks/proxies, so every plausible real-world
# spelling is checked - see verify_webhook_signature()'s caller
# (app/main.py's safepay_webhook()), which tries each of these against the
# incoming Headers object (itself already case-insensitive).
WEBHOOK_SIGNATURE_HEADER_CANDIDATES = ("X-SFPY-Signature", "X-Sfpy-Signature", "X_SFPY_SIGNATURE")


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """HMAC-SHA256 of the RAW request body (not re-serialized JSON, which
    would produce different bytes and always fail) using the webhook's own
    shared secret (Dashboard -> Payments 2.0 -> Developer -> Endpoints -
    separate from safepay_secret_key, which only verifies the redirect
    callback's tracker signature). Real, confirmed scheme per Safepay's own
    docs: "Safepay requires the raw body of the request to perform signature
    verification... compute the signature on your end [and] verify if the
    signature returned from Safepay is the same as the one computed."
    Constant-time comparison, same reasoning as verify_callback_signature().
    """
    if not settings.safepay_webhook_secret or not raw_body or not signature:
        return False
    expected = hmac.new(settings.safepay_webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_tracker_and_state(payload: dict) -> tuple[str | None, str | None]:
    """Best-effort extraction of (tracker, state) from a webhook event body -
    NOT a confirmed shape (see this module's docstring: 0 real deliveries
    observed on this account yet). Tries every plausible key name/nesting
    this codebase has actually confirmed elsewhere (order/v1/init and
    order/v1/{tracker} both nest under "data": {"token", "state"}) plus the
    most common alternate webhook-envelope convention used elsewhere in this
    same codebase (Clerk's own webhook - {"type", "data": {...}}), so this
    degrades gracefully rather than crashing on the first real delivery.
    ALWAYS log the raw payload at the call site (app/main.py's
    safepay_webhook()) regardless of whether extraction succeeds, so the
    first real delivery can be inspected and this function tightened to the
    real shape once it's known.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tracker = data.get("token") or data.get("tracker") or payload.get("tracker") or payload.get("token")
    state = data.get("state") or data.get("status") or payload.get("state") or payload.get("status")
    return tracker, state
