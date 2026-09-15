import httpx
import pytest

import app.providers.serpapi as serpapi_module
from app.providers.serpapi import search


@pytest.fixture(autouse=True)
def _reset_active_key_index():
    # _active_key_index is deliberately module-level global state (see
    # serpapi.py's docstring - it's meant to persist ACROSS calls within a
    # real process so a known-exhausted key isn't retried on every
    # subsequent item's search) - but that same persistence would leak
    # between tests if not reset, since pytest imports this module once and
    # reuses it for the whole session.
    serpapi_module._active_key_index = 0
    yield
    serpapi_module._active_key_index = 0


class FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_search_sends_query_and_api_key(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse({"search_metadata": {"status": "Success"}, "organic_results": []})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "test-key")

    search("ceramic tile price Karachi")

    assert captured["params"]["q"] == "ceramic tile price Karachi"
    assert captured["params"]["api_key"] == "test-key"
    assert captured["params"]["engine"] == "google"
    assert "location" not in captured["params"]  # not passed when omitted


def test_search_includes_location_when_given(monkeypatch):
    # Regression guard for a real bug: without geo-biasing, results skewed
    # toward global/US retailers instead of local sellers for the given city.
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["params"] = params
        return FakeResponse({"search_metadata": {"status": "Success"}, "organic_results": []})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)

    search("tile price", location="Karachi")

    assert captured["params"]["location"] == "Karachi"


def test_search_with_explicit_api_key_uses_it_directly_when_it_works(monkeypatch):
    # 2026-09: gemini.py passes each tier's own dedicated SerpApi key here -
    # the normal case (that key has quota left) must use EXACTLY that key,
    # a single call, never touching the shared settings.serpapi_api_keys pool
    # at all - that's the whole point of dedicating a key per tier.
    captured = {"calls": []}

    def fake_get(url, params=None, timeout=None):
        captured["calls"].append(params["api_key"])
        return FakeResponse({"search_metadata": {"status": "Success"}, "organic_results": []})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    # A different key is configured in the shared pool - must never be tried.
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "pool-key")

    search("tile price", api_key="tier-dedicated-key")

    assert captured["calls"] == ["tier-dedicated-key"]


def test_search_with_explicit_api_key_falls_back_to_another_key_on_quota_exhaustion(monkeypatch):
    # Real gap this closes: a tier's own dedicated key can run out of its
    # 250/month quota mid-generation. Without a fallback, that tier would get
    # ZERO real search coverage (every item degrading to a Gemini guess) for
    # the rest of the month. A quota-exhausted dedicated key must now borrow
    # another configured key rather than immediately giving up.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["api_key"])
        if params["api_key"] == "exhausted-dedicated-key":
            return FakeResponse({"search_metadata": {"status": "Error"}, "error": "You have run out of searches"})
        return FakeResponse(
            {
                "search_metadata": {"status": "Success"},
                "organic_results": [{"title": "Tile", "link": "https://example.com/tile", "snippet": "..."}],
            }
        )

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "exhausted-dedicated-key")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "backup-key")

    results = search("tile price", api_key="exhausted-dedicated-key")

    assert calls == ["exhausted-dedicated-key", "backup-key"]
    assert results[0]["title"] == "Tile"


def test_search_with_explicit_api_key_raises_when_every_key_is_exhausted(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "You have run out of searches"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "key-2")

    with pytest.raises(serpapi_module._QuotaExhaustedError):
        search("tile price", api_key="key-1")


def test_search_with_explicit_api_key_does_not_fall_back_on_a_non_quota_error(monkeypatch):
    # A bad key entirely / network error / malformed query isn't fixed by
    # trying a different key - must raise immediately, not burn 3 more doomed
    # requests against every other configured key.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["api_key"])
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "Invalid API key"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "bad-key")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "other-key")

    with pytest.raises(RuntimeError):
        search("tile price", api_key="bad-key")

    assert calls == ["bad-key"]  # never touched the other configured key


