import io

from google.genai import errors as genai_errors
from PIL import Image

import app.providers.gemini as gemini_module
from app.providers.gemini import GeminiProvider, fallback_materials, parse_materials


def _sample_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_parse_materials_valid_json():
    raw = (
        '{"items": [{"name": "Flooring", "spec": "laminate", "price": "$500", '
        '"currency": "USD", "source_url": "https://example.com/floor", "is_estimate": false}], '
        '"total": "$500", "currency": "USD"}'
    )
    result = parse_materials(raw)
    assert result["items"][0]["name"] == "Flooring"
    assert result["items"][0]["price"] == "$500"
    assert result["items"][0]["is_estimate"] is False
    assert result["total"] == "$500"


def test_parse_materials_strips_markdown_fences():
    raw = '```json\n{"items": [{"name": "Paint", "price": "$50"}], "total": "$50"}\n```'
    result = parse_materials(raw)
    assert result["items"][0]["name"] == "Paint"


def test_parse_materials_fills_missing_price_as_estimate():
    # Regression guard for the "never leave a price blank" requirement - even a
    # successfully-parsed item that's missing its price must not render empty.
    raw = '{"items": [{"name": "Lighting"}], "total": "$0"}'
    result = parse_materials(raw)
    item = result["items"][0]
    assert item["price"] == "Estimate unavailable"
    assert item["is_estimate"] is True


def test_parse_materials_invalid_json_returns_none():
    assert parse_materials("not json at all") is None


def test_parse_materials_missing_items_key_returns_none():
    assert parse_materials('{"total": "$100"}') is None


def test_parse_materials_empty_items_returns_none():
    # An empty items list must be treated as a parse failure (triggering the
    # caller's fallback_materials()), not a valid-but-empty result.
    assert parse_materials('{"items": [], "total": "$0"}') is None


def test_parse_materials_skips_items_without_a_name():
    raw = '{"items": [{"spec": "no name here"}, {"name": "Valid", "price": "$10"}], "total": "$10"}'
    result = parse_materials(raw)
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "Valid"


def test_parse_materials_defaults_missing_total():
    raw = '{"items": [{"name": "Paint", "price": "$50"}]}'
    result = parse_materials(raw)
    assert result["total"]


def test_fallback_materials_never_empty_and_has_search_links():
    tier_spec = {
        "paint": "sage green paint",
        "flooring": "grey tile",
        "lighting_temp": "cool white",
        "ceiling": "flat white",
        "feature_wall": "",
        "decor": "potted plants",
    }
    result = fallback_materials(tier_spec, "Karachi")
    assert len(result["items"]) > 0
    for item in result["items"]:
        assert item["price"]  # never blank, even in the hard-failure fallback
        assert item["is_estimate"] is True
        assert item["source_url"].startswith("https://www.google.com/search?q=")
        assert "Karachi" in item["source_url"]
    assert result["total"]


def test_fallback_materials_skips_empty_spec_fields():
    tier_spec = {k: "" for k in ("paint", "flooring", "lighting_temp", "ceiling", "feature_wall", "decor")}
    result = fallback_materials(tier_spec, "Lahore")
    assert result["items"] == []
    assert result["total"]  # still never blank even with zero items


def test_tier_line_items_returns_fixed_deterministic_list():
    tier_spec = {
        "flooring": "marble",
        "paint": "cream",
        "lighting_temp": "warm",
        "ceiling": "cove ceiling",
        "feature_wall": "brass inlay",
        "decor": "rugs",
    }
    items = gemini_module._tier_line_items(tier_spec)
    assert items == [
        ("Flooring", "marble"),
        ("Paint / wall finish", "cream"),
        ("Lighting", "warm"),
        ("Ceiling treatment", "cove ceiling"),
        ("Feature wall", "brass inlay"),
        ("Decor", "rugs"),
    ]


def test_tier_line_items_skips_empty_fields():
    tier_spec = {"flooring": "marble", "paint": "", "lighting_temp": "warm"}
    items = gemini_module._tier_line_items(tier_spec)
    assert items == [("Flooring", "marble"), ("Lighting", "warm")]


def test_format_search_results_empty():
    assert gemini_module._format_search_results([]) == "(no search results available for this item)"


def test_format_search_results_includes_title_snippet_link():
    results = [{"title": "Tile Shop", "snippet": "Rs 300/sqft", "link": "https://example.com/tiles"}]
    formatted = gemini_module._format_search_results(results)
    assert "Tile Shop" in formatted
    assert "Rs 300/sqft" in formatted
    assert "https://example.com/tiles" in formatted


def test_format_line_items_with_results_labels_each_item():
    line_items = [("Flooring", "marble"), ("Paint / wall finish", "cream")]
    item_results = {
        "Flooring": [{"title": "Marble Co", "snippet": "PKR 1,500/sqft", "link": "https://example.com/marble"}],
        "Paint / wall finish": [],
    }
    formatted = gemini_module._format_line_items_with_results(line_items, item_results)
    assert "ITEM: Flooring (marble)" in formatted
    assert "Marble Co" in formatted
    assert "ITEM: Paint / wall finish (cream)" in formatted
    assert "no search results available for this item" in formatted


