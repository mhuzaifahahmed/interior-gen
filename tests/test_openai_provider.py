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


def test_generate_image_uses_high_fidelity_for_premium_and_mid_tiers(monkeypatch):
    # Regression guard: premium's vivid luxury vocabulary was observed pulling
    # gpt-image-1 toward a hallucinated generic room at the default "low"
    # fidelity, even with the structural-lock text present in the prompt. Mid
    # showed the same failure shape (camera angle/geometry drift) even after
    # already having its own prompt-level structure_reminder fix - confirming
    # it needed the fidelity escalation too, not more prompt tuning. Both must
    # get "high" fidelity regardless of the configured default.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["input_fidelity"] = data["input_fidelity"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_input_fidelity", "low")

    for tier in ("premium", "mid"):
        OpenAIImageProvider().generate_image(b"input-bytes", "prompt", tier=tier)
        assert captured["input_fidelity"] == "high"


def test_generate_image_uses_configured_fidelity_for_other_tiers(monkeypatch):
    # Economical should NOT pay the high-fidelity cost - only tiers that
    # actually showed the drift should.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["input_fidelity"] = data["input_fidelity"]
        return FakeResponse(json_data={"data": [{"b64_json": ""}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_image_input_fidelity", "low")

    for tier in ("economical", None):
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


def test_generate_house_render_shares_the_edit_call_shape(monkeypatch):
    # generate_house_render is its own public method (not a reuse of
    # generate_image directly) so the house feature's params never get
    # tangled with room-redesign's tier semantics, but shares the same
    # underlying request shape via the private _edit_image helper.
    captured = {}

    def fake_post(url, headers=None, data=None, files=None, timeout=None):
        captured["prompt"] = data["prompt"]
        captured["input_fidelity"] = data["input_fidelity"]
        assert "image" in files
        return FakeResponse(json_data={"data": [{"b64_json": base64.b64encode(b"render-bytes").decode()}]})

    monkeypatch.setattr(openai_module.httpx, "post", fake_post)
    monkeypatch.setattr(openai_module.settings, "openai_house_input_fidelity", "high")

    result = OpenAIImageProvider().generate_house_render(b"plot-bytes", "a modern house concept render")

    assert result == b"render-bytes"
    assert captured["prompt"] == "a modern house concept render"
    # Uses its own dedicated setting, not the room-redesign one.
    assert captured["input_fidelity"] == "high"


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