def test_search_parses_organic_results(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse(
            {
                "search_metadata": {"status": "Success"},
                "organic_results": [
                    {"title": "Tile Shop", "link": "https://example.com/tiles", "snippet": "Rs 300/sqft"},
                    {"title": "No Link", "snippet": "missing link, should be skipped"},
                ],
            }
        )

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)

    results = search("tile price")
    assert len(results) == 1
    assert results[0]["title"] == "Tile Shop"
    assert results[0]["link"] == "https://example.com/tiles"
    assert results[0]["snippet"] == "Rs 300/sqft"


def test_search_raises_on_api_error_status(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "Invalid API key"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)

    try:
        search("tile price")
    except RuntimeError as exc:
        assert "Invalid API key" in str(exc)
        return
    assert False, "expected RuntimeError"


def test_search_raises_on_http_error(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({}, status_code=403)

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)

    try:
        search("tile price")
    except httpx.HTTPStatusError:
        return
    assert False, "expected HTTPStatusError"


def test_search_caps_results_at_num_results(monkeypatch):
    many_results = [
        {"title": f"Result {i}", "link": f"https://example.com/{i}", "snippet": "..."} for i in range(20)
    ]

    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"search_metadata": {"status": "Success"}, "organic_results": many_results})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)

    results = search("tile price")
    assert len(results) == serpapi_module.NUM_RESULTS


# ---- automatic fallback to a second SerpApi key when the first runs out of ----
# ---- credits mid-generation ----


def test_search_switches_to_second_key_when_first_is_out_of_credits(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["api_key"])
        if params["api_key"] == "key-1":
            return FakeResponse(
                {"search_metadata": {"status": "Error"}, "error": "You have run out of searches for this month."}
            )
        return FakeResponse(
            {
                "search_metadata": {"status": "Success"},
                "organic_results": [{"title": "Tile", "link": "https://example.com/tile", "snippet": "..."}],
            }
        )

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "key-2")

    results = search("tile price")

    assert calls == ["key-1", "key-2"]
    assert results[0]["title"] == "Tile"


def test_search_remembers_the_switch_for_subsequent_calls(monkeypatch):
    # Real reason this matters: up to 18 per-item searches happen per
    # generation - once key-1 is known exhausted, every later item's search
    # should go straight to key-2, not waste a request re-discovering the
    # same exhaustion on key-1 every single time.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["api_key"])
        if params["api_key"] == "key-1":
            return FakeResponse({"search_metadata": {"status": "Error"}, "error": "quota exceeded"})
        return FakeResponse({"search_metadata": {"status": "Success"}, "organic_results": []})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "key-2")

    search("item one")
    search("item two")
    search("item three")

    # key-1 only ever appears once (the very first call that discovered the
    # exhaustion) - every call after that goes straight to key-2.
    assert calls == ["key-1", "key-2", "key-2", "key-2"]


def test_search_does_not_switch_keys_for_a_non_quota_error(monkeypatch):
    # A bad query / genuine auth failure / network error should NOT trigger
    # a key switch - only quota exhaustion specifically is treated as
    # "try the other key," same discipline as the Gemini 429-only retry fix.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["api_key"])
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "Invalid API key"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "key-2")

    with pytest.raises(RuntimeError):
        search("tile price")

    assert calls == ["key-1"]  # never tried key-2


def test_search_raises_when_all_keys_are_exhausted(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "run out of searches"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "key-2")

    with pytest.raises(Exception):
        search("tile price")


def test_search_with_no_second_key_behaves_exactly_as_before(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"search_metadata": {"status": "Error"}, "error": "run out of searches"})

    monkeypatch.setattr(serpapi_module.httpx, "get", fake_get)
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key", "key-1")
    monkeypatch.setattr(serpapi_module.settings, "serpapi_api_key_2", "")

    with pytest.raises(Exception):
        search("tile price")