def test_sanitize_source_urls_keeps_real_link():
    materials = {"items": [{"source_url": "https://real.com/a", "is_estimate": False}]}
    search_results = [{"link": "https://real.com/a"}]
    result = gemini_module._sanitize_source_urls(materials, search_results)
    assert result["items"][0]["source_url"] == "https://real.com/a"
    assert result["items"][0]["is_estimate"] is False


def test_sanitize_source_urls_strips_hallucinated_link():
    # Defense in depth: even if the model ignores the "never invent a URL"
    # instruction, a link that wasn't actually in the real search results
    # must never be presented to the user as a real source.
    materials = {"items": [{"source_url": "https://made-up.com/product", "is_estimate": False}]}
    search_results = [{"link": "https://real.com/a"}]
    result = gemini_module._sanitize_source_urls(materials, search_results)
    assert result["items"][0]["source_url"] is None
    assert result["items"][0]["is_estimate"] is True


def test_generate_materials_uses_search_results_when_client_succeeds(monkeypatch):
    captured = {}

    class FakeResponse:
        text = (
            '{"items": [{"name": "Paint", "price": "$40", "is_estimate": false, '
            '"source_url": "https://real.com/paint"}], "total": "$40"}'
        )

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(
        gemini_module.serpapi,
        "search",
        lambda query, location=None: [
            {"title": "Paint Shop", "snippet": "$40/gal", "link": "https://real.com/paint"}
        ],
    )

    provider = GeminiProvider()
    result = provider.generate_materials(
        "premium", {"label": "premium luxury renovation", "paint": "marble"}, None, "Karachi"
    )
    assert result["items"][0]["name"] == "Paint"
    assert result["items"][0]["is_estimate"] is False
    assert result["items"][0]["source_url"] == "https://real.com/paint"
    assert "Paint Shop" in captured["prompt"]  # real search results reached the prompt


def test_generate_materials_states_area_in_prompt_when_provided(monkeypatch):
    # Real bug guard: a found per-sqft price used to pass straight through as
    # the item's "total" with no multiplication - the fix threads the room's
    # estimated area into the prompt so Gemini can actually do that math.
    captured = {}

    class FakeResponse:
        text = '{"items": [{"name": "Flooring", "price": "$1980"}], "total": "$1980"}'

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])

    provider = GeminiProvider()
    provider.generate_materials(
        "economical", {"label": "x", "flooring": "tile"}, None, "Karachi", room_area_sqft=180
    )
    assert "180 square feet" in captured["prompt"]
    assert "multiply" in captured["prompt"].lower()


def test_generate_materials_asks_for_an_assumption_when_area_unknown(monkeypatch):
    captured = {}

    class FakeResponse:
        text = '{"items": [{"name": "Flooring", "price": "$1"}], "total": "$1"}'

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])

    provider = GeminiProvider()
    provider.generate_materials("economical", {"label": "x", "flooring": "tile"}, None, "Karachi")
    assert "No floor-area estimate is available" in captured["prompt"]


def test_estimate_room_area_parses_numeric_response(monkeypatch):
    class FakeResponse:
        text = "180"

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    provider = GeminiProvider()
    assert provider.estimate_room_area(_sample_image_bytes()) == 180.0


def test_estimate_room_area_returns_none_on_unparseable_response(monkeypatch):
    class FakeResponse:
        text = "I can't tell from this photo."

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    provider = GeminiProvider()
    assert provider.estimate_room_area(_sample_image_bytes()) is None


def test_estimate_room_area_returns_none_on_exception(monkeypatch):
    class FakeModels:
        def generate_content(self, model, contents):
            raise RuntimeError("network error")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    provider = GeminiProvider()
    assert provider.estimate_room_area(_sample_image_bytes()) is None


def test_generate_materials_searches_once_per_item_not_once_per_tier(monkeypatch):
    # Regression guard for the real coverage problem this replaced: a single
    # combined search per tier only ever matched one item (usually flooring),
    # leaving the rest as pure guesses. Each non-empty tier_spec field must get
    # its own dedicated search.
    search_calls = []

    class FakeResponse:
        text = '{"items": [{"name": "Flooring", "price": "$1"}], "total": "$1"}'

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    def fake_search(query, location=None):
        search_calls.append(query)
        return []

    monkeypatch.setattr(gemini_module.serpapi, "search", fake_search)

    tier_spec = {
        "label": "x",
        "paint": "cream paint",
        "flooring": "tile",
        "lighting_temp": "cool",
        "ceiling": "flat",
        "feature_wall": "",  # blank - economical has no feature wall
        "decor": "plants",
    }
    provider = GeminiProvider()
    provider.generate_materials("economical", tier_spec, None, "Karachi")

    # 5 non-empty fields (feature_wall is blank) -> 5 dedicated searches.
    assert len(search_calls) == 5


