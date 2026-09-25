import io
import json

from google.genai import errors as genai_errors
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


# ---- defense-in-depth: an explicit False sub-signal overrides a possibly- ----
# ---- wrong "workable: true" verdict (the real astronaut-illustration bug) ----


def test_parse_describe_room_response_rejects_when_not_a_real_photograph_even_if_workable_true():
    # Real, live-observed bug this guards: Gemini's own "workable" verdict
    # alone let a stylized illustration through as true. A self-contradictory
    # response (workable=true but is_real_photograph=false) must be treated
    # as unworkable, not as noise to ignore.
    raw = json.dumps(
        {
            "is_real_photograph": False,
            "shows_room_interior": True,
            "structure_visible": True,
            "workable": True,
            "description": "A stylized illustration of a figure on a rock.",
        }
    )
    assert _parse_describe_room_response(raw) == UNWORKABLE_IMAGE_MARKER


def test_parse_describe_room_response_rejects_when_no_room_interior_shown():
    raw = json.dumps(
        {"is_real_photograph": True, "shows_room_interior": False, "structure_visible": True, "workable": True}
    )
    assert _parse_describe_room_response(raw) == UNWORKABLE_IMAGE_MARKER


def test_parse_describe_room_response_rejects_when_no_structure_visible():
    raw = json.dumps(
        {"is_real_photograph": True, "shows_room_interior": True, "structure_visible": False, "workable": True}
    )
    assert _parse_describe_room_response(raw) == UNWORKABLE_IMAGE_MARKER


def test_parse_describe_room_response_accepts_when_all_sub_signals_are_true():
    raw = json.dumps(
        {
            "is_real_photograph": True,
            "shows_room_interior": True,
            "structure_visible": True,
            "workable": True,
            "description": "A bedroom with one window.",
        }
    )
    assert _parse_describe_room_response(raw) == "A bedroom with one window."


def test_parse_describe_room_response_missing_sub_signals_does_not_block():
    # An older/simpler response shape with no sub-signal fields at all must
    # stay fail-open, not newly stricter by accident.
    raw = json.dumps({"workable": True, "description": "A kitchen."})
    assert _parse_describe_room_response(raw) == "A kitchen."


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
    # The 3 defense-in-depth sub-questions must actually be asked, not just
    # accepted if present - see _UNWORKABLE_SUB_SIGNALS.
    assert "is_real_photograph" in captured["prompt"]
    assert "shows_room_interior" in captured["prompt"]
    assert "structure_visible" in captured["prompt"]


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


def test_describe_room_rejects_a_stylized_illustration_flagged_via_sub_signals(monkeypatch):
    # Real regression case: a stylized astronaut-on-a-rock illustration was
    # generated into fake "room" tiers because Gemini's single "workable"
    # verdict alone said true. Simulates the corrected behavior once Gemini
    # honestly flags is_real_photograph=false for a non-photographic image.
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai,
        "Client",
        _fake_client_returning(
            json.dumps(
                {
                    "is_real_photograph": False,
                    "shows_room_interior": False,
                    "structure_visible": False,
                    "workable": False,
                    "description": "",
                }
            )
        ),
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(5))
    assert result == UNWORKABLE_IMAGE_MARKER


def test_describe_room_retries_on_transient_server_error(monkeypatch):
    # Real, live-reproduced gap (2026-09-25): describe_room() used to be a
    # bare, unretried Gemini call while generate_materials()/
    # generate_room_layout() already retried transient errors - a live 503
    # "high demand" (reproduced directly against the real API while
    # investigating a real non-room upload that slipped through) made
    # describe_room() raise, and run_pipeline()'s deliberate fail-open
    # (any exception -> room_description=None, so a hiccup never blocks a
    # legitimate upload) also silently skipped the entire room-photo gate on
    # that exact kind of transient error. This guards that a single 503 is
    # now retried, not treated as a reason to skip validation.
    analysis_cache.clear()
    call_count = {"n": 0}

    class FakeResponse:
        text = '{"workable": false, "description": ""}'

    class FakeModels:
        def generate_content(self, model, contents):
            call_count["n"] += 1
            if call_count["n"] < 2:
                raise genai_errors.ServerError(503, {"error": {"message": "high demand"}})
            return FakeResponse()

    class FakeClient:
        def __init__(self, api_key=None):
            self.models = FakeModels()

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    monkeypatch.setattr(gemini_module.time, "sleep", lambda seconds: None)

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(6))

    assert call_count["n"] == 2
    assert result == UNWORKABLE_IMAGE_MARKER


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
