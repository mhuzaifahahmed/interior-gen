import io
import json

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers import analysis_cache
from app.providers.gemini import UNWORKABLE_IMAGE_MARKER, GeminiProvider, _parse_describe_room_response


def _sample_image_bytes(seed: int = 0) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(seed % 256, 100, 150)).save(buf, format="PNG")
    return buf.getvalue()


def _fake_client_returning(text: str):
    class FakeResponse:
        pass

    response = FakeResponse()
    response.text = text

    class FakeModels:
        def generate_content(self, model, contents):
            return response

    class FakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.models = FakeModels()

    return FakeClient


# ---- _parse_describe_room_response() - the pure parsing logic ----


def test_parse_describe_room_response_returns_marker_when_workable_is_false():
    raw = json.dumps({"workable": False, "description": ""})
    assert _parse_describe_room_response(raw) == UNWORKABLE_IMAGE_MARKER


def test_parse_describe_room_response_returns_description_when_workable_is_true():
    raw = json.dumps({"workable": True, "description": "A bedroom with one window."})
    assert _parse_describe_room_response(raw) == "A bedroom with one window."


def test_parse_describe_room_response_strips_markdown_fences():
    raw = '```json\n{"workable": true, "description": "A kitchen."}\n```'
    assert _parse_describe_room_response(raw) == "A kitchen."


def test_parse_describe_room_response_falls_back_to_raw_text_on_invalid_json():
    # Fail-open: unparseable output must never be mistaken for a confident
    # "unworkable" classification (a hard gate) - it degrades to being
    # treated as a plain description instead, same as describe_room's
    # pre-JSON behavior.
    raw = "just a plain sentence, not JSON at all"
    assert _parse_describe_room_response(raw) == raw
    assert _parse_describe_room_response(raw) != UNWORKABLE_IMAGE_MARKER


def test_parse_describe_room_response_falls_back_when_workable_is_missing():
    raw = json.dumps({"description": "A hallway."})
    assert _parse_describe_room_response(raw) == "A hallway."


def test_parse_describe_room_response_falls_back_to_raw_text_when_shape_is_unexpected():
    raw = json.dumps({"workable": True, "description": ""})
    # No usable description and workable isn't explicitly False - falls back
    # to the raw text rather than returning an empty string.
    assert _parse_describe_room_response(raw) == raw


def test_parse_describe_room_response_non_dict_json_falls_back_to_raw_text():
    raw = "[1, 2, 3]"
    assert _parse_describe_room_response(raw) == raw


# ---- GeminiProvider.describe_room() - the real call site ----


def test_describe_room_prompt_asks_for_structured_workable_json(monkeypatch):
    # Not asserting exact prompt wording (that's brittle) - just that the
    # prompt asks for the structured "workable" JSON field this parser
    # depends on, and requests JSON explicitly (this project's established
    # pattern for every other classification/extraction Gemini call).
    captured = {}

    class FakeResponse:
        text = '{"workable": true, "description": "A rectangular room."}'

    class FakeModels:
        def generate_content(self, model, contents):
            captured["prompt"] = contents[0]
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    provider = GeminiProvider()
    provider.describe_room(_sample_image_bytes(0))

    assert "workable" in captured["prompt"]
    assert "JSON" in captured["prompt"]


def test_describe_room_returns_marker_when_gemini_flags_it_unworkable(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai,
        "Client",
        _fake_client_returning('{"workable": false, "description": ""}'),
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(1))
    assert result == UNWORKABLE_IMAGE_MARKER


def test_describe_room_returns_real_description_for_a_workable_photo(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai,
        "Client",
        _fake_client_returning('{"workable": true, "description": "A bedroom with one window on the left wall."}'),
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(2))
    assert result != UNWORKABLE_IMAGE_MARKER
    assert "bedroom" in result


def test_describe_room_still_fails_open_when_gemini_ignores_the_json_format(monkeypatch):
    # Real, live-observed failure this whole structured-format change fixes:
    # an earlier free-text "respond with EXACTLY <marker>" design let Gemini
    # answer in a normal-sounding sentence instead of the literal marker for
    # a real non-room upload, silently letting it through. With a plain-text
    # (non-JSON) response now, the parser must still fail open rather than
    # ever synthesizing UNWORKABLE_IMAGE_MARKER on its own.
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai,
        "Client",
        _fake_client_returning("A red and yellow circular design."),
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(3))
    assert result != UNWORKABLE_IMAGE_MARKER


def test_describe_room_caches_the_marker_result_per_image(monkeypatch):
    analysis_cache.clear()
    call_count = 0

    class FakeResponse:
        text = '{"workable": false, "description": ""}'

    class FakeModels:
        def generate_content(self, model, contents):
            nonlocal call_count
            call_count += 1
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)

    provider = GeminiProvider()
    image_bytes = _sample_image_bytes(4)
    assert provider.describe_room(image_bytes) == UNWORKABLE_IMAGE_MARKER
    assert provider.describe_room(image_bytes) == UNWORKABLE_IMAGE_MARKER
    assert call_count == 1
