import logging
import threading

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://serpapi.com/search"

# Each call is billed as 1 search regardless of how many results come back
# (SerpApi's 250/month free tier is metered per-call, not per-result), so
# asking for more results per search is a free improvement to real-item
# coverage now that each search targets one specific item (see gemini.py's
# generate_materials) - more headroom for that item's real price to actually
# appear somewhere in the returned results.
NUM_RESULTS = 12


class _QuotaExhaustedError(Exception):
    """Internal signal that THIS key specifically is out of credits (as
    opposed to a generic network/bad-request failure) - caught only inside
    search() to advance to the next configured key. Never escapes this module.
    """


# Which configured key (settings.serpapi_api_keys index) to try FIRST. Starts
# at 0 and only ever moves forward, never back within a process lifetime -
# once a key is found exhausted there's no reason to keep re-trying it on
# every subsequent item's search (this feature fires per-item, up to 18
# times per generation - retrying a known-dead key each time would just
# waste a request and add latency for no benefit). Resets to 0 on restart,
# which is fine - a monthly quota renews well before then in practice.
_active_key_index = 0
_active_key_lock = threading.Lock()


def search(query: str, location: str | None = None) -> list[dict]:
    """Run one real Google search via SerpApi and return organic results as
    [{"title", "link", "snippet"}]. Raises on any failure (network, bad key,
    quota exhausted on every configured key) - callers must catch this and
    fall back, same contract as every other best-effort external call in
    this codebase.

    If settings.serpapi_api_key_2 is configured and the currently-active key
    turns out to be out of credits, automatically retries this SAME search
    against the next configured key before giving up - see
    _QuotaExhaustedError/_active_key_index above for how that's detected and
    remembered across calls. With no second key configured, behavior is
    identical to before this existed (single key, no retry-on-quota).

    location biases results toward that geographic area (SerpApi's `location`
    param, same field shown in their playground UI) - without it, results skew
    toward whatever global/US retailers rank highest for the query text alone,
    which is what caused a real observed bug: prices for a Karachi search came
    back in USD from Home Depot/Alibaba instead of local PKR listings.
    """
    global _active_key_index

    keys = settings.serpapi_api_keys
    if not keys:
        raise RuntimeError("no SerpApi key configured (SERPAPI_API_KEY is blank)")

    start = min(_active_key_index, len(keys) - 1)
    last_exc: Exception | None = None
    for offset in range(len(keys) - start):
        idx = start + offset
        try:
            return _search_with_key(query, keys[idx], location)
        except _QuotaExhaustedError as exc:
            last_exc = exc
            logger.warning(
                "SerpApi key #%d ran out of credits, switching to key #%d", idx + 1, idx + 2
            )
            with _active_key_lock:
                _active_key_index = max(_active_key_index, idx + 1)

    raise last_exc


def _is_quota_exhausted(status_code: int, error_text: str) -> bool:
    # SerpApi returns 401 for an exhausted-quota key (same status as a bad
    # key entirely - text is what actually distinguishes them) and has also
    # been observed returning 429. Text check covers both, plus a 200 response
    # whose JSON body itself reports the account is out of searches.
    if status_code == 429:
        return True
    lowered = (error_text or "").lower()
    return any(
        phrase in lowered
        for phrase in ("run out of searches", "out of searches", "quota", "exceeded your", "no more searches")
    )


def _search_with_key(query: str, api_key: str, location: str | None) -> list[dict]:
    params = {"engine": "google", "q": query, "api_key": api_key, "num": NUM_RESULTS}
    if location:
        params["location"] = location

    # Bumped from 20s after a real observed ReadTimeout on one item's search
    # during live testing - that one item's search failing is non-fatal (see
    # generate_materials's per-item try/except) but still worth padding to
    # cut down on losing real coverage to a slow-but-would-have-succeeded call.
    response = httpx.get(SEARCH_URL, params=params, timeout=30)

    if response.status_code in (401, 429):
        body_text = response.text
        if _is_quota_exhausted(response.status_code, body_text):
            raise _QuotaExhaustedError(body_text)
        response.raise_for_status()  # a genuine auth/rate error, not quota - don't retry another key

    response.raise_for_status()
    data = response.json()

    error = data.get("search_metadata", {}).get("status")
    if error and error != "Success":
        error_message = str(data.get("error", error))
        if _is_quota_exhausted(response.status_code, error_message):
            raise _QuotaExhaustedError(error_message)
        raise RuntimeError(f"SerpApi search failed: {error_message}")

    results = []
    for item in data.get("organic_results", [])[:NUM_RESULTS]:
        if not item.get("link"):
            continue
        results.append(
            {
                "title": item.get("title", ""),
                "link": item["link"],
                "snippet": item.get("snippet", ""),
            }
        )
    return results