def test_generate_materials_passes_city_as_search_location(monkeypatch):
    # Regression guard for a real bug: without geo-biasing the search, results
    # skewed toward global/US retailers (Home Depot, Alibaba) instead of local
    # sellers, so prices came back in USD instead of the local currency.
    captured = {}

    class FakeResponse:
        text = '{"items": [{"name": "Paint", "price": "PKR 4000"}], "total": "PKR 4000"}'

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    def fake_search(query, location=None):
        captured["location"] = location
        return []

    monkeypatch.setattr(gemini_module.serpapi, "search", fake_search)

    provider = GeminiProvider()
    provider.generate_materials("premium", {"label": "x", "paint": "marble"}, None, "Karachi")

    assert captured["location"] == "Karachi"


def test_generate_materials_still_works_when_search_itself_fails(monkeypatch):
    # A failed search isn't fatal - Gemini still runs without real snippets, it
    # just can't cite anything (matches the prompt: nothing to cite -> estimate).
    class FakeResponse:
        text = '{"items": [{"name": "Paint", "price": "$40", "is_estimate": true}], "total": "$40"}'

    class FakeModels:
        def generate_content(self, model, contents):
            assert "no search results available" in contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    def failing_search(query, location=None):
        raise RuntimeError("SerpApi quota exceeded")

    monkeypatch.setattr(gemini_module.serpapi, "search", failing_search)

    provider = GeminiProvider()
    result = provider.generate_materials("premium", {"label": "x", "paint": "marble"}, None, "Karachi")
    assert result["items"][0]["name"] == "Paint"


def test_generate_materials_falls_back_when_client_raises(monkeypatch):
    class FakeModels:
        def generate_content(self, model, contents):
            raise RuntimeError("network error")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])

    provider = GeminiProvider()
    tier_spec = {
        "label": "budget renovation",
        "paint": "cream paint",
        "flooring": "tile",
        "lighting_temp": "cool",
        "ceiling": "flat",
        "feature_wall": "",
        "decor": "plants",
    }
    result = provider.generate_materials("economical", tier_spec, None, "Lahore")
    assert len(result["items"]) > 0
    assert all(item["is_estimate"] for item in result["items"])


def test_generate_materials_uses_distinct_client_per_api_key(monkeypatch):
    created_keys = []

    class FakeModels:
        def generate_content(self, model, contents):
            class R:
                text = '{"items": [{"name": "X", "price": "$1"}], "total": "$1"}'

            return R()

    class FakeClient:
        def __init__(self, api_key):
            created_keys.append(api_key)
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.settings, "gemini_api_key", "default-key")
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])

    provider = GeminiProvider()
    provider.generate_materials("economical", {"label": "x"}, None, "Karachi", api_key="default-key")
    provider.generate_materials("mid", {"label": "x"}, None, "Karachi", api_key="key-2")
    provider.generate_materials("premium", {"label": "x"}, None, "Karachi", api_key="key-2")

    # default key creates exactly one client (via self.client), reused; key-2
    # also creates exactly one client (cached in _clients_by_key), reused too.
    assert created_keys.count("default-key") == 1
    assert created_keys.count("key-2") == 1


def test_generate_materials_retries_on_transient_server_error(monkeypatch):
    # Regression guard for a real, live-observed failure: gemini-3.5-flash
    # returned a transient 503 ("currently experiencing high demand") after 6
    # real, already-successful SerpApi searches, and with no retry that threw
    # away all 6 real results and collapsed straight to the generic fallback.
    call_count = {"n": 0}

    class FakeResponse:
        text = '{"items": [{"name": "Flooring", "price": "PKR 1000", "is_estimate": false}], "total": "PKR 1000"}'

    class FakeModels:
        def generate_content(self, model, contents):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise genai_errors.ServerError(503, {"error": {"message": "high demand"}})
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])
    monkeypatch.setattr(gemini_module.time, "sleep", lambda seconds: None)

    provider = GeminiProvider()
    result = provider.generate_materials("economical", {"label": "x", "flooring": "tile"}, None, "Karachi")

    assert call_count["n"] == 2
    assert result["items"][0]["name"] == "Flooring"
    assert result["items"][0]["is_estimate"] is False


def test_generate_materials_falls_back_after_exhausting_retries(monkeypatch):
    class FakeModels:
        def generate_content(self, model, contents):
            raise genai_errors.ServerError(503, {"error": {"message": "high demand"}})

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.serpapi, "search", lambda query, location=None: [])
    monkeypatch.setattr(gemini_module.time, "sleep", lambda seconds: None)

    provider = GeminiProvider()
    tier_spec = {"label": "x", "flooring": "tile", "paint": "cream"}
    result = provider.generate_materials("economical", tier_spec, None, "Karachi")

    # Never-empty fallback still kicks in once retries are genuinely exhausted.
    assert len(result["items"]) > 0
    assert all(item["is_estimate"] for item in result["items"])
