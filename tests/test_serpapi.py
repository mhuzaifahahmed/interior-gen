import httpx

import app.providers.serpapi as serpapi_module
from app.providers.serpapi import search


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
