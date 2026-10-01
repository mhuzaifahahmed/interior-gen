import io

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers.gemini import (
    GeminiProvider,
    ROOM_LAYOUT_PROMPT_TEMPLATE,
    _enforce_floor_count,
    _explicit_floor_count,
    _format_room_targets_block,
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


def test_generate_room_layout_explicit_floor_count_overrides_prompt_text(monkeypatch):
    # A real explicit value from the "Floors" dropdown must win even when the
    # free-text prompt says something different (or nothing at all) - see
    # app/main.py's create_house_project/_compose_house_requirements().
    class FakeResponse:
        text = '{"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}]}'

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    # Gemini's response only has 1 floor, and the prompt text mentions no
    # floor count at all - but floor_count=3 is passed explicitly.
    result = GeminiProvider().generate_room_layout(
        {"length": 40, "width": 60, "unit": "ft"}, "modern style", floor_count=3
    )
    assert len(result["floors"]) == 3


def test_generate_room_layout_falls_back_to_regex_when_floor_count_not_given(monkeypatch):
    class FakeResponse:
        text = '{"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}]}'

    class FakeModels:
        def generate_content(self, model, contents):
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    result = GeminiProvider().generate_room_layout(
        {"length": 40, "width": 60, "unit": "ft"}, "I want 2 floors", floor_count=None
    )
    assert len(result["floors"]) == 2


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


def test_fallback_room_layout_uses_explicit_per_floor_bedroom_bathroom_counts():
    # Real fix for a live-reproduced bug: a total Gemini failure previously
    # fell back to a FIXED 2-bedroom/1-bathroom upper-floor set regardless of
    # what the user actually asked for - this confirms the fallback now
    # honors real per-floor targets when given.
    result = fallback_room_layout(
        {"length": 40, "width": 60, "unit": "ft"},
        "2 floors",
        floor_count=2,
        floor_bedrooms=[3, 2],
        floor_bathrooms=[2, 1],
    )
    from app.pipeline.room_specs import classify_room_category

    for floor, expected_beds, expected_baths in zip(result["floors"], [3, 2], [2, 1]):
        beds = [r for r in floor["rooms"] if classify_room_category(r["name"]) == "bedroom"]
        baths = [r for r in floor["rooms"] if classify_room_category(r["name"]) == "bathroom"]
        assert len(beds) == expected_beds
        assert len(baths) == expected_baths


def test_fallback_room_layout_per_floor_counts_do_not_duplicate_ground_floor_bathroom():
    # _GROUND_FLOOR_ROOMS already includes a "Guest Bathroom" - when an
    # explicit floor_bathrooms count is given for floor 1, that generic
    # bathroom must be dropped first, not left alongside the real count
    # (which would silently double the real bathroom count).
    result = fallback_room_layout(
        {"length": 40, "width": 60, "unit": "ft"},
        "1 floor",
        floor_count=1,
        floor_bedrooms=[2],
        floor_bathrooms=[2],
    )
    from app.pipeline.room_specs import classify_room_category

    ground = result["floors"][0]["rooms"]
    baths = [r for r in ground if classify_room_category(r["name"]) == "bathroom"]
    assert len(baths) == 2
    assert "Guest Bathroom" not in {r["name"] for r in ground}


def test_fallback_room_layout_ignores_per_floor_counts_when_not_given():
    # No behavior change at all for the legacy, house-wide-total path.
    result = fallback_room_layout({"length": 40, "width": 60, "unit": "ft"}, "2 floors", floor_count=2)
    names = {room["name"].lower() for room in result["floors"][1]["rooms"]}
    assert "bedroom 1" in names and "bedroom 2" in names


def test_room_layout_prompt_names_the_floor_placement_rules():
    assert "GROUND FLOOR" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "garage" in ROOM_LAYOUT_PROMPT_TEMPLATE.lower()
    assert "NEVER place a garage" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "EXACTLY that many floors" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "{room_targets_block}" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "{extra_rooms_block}" in ROOM_LAYOUT_PROMPT_TEMPLATE


def test_room_layout_prompt_allows_an_explicit_user_override_for_kitchen_placement():
    # 2026-09-30: the OLD wording ("NEVER place a garage or a kitchen on any
    # floor above the ground floor") silently overrode even an explicit user
    # request - real, confirmed complaint. The garage rule stays absolute
    # (never user-overridable), but kitchen/dining/living must now say the
    # user's explicit placement wins.
    assert "NEVER place a garage on any floor above the ground floor" in ROOM_LAYOUT_PROMPT_TEMPLATE
    assert "user's explicit, stated placement always wins" in ROOM_LAYOUT_PROMPT_TEMPLATE


# ---- _format_room_targets_block() - real fix for a live-reproduced bug: a ----
# ---- floor came back with no bedrooms/bathrooms at all (just "Hallway")  ----


def test_format_room_targets_block_returns_empty_when_no_targets_given():
    assert _format_room_targets_block(None, None) == ""
    assert _format_room_targets_block([], []) == ""


def test_format_room_targets_block_states_exact_per_floor_counts():
    block = _format_room_targets_block([3, 2], [2, 1])
    assert "Floor 1 MUST contain exactly 3 bedrooms and exactly 2 bathrooms." in block
    assert "Floor 2 MUST contain exactly 2 bedrooms and exactly 1 bathroom." in block


def test_format_room_targets_block_handles_bedrooms_only():
    block = _format_room_targets_block([3], None)
    floor_line = next(line for line in block.splitlines() if line.startswith("- Floor 1"))
    assert floor_line == "- Floor 1 MUST contain exactly 3 bedrooms."
    assert "bathroom" not in floor_line.lower()


def test_generate_room_layout_includes_per_floor_targets_in_the_sent_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        text = '{"floors": [{"floor_number": 1, "rooms": [{"name": "Bedroom 1", "area": 1}]}]}'

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    GeminiProvider().generate_room_layout(
        {"length": 40, "width": 60, "unit": "ft"},
        "2 floors",
        floor_count=2,
        floor_bedrooms=[3, 2],
        floor_bathrooms=[2, 1],
    )
    assert "Floor 1 MUST contain exactly 3 bedrooms and exactly 2 bathrooms." in captured["prompt"]
    assert "Floor 2 MUST contain exactly 2 bedrooms and exactly 1 bathroom." in captured["prompt"]


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
