import io

from PIL import Image

import app.providers.gemini as gemini_module
from app.providers.gemini import GeminiProvider


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
