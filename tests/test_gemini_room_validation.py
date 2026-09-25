import io

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers import analysis_cache
from app.providers.gemini import UNWORKABLE_IMAGE_MARKER, GeminiProvider


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


def test_describe_room_prompt_asks_gemini_to_return_the_marker_when_unworkable(monkeypatch):
    # Not asserting exact prompt wording (that's brittle), just that the
    # marker Gemini is told to respond with is actually present in the
    # instruction it's given. The classifier prompt is allowed to give
    # Gemini illustrative examples (logo, diagram, screenshot, etc.) - the
    # "never say what the upload actually was" requirement applies to the
    # USER-FACING rejection message (see run_pipeline's fixed generic
    # string), not to Gemini's own internal classification instructions.
    captured = {}

    class FakeResponse:
        text = "A rectangular room."

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

    assert UNWORKABLE_IMAGE_MARKER in captured["prompt"]


def test_describe_room_returns_marker_when_gemini_flags_it_unworkable(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", _fake_client_returning(UNWORKABLE_IMAGE_MARKER))

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(1))
    assert UNWORKABLE_IMAGE_MARKER in result


def test_describe_room_tolerates_stray_formatting_around_the_marker(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai, "Client", _fake_client_returning(f"  {UNWORKABLE_IMAGE_MARKER}.\n")
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(2))
    assert UNWORKABLE_IMAGE_MARKER in result


def test_describe_room_returns_real_description_for_a_workable_photo(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(
        gemini_module.genai,
        "Client",
        _fake_client_returning("A bedroom with one window on the left wall."),
    )

    provider = GeminiProvider()
    result = provider.describe_room(_sample_image_bytes(3))
    assert UNWORKABLE_IMAGE_MARKER not in result
    assert "bedroom" in result


def test_describe_room_caches_the_marker_result_per_image(monkeypatch):
    analysis_cache.clear()
    call_count = 0

    class FakeResponse:
        text = UNWORKABLE_IMAGE_MARKER

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
    assert UNWORKABLE_IMAGE_MARKER in provider.describe_room(image_bytes)
    assert UNWORKABLE_IMAGE_MARKER in provider.describe_room(image_bytes)
    assert call_count == 1
