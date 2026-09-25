import io

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers import analysis_cache
from app.providers.gemini import GeminiProvider


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


def test_validate_room_photo_true_when_gemini_says_valid(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", _fake_client_returning("VALID"))

    provider = GeminiProvider()
    assert provider.validate_room_photo(_sample_image_bytes(1)) is True


def test_validate_room_photo_false_when_gemini_says_invalid(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", _fake_client_returning("INVALID"))

    provider = GeminiProvider()
    assert provider.validate_room_photo(_sample_image_bytes(2)) is False


def test_validate_room_photo_tolerates_stray_whitespace_and_case(monkeypatch):
    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", _fake_client_returning("  invalid.\n"))

    provider = GeminiProvider()
    assert provider.validate_room_photo(_sample_image_bytes(3)) is False


def test_validate_room_photo_fails_open_on_unexpected_response(monkeypatch):
    # Anything that isn't a confident "INVALID" must be treated as valid -
    # this is a fail-open check, not fail-closed.
    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", _fake_client_returning("uh, sure, looks fine"))

    provider = GeminiProvider()
    assert provider.validate_room_photo(_sample_image_bytes(4)) is True


def test_validate_room_photo_fails_open_when_client_raises(monkeypatch):
    class BrokenModels:
        def generate_content(self, model, contents):
            raise RuntimeError("network error")

    class BrokenClient:
        def __init__(self, api_key=None):
            self.models = BrokenModels()

    analysis_cache.clear()
    monkeypatch.setattr(gemini_module.genai, "Client", BrokenClient)

    provider = GeminiProvider()
    assert provider.validate_room_photo(_sample_image_bytes(5)) is True


def test_validate_room_photo_caches_result_per_image(monkeypatch):
    analysis_cache.clear()
    call_count = 0

    class FakeResponse:
        text = "INVALID"

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
    image_bytes = _sample_image_bytes(6)
    assert provider.validate_room_photo(image_bytes) is False
    assert provider.validate_room_photo(image_bytes) is False
    assert call_count == 1
