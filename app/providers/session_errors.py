"""Shared classification for "the Kaggle notebook session is offline" style
failures, used by every Kaggle-backed provider (app/providers/kaggle.py,
app/providers/kaggle_autocad.py).

Real motivation (2026-09-01): a stuck "Build a House" generation turned out
to be caused by the friend's Kaggle Concept Layout notebook session having
stopped - the Cloudflare tunnel simply had nothing listening behind it. The
existing code had no way to tell that apart from any other unexpected error
(both just logged a traceback and silently degraded), so the user had no way
to know WHY a generation was stuck/incomplete without reading server logs
directly. This module gives every Kaggle call site a single, shared way to
detect that specific shape of failure and raise a message a human can
actually act on ("start/restart the Kaggle notebook"), instead of a raw
httpx traceback or silent degradation.

Deliberately narrow: only classifies failures that really do look like "no
one is listening" (a connection-level failure, or an HTTP status Cloudflare
itself returns when the tunnel is up but the origin behind it isn't) - a
genuine bug inside a running notebook (a 500 from the FastAPI app itself, a
malformed response body) is NOT reclassified as a session-availability
problem, since that would hide a different, real bug behind a misleading
"session is offline" message.
"""

import httpx

# Cloudflare's own tunnel infrastructure returns these when it can't reach
# whatever is supposed to be listening behind the tunnel (session stopped,
# notebook restarted without ever bringing the tunnel back, etc.) - NOT
# codes the notebook's own FastAPI app would return itself.
_TUNNEL_DOWN_STATUS_CODES = {502, 503, 521, 522, 523, 524, 525, 526, 530}


class KaggleSessionUnavailableError(RuntimeError):
    """Raised in place of a raw connection/timeout error when a Kaggle-backed
    call fails in a way that specifically looks like "the notebook session
    isn't running" - carries a message meant to be shown directly to the
    end user (see callers), not just logged."""


def classify_kaggle_failure(exc: Exception, vendor_label: str) -> KaggleSessionUnavailableError | None:
    """Returns a KaggleSessionUnavailableError to raise instead of `exc` if
    `exc` looks like the Kaggle session is offline, else None (caller should
    keep treating `exc` as an ordinary, unclassified error)."""
    if isinstance(exc, httpx.TransportError):
        # Covers ConnectError/ConnectTimeout/ReadTimeout/WriteTimeout/
        # PoolTimeout/NetworkError - httpx couldn't even complete the
        # request, the single strongest "nothing is listening" signal.
        return KaggleSessionUnavailableError(
            f"The {vendor_label} Kaggle session appears to be offline (couldn't connect at all). "
            "Start/restart the Kaggle notebook, update the tunnel URL if it changed, and try again."
        )
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in _TUNNEL_DOWN_STATUS_CODES:
        return KaggleSessionUnavailableError(
            f"The {vendor_label} Kaggle session appears to be offline (tunnel returned "
            f"{exc.response.status_code}). Start/restart the Kaggle notebook, update the tunnel URL if it "
            "changed, and try again."
        )
    return None
