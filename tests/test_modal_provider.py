import base64

import httpx
import pytest

import app.providers.modal_provider as modal_module
from app.pipeline.prompts import KAGGLE_PROMPT_MAX_WORDS, build_prompt
from app.providers.modal_provider import ModalImageProvider


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_generate_image_decodes_generated_image_base64(monkeypatch):
    encoded = base64.b64encode(b"decoded-jpeg-bytes").decode("ascii")

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        assert url == "https://example.modal.run/generate"
        assert base64.b64decode(json["image_base64"]) == b"input-bytes"
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    provider = ModalImageProvider()
    result = provider.generate_image(b"input-bytes", "make it japandi")
    assert result == b"decoded-jpeg-bytes"


def test_generate_image_always_sends_num_inference_steps(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"jpeg-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")
    monkeypatch.setattr(modal_module.settings, "modal_num_inference_steps", 20)

    ModalImageProvider().generate_image(b"input-bytes", "prompt")

    assert captured["payload"]["num_inference_steps"] == 20


def test_generate_image_raises_on_status_error_in_response_body(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(json_data={"status": "error", "detail": "CUDA out of memory"})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        ModalImageProvider().generate_image(b"input-bytes", "prompt")


def test_generate_image_raises_on_unexpected_response_shape(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(json_data={"status": "success"})  # missing generated_image_base64

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    with pytest.raises(RuntimeError, match="unexpected Modal image response shape"):
        ModalImageProvider().generate_image(b"input-bytes", "prompt")


def test_generate_image_raises_on_http_error(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(json_data={}, status_code=500)

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    with pytest.raises(httpx.HTTPStatusError):
        ModalImageProvider().generate_image(b"input-bytes", "prompt")


def test_generate_image_sends_a_short_prompt_not_the_full_build_prompt_output(monkeypatch):
    full_prompt = build_prompt("mid", "Spanish", "Sage")
    assert len(full_prompt.split()) > 200

    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"jpeg-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    ModalImageProvider().generate_image(b"input-bytes", full_prompt, tier="mid")

    sent_prompt = captured["payload"]["prompt"]
    assert len(sent_prompt.split()) <= KAGGLE_PROMPT_MAX_WORDS
    assert sent_prompt != full_prompt
    assert "Spanish" in sent_prompt


# ---- generate_images_batch: a single blocking call (unlike Kaggle's ----
# submit-then-poll) - Modal has no free-tier proxy timeout to design around. ----


def test_generate_images_batch_sends_all_tiers_and_decodes_results(monkeypatch):
    encoded = [
        base64.b64encode(b"economical-bytes").decode("ascii"),
        base64.b64encode(b"mid-bytes").decode("ascii"),
        base64.b64encode(b"premium-bytes").decode("ascii"),
    ]
    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        assert url == "https://example.modal.run/generate_batch"
        captured["payload"] = json
        return FakeResponse(json_data={"status": "success", "generated_images_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        modal_module.settings, "modal_room_redesign_batch_url", "https://example.modal.run/generate_batch"
    )
    monkeypatch.setattr(modal_module.settings, "modal_batch_resolution", 768)

    provider = ModalImageProvider()
    result = provider.generate_images_batch(
        b"input-bytes",
        {"economical": "econ prompt", "mid": "mid prompt", "premium": "premium prompt"},
    )

    assert result == {
        "economical": b"economical-bytes",
        "mid": b"mid-bytes",
        "premium": b"premium-bytes",
    }
    assert captured["payload"]["resolution"] == 768
    assert len(captured["payload"]["items"]) == 3


def test_generate_images_batch_raises_on_status_error_in_response_body(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(json_data={"status": "error", "detail": "CUDA out of memory"})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        modal_module.settings, "modal_room_redesign_batch_url", "https://example.modal.run/generate_batch"
    )

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        ModalImageProvider().generate_images_batch(b"input-bytes", {"economical": "prompt"})


def test_generate_images_batch_raises_on_count_mismatch(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(
            json_data={"status": "success", "generated_images_base64": [base64.b64encode(b"one").decode()]}
        )

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        modal_module.settings, "modal_room_redesign_batch_url", "https://example.modal.run/generate_batch"
    )

    with pytest.raises(RuntimeError, match="unexpected Modal batch response shape"):
        ModalImageProvider().generate_images_batch(b"input-bytes", {"economical": "a", "mid": "b"})


def test_supports_batch_is_true():
    assert ModalImageProvider().supports_batch() is True


# ---- generate_house_render: separate deployment/URL, prompt passed through ----
# unmodified (no CLIP-length shortening - house prompts are already capped ----
# upstream by house_prompts.py). ----


def test_generate_house_render_decodes_generated_image_base64(monkeypatch):
    encoded = base64.b64encode(b"house-render-bytes").decode("ascii")
    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        assert url == "https://example.modal.run/house"
        captured["payload"] = json
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_house_url", "https://example.modal.run/house")

    result = ModalImageProvider().generate_house_render(b"plot-bytes", "a full house prompt, unshortened")

    assert result == b"house-render-bytes"
    assert captured["payload"]["prompt"] == "a full house prompt, unshortened"


def test_generate_house_render_raises_on_status_error(monkeypatch):
    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        return FakeResponse(json_data={"status": "error", "detail": "model error"})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_house_url", "https://example.modal.run/house")

    with pytest.raises(RuntimeError, match="model error"):
        ModalImageProvider().generate_house_render(b"plot-bytes", "prompt")


# ---- follow_redirects: real, live-observed behavior - Modal's own web ----
# endpoints can respond with a 303 carrying a "__modal_attempt_token" on a
# cold-start/retry (part of Modal's own protocol, not an error). httpx does
# NOT follow redirects by default, so every call must explicitly opt in or a
# legitimate cold-start response gets raised as an HTTPStatusError. ----


def test_generate_image_follows_redirects(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        captured["follow_redirects"] = follow_redirects
        encoded = base64.b64encode(b"jpeg-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_room_redesign_url", "https://example.modal.run/generate")

    ModalImageProvider().generate_image(b"input-bytes", "prompt")

    assert captured["follow_redirects"] is True


def test_generate_images_batch_follows_redirects(monkeypatch):
    captured = {}
    encoded = base64.b64encode(b"jpeg-bytes").decode("ascii")

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        captured["follow_redirects"] = follow_redirects
        return FakeResponse(json_data={"status": "success", "generated_images_base64": [encoded]})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(
        modal_module.settings, "modal_room_redesign_batch_url", "https://example.modal.run/generate_batch"
    )

    ModalImageProvider().generate_images_batch(b"input-bytes", {"economical": "prompt"})

    assert captured["follow_redirects"] is True


def test_generate_house_render_follows_redirects(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, follow_redirects=None):
        captured["follow_redirects"] = follow_redirects
        encoded = base64.b64encode(b"jpeg-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(modal_module.httpx, "post", fake_post)
    monkeypatch.setattr(modal_module.settings, "modal_house_url", "https://example.modal.run/house")

    ModalImageProvider().generate_house_render(b"plot-bytes", "prompt")

    assert captured["follow_redirects"] is True
