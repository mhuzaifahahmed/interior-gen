import io

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers.gemini import (
    GeminiProvider,
    ROOM_LAYOUT_PROMPT_TEMPLATE,
    _enforce_floor_count,
    _explicit_floor_count,
    fallback_room_layout,
    parse_room_layout,
)


def _sample_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_analyze_plot_returns_response_text(monkeypatch):
    class FakeResponse:
        text = "A rectangular plot facing north, with a low wall on one side."

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    provider = GeminiProvider()
    result = provider.analyze_plot(_sample_image_bytes(), {"length": 40, "width": 60, "unit": "ft"})
    assert result == "A rectangular plot facing north, with a low wall on one side."


def test_analyze_plot_degrades_to_none_on_failure(monkeypatch):
    class FakeModels:
        def generate_content(self, model, contents):
            raise RuntimeError("network error")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    provider = GeminiProvider()
    result = provider.analyze_plot(_sample_image_bytes(), {})
    assert result is None


def test_analyze_plot_includes_dimensions_in_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        text = "ok"

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    GeminiProvider().analyze_plot(_sample_image_bytes(), {"length": 40, "width": 60, "unit": "ft"})

    assert "40 x 60 ft" in captured["prompt"]


def test_generate_floor_plan_always_returns_none():
    # Gemini has no floor-plan capability - the real vendor slot is
    # app/providers/idealhouse.py, composed in by HybridProvider, not this class.
    provider = GeminiProvider()
    assert provider.generate_floor_plan("a plot", {}, "modern") is None


def test_generate_house_render_delegates_to_generate_image(monkeypatch):
    class FakePart:
        inline_data = type("Data", (), {"data": b"gemini-render-bytes"})()

    class FakeContent:
        parts = [FakePart()]

    class FakeCandidate:
        content = FakeContent()

    class FakeResponse:
        candidates = [FakeCandidate()]

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_house_render(_sample_image_bytes(), "a modern house render")
    assert result == b"gemini-render-bytes"


def test_generate_room_layout_returns_parsed_json(monkeypatch):
    class FakeResponse:
        text = (
            '{"floors": [{"floor_number": 1, "rooms": '
            '[{"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1}]}]}'
        )

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_room_layout({"length": 40, "width": 60, "unit": "ft"}, "modern style")
    assert result == {
        "floors": [
            {
                "floor_number": 1,
                "rooms": [{"name": "Living Room", "area": 2.0}, {"name": "Kitchen", "area": 1.0}],
            }
        ]
    }


def test_generate_room_layout_falls_back_on_unparseable_response(monkeypatch):
    class FakeResponse:
        text = "not json at all"

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_room_layout({"length": 40, "width": 60, "unit": "ft"}, "2 floors")
    assert len(result["floors"]) == 2
    assert all(floor["rooms"] for floor in result["floors"])


def test_generate_room_layout_falls_back_on_exception(monkeypatch):
    class FakeModels:
        def generate_content(self, model, contents):
            raise RuntimeError("network error")

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_room_layout({"length": 40, "width": 60, "unit": "ft"}, "")
    assert len(result["floors"]) == 1
    assert result["floors"][0]["rooms"]


def test_parse_room_layout_rejects_floor_with_no_rooms():
    assert parse_room_layout('{"floors": [{"floor_number": 1, "rooms": []}]}') is None


def test_parse_room_layout_rejects_malformed_json():
    assert parse_room_layout("not json") is None


def test_fallback_room_layout_defaults_to_one_floor():
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "modern style")
    assert len(result["floors"]) == 1
    assert result["floors"][0]["rooms"]


def test_fallback_room_layout_parses_floor_count_from_prompt():
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "3 floors, luxury style")
    assert len(result["floors"]) == 3
    assert [f["floor_number"] for f in result["floors"]] == [1, 2, 3]


def test_fallback_room_layout_ground_floor_has_no_bedrooms():
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "2 floors")
    ground_names = {room["name"] for room in result["floors"][0]["rooms"]}
    assert not any("bedroom" in name.lower() for name in ground_names)
    assert any(name.lower() in ("living room", "kitchen") for name in ground_names)


def test_fallback_room_layout_upper_floors_have_no_garage_or_kitchen():
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "3 floors")
    for floor in result["floors"][1:]:
        names = {room["name"].lower() for room in floor["rooms"]}
        assert "garage" not in names
        assert "kitchen" not in names
        assert any("bedroom" in name for name in names)


def test_fallback_room_layout_single_storey_has_both_living_and_bedroom_rooms():
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "a small house")
    names = {room["name"].lower() for room in result["floors"][0]["rooms"]}
    assert "living room" in names
    assert any("bedroom" in name for name in names)


def test_room_layout_prompt_names_the_floor_placement_rules():
    assert "GROUND FLOOR" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "garage" in ROOM_LAYOUT_PROMPT_TEMPLATE.lower()
    assert "NEVER place a garage or a kitchen" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "EXACTLY that many floors" in ROOM_LAYOUT_PROMPT_TEMPLATE


def test_explicit_floor_count_returns_none_when_unstated():
    assert _explicit_floor_count("a modern house") is None


def test_explicit_floor_count_parses_stated_number():
    assert _explicit_floor_count("I want 3 floors please") == 3


def test_enforce_floor_count_is_noop_when_not_explicit():
    parsed = {"floors": [{"floor_number": 1, "rooms": [{"name": "Room", "area": 1}]}]}
    result = _enforce_floor_count(parsed, None)
    assert len(result["floors"]) == 1


def test_enforce_floor_count_truncates_extra_floors():
    parsed = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 1", "area": 1}]},
            {"floor_number": 3, "rooms": [{"name": "Bedroom 2", "area": 1}]},
        ]
    }
    result = _enforce_floor_count(parsed, 2)
    assert len(result["floors"]) == 2
    assert [f["floor_number"] for f in result["floors"]] == [1, 2]


def test_enforce_floor_count_pads_missing_floors_with_upper_floor_rooms():
    parsed = {"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}]}
    result = _enforce_floor_count(parsed, 2)
    assert len(result["floors"]) == 2
    padded_names = {room["name"].lower() for room in result["floors"][1]["rooms"]}
    assert "garage" not in padded_names
    assert "kitchen" not in padded_names
    assert any("bedroom" in name for name in padded_names)


def test_generate_room_layout_enforces_explicit_floor_count(monkeypatch):
    # Gemini returns only 1 floor even though the user asked for 2 - the
    # deterministic backstop must pad it, not just trust the model's prompt
    # compliance.
    class FakeResponse:
        text = '{"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}]}'

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_room_layout({"length": 40, "width": 60, "unit": "ft"}, "2 floors, modern")
    assert len(result["floors"]) == 2
