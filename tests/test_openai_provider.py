import base64

import httpx

import app.providers.openai as openai_module
from app.providers.openai import OpenAIImageProvider


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_generate_image_decodes_b64_json_response(monkeypatch):
    encoded = base64.b64encode(b"decoded-png-bytes").decode("ascii")

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        assert "Authorization" in headers
        assert data["prompt"] == "make it premium"
        assert "image" in files
        return FakeResponse(json_data={"data": [{"b64_json": encoded}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)

    provider = OpenAIImageProvider()
    result = provider.generate_image(b"input-bytes", "make it premium")
    assert result == b"decoded-png-bytes"


def test_generate_image_uses_configured_model_and_quality(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["model"] = data["model"]
        captured["quality"] = data["quality"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_model", "gpt-image-2")
    monkeypatch.setattr(openai_module.settings, "openai_image_quality", "low")

    OpenAIImageProvider().generate_image(b"input-bytes", "prompt")

    assert captured["model"] == "gpt-image-2"
    assert captured["quality"] == "low"


def test_generate_image_always_sends_input_fidelity_explicitly(monkeypatch):
    # Regression guard for a real, expensive mistake: input_fidelity is a SEPARATE
    # param from quality that also drives cost heavily and defaults to "high" if
    # omitted - a real test that only set quality="low" still cost $0.10/image
    # (~20x the expected cost) because this was missing. Must always be sent.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["input_fidelity"] = data["input_fidelity"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_input_fidelity", "low")

    OpenAIImageProvider().generate_image(b"input-bytes", "prompt")

    assert captured["input_fidelity"] == "low"


def test_generate_image_uses_high_fidelity_for_premium_tier(monkeypatch):
    # Regression guard: premium's vivid luxury vocabulary was observed pulling
    # gpt-image-1 toward a hallucinated generic room at the default "low"
    # fidelity, even with the structural-lock text present in the prompt. Premium
    # must get "high" fidelity regardless of the configured default.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["input_fidelity"] = data["input_fidelity"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_input_fidelity", "low")

    OpenAIImageProvider().generate_image(b"input-bytes", "prompt", tier="premium")

    assert captured["input_fidelity"] == "high"


def test_generate_image_uses_configured_fidelity_for_other_tiers(monkeypatch):
    # Economical/mid should NOT pay the high-fidelity cost - only the tier that
    # actually showed the drift should.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["input_fidelity"] = data["input_fidelity"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_input_fidelity", "low")

    for tier in ("economical", "mid", None):
        OpenAIImageProvider().generate_image(b"input-bytes", "prompt", tier=tier)
        assert captured["input_fidelity"] == "low"


def test_generate_image_sends_prompt_unmodified(monkeypatch):
    # No negative_prompt/strength channel exists anymore - the prompt sent is
    # exactly what build_prompt() produced, with exclusions already stated
    # directly in it (see app/pipeline/prompts.py).
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["prompt"] = data["prompt"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)

    OpenAIImageProvider().generate_image(b"input-bytes", "budget renovation. Do not include: chandelier.")

    assert captured["prompt"] == "budget renovation. Do not include: chandelier."


def test_generate_image_unexpected_response_shape_raises(monkeypatch):
    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        return FakeResponse(json_data={"error": "something went wrong"})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)

    provider = OpenAIImageProvider()
    try:
        provider.generate_image(b"input-bytes", "prompt")
    except RuntimeError as exc:
        assert "unexpected OpenAI image response shape" in str(exc)
        return
    assert False, "expected RuntimeError"
