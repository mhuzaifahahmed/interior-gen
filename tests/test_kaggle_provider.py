import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

import app.providers.kaggle as kaggle_module
from app.pipeline.prompts import KAGGLE_PROMPT_MAX_WORDS, build_prompt
from app.providers.kaggle import KaggleImageProvider, _generate_url, _prepare_kaggle_prompt


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
    encoded = base64.b64encode(b"decoded-png-bytes").decode("ascii")

    def fake_post(url, json=None, timeout=None):
        assert url == "https://example.trycloudflare.com/generate"
        assert json["prompt"] == "make it japandi"
        assert base64.b64decode(json["image_base64"]) == b"input-bytes"
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    provider = KaggleImageProvider()
    result = provider.generate_image(b"input-bytes", "make it japandi")
    assert result == b"decoded-png-bytes"


def test_generate_image_always_sends_num_inference_steps(monkeypatch):
    # Regression guard: the endpoint's own default is 30 (confirmed via its
    # live /openapi.json schema), but a real side-by-side timing+visual
    # comparison found 20 the accepted speed/quality middle ground - this
    # must be sent explicitly on every call, not left to the endpoint default.
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_module.settings, "kaggle_num_inference_steps", 20)

    KaggleImageProvider().generate_image(b"input-bytes", "prompt")

    assert captured["payload"]["num_inference_steps"] == 20


def test_generate_image_raises_on_unexpected_response_shape(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "error", "detail": "model not loaded"})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    with pytest.raises(RuntimeError):
        KaggleImageProvider().generate_image(b"input-bytes", "prompt")


def test_generate_image_raises_on_http_error(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={}, status_code=500)

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    with pytest.raises(httpx.HTTPStatusError):
        KaggleImageProvider().generate_image(b"input-bytes", "prompt")


# ---- _generate_url: KAGGLE_API_URL has been observed set both as a bare
# tunnel root and as a full URL with /generate already appended - real,
# live-observed ambiguity, not theoretical (see the module docstring). ----


def test_generate_url_appends_generate_to_a_bare_base_url():
    assert _generate_url("https://example.trycloudflare.com") == "https://example.trycloudflare.com/generate"


def test_generate_url_does_not_double_append_when_already_present():
    assert (
        _generate_url("https://example.trycloudflare.com/generate")
        == "https://example.trycloudflare.com/generate"
    )


def test_generate_url_strips_a_trailing_slash():
    assert _generate_url("https://example.trycloudflare.com/") == "https://example.trycloudflare.com/generate"


def test_concurrent_calls_are_serialized_not_sent_in_parallel(monkeypatch):
    # Regression guard for a real, live-observed failure: the room-redesign
    # pipeline fires all 3 tiers concurrently, which crashed a real Kaggle
    # notebook's model ("tensors on different devices") under simultaneous
    # requests. This proves generate_image() actually queues concurrent
    # callers instead of letting their HTTP calls overlap.
    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    concurrent_count = 0
    max_concurrent_seen = 0
    lock = threading.Lock()

    def fake_post(url, json=None, timeout=None):
        nonlocal concurrent_count, max_concurrent_seen
        with lock:
            concurrent_count += 1
            max_concurrent_seen = max(max_concurrent_seen, concurrent_count)
        time.sleep(0.05)
        with lock:
            concurrent_count -= 1
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    provider = KaggleImageProvider()
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda i: provider.generate_image(b"x", f"prompt {i}"), range(3)))

    assert max_concurrent_seen == 1


# ---- prompt shortening: the model's CLIP encoder has a ~77-token limit, so
# build_prompt()'s full ~600-word output must never be sent as-is. ----


def test_generate_image_sends_a_short_prompt_not_the_full_build_prompt_output(monkeypatch):
    full_prompt = build_prompt("mid", "Spanish", "Sage")
    assert len(full_prompt.split()) > 200  # sanity: this really is the long OpenAI-shaped prompt

    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    KaggleImageProvider().generate_image(b"input-bytes", full_prompt, tier="mid")

    sent_prompt = captured["payload"]["prompt"]
    assert len(sent_prompt.split()) <= KAGGLE_PROMPT_MAX_WORDS
    assert sent_prompt != full_prompt
    assert "Spanish" in sent_prompt


def test_generate_image_sends_a_negative_prompt_when_style_and_palette_are_known(monkeypatch):
    full_prompt = build_prompt("economical", "Modern", "Neutral")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    KaggleImageProvider().generate_image(b"input-bytes", full_prompt, tier="economical")

    assert "chandelier" in captured["payload"]["negative_prompt"]


def test_generate_image_omits_negative_prompt_when_tier_is_unknown(monkeypatch):
    # Fallback path (no tier / can't extract style+palette) - never send a
    # negative_prompt built from a guessed tier.
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        encoded = base64.b64encode(b"png-bytes").decode("ascii")
        return FakeResponse(json_data={"status": "success", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    KaggleImageProvider().generate_image(b"input-bytes", "some arbitrary prompt with no known shape")

    assert "negative_prompt" not in captured["payload"]


def test_prepare_kaggle_prompt_uses_the_real_build_prompt_data_when_tier_is_known():
    full_prompt = build_prompt("premium", "Japandi", "Sage")
    short_prompt, negative_prompt = _prepare_kaggle_prompt(full_prompt, "premium")

    assert len(short_prompt.split()) <= KAGGLE_PROMPT_MAX_WORDS
    assert "Japandi" in short_prompt
    # Premium has no NEGATIVE_ADDITIONS, but still gets the base quality terms.
    assert negative_prompt is not None
    assert "chandelier" not in negative_prompt


def test_prepare_kaggle_prompt_falls_back_to_truncation_for_unrecognized_text():
    unrelated_text = " ".join(f"word{i}" for i in range(200))
    short_prompt, negative_prompt = _prepare_kaggle_prompt(unrelated_text, None)

    assert len(short_prompt.split()) == KAGGLE_PROMPT_MAX_WORDS
    assert negative_prompt is None


# ---- generate_images_batch: submit-then-poll, not one blocking call - see
# the method's own docstring for why (a real live test showed the free
# Cloudflare quick tunnel hard-kills any single request open past ~100s,
# independent of whether the GPU work itself succeeds). ----


def test_generate_images_batch_submits_and_polls_until_done(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_module.settings, "kaggle_batch_resolution", 768)

    encoded = [
        base64.b64encode(b"economical-bytes").decode("ascii"),
        base64.b64encode(b"mid-bytes").decode("ascii"),
        base64.b64encode(b"premium-bytes").decode("ascii"),
    ]
    poll_responses = [
        {"status": "running"},
        {"status": "running"},
        {"status": "done", "generated_images_base64": encoded},
    ]
    submit_calls = []
    poll_calls = []

    def fake_post(url, json=None, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_batch"
        assert json["resolution"] == 768
        assert len(json["items"]) == 3
        submit_calls.append(json)
        return FakeResponse(json_data={"status": "started", "job_id": "job-123"})

    def fake_get(url, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_batch/status/job-123"
        poll_calls.append(url)
        return FakeResponse(json_data=poll_responses[len(poll_calls) - 1])

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    provider = KaggleImageProvider()
    result = provider.generate_images_batch(
        b"input-bytes",
        {"economical": "econ prompt", "mid": "mid prompt", "premium": "premium prompt"},
    )

    assert result == {
        "economical": b"economical-bytes",
        "mid": b"mid-bytes",
        "premium": b"premium-bytes",
    }
    assert len(submit_calls) == 1
    assert len(poll_calls) == 3


def test_generate_images_batch_raises_on_job_failed(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-456"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "failed", "detail": "CUDA out of memory"})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    provider = KaggleImageProvider()
    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        provider.generate_images_batch(b"input-bytes", {"economical": "prompt"})


def test_generate_images_batch_raises_when_polling_exceeds_deadline(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_module, "BATCH_POLL_MAX_SECONDS", 0)

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-789"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "running"})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    provider = KaggleImageProvider()
    with pytest.raises(RuntimeError, match="did not complete within"):
        provider.generate_images_batch(b"input-bytes", {"economical": "prompt"})


def test_generate_images_batch_raises_on_missing_job_id(monkeypatch):
    monkeypatch.setattr(kaggle_module.settings, "kaggle_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started"})  # no job_id

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)

    provider = KaggleImageProvider()
    with pytest.raises(RuntimeError, match="unexpected Kaggle batch submit response shape"):
        provider.generate_images_batch(b"input-bytes", {"economical": "prompt"})


# ---- house render: real elevation model (text-to-image, submit-then-poll) ----
# The house backend is RealVisXL_V4.0 (SDXL) TEXT-TO-IMAGE - it takes NO input
# image and uses the same submit-then-poll shape as the room batch endpoint, on
# /generate_elevation. See KaggleImageProvider.generate_house_render()'s
# docstring + kaggle_notebooks/elevation_server.py for the confirmed contract.


def test_generate_house_render_submits_and_polls_then_decodes(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )
    encoded = base64.b64encode(b"decoded-elevation-png").decode("ascii")
    submit_calls = []
    poll_calls = []

    def fake_post(url, json=None, timeout=None):
        assert url == "https://house-example.trycloudflare.com/generate_elevation"
        # Text-to-image: NO input image is ever sent.
        assert "image_base64" not in json
        submit_calls.append(json)
        return FakeResponse(json_data={"status": "started", "job_id": "elev-1"})

    def fake_get(url, timeout=None):
        assert url == "https://house-example.trycloudflare.com/generate_elevation/status/elev-1"
        poll_calls.append(url)
        # First poll still running, second poll done.
        if len(poll_calls) == 1:
            return FakeResponse(json_data={"status": "running"})
        return FakeResponse(json_data={"status": "done", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    result = KaggleImageProvider().generate_house_render(None, "modern car porch", floor_count=2)
    assert result == b"decoded-elevation-png"
    assert len(submit_calls) == 1
    assert len(poll_calls) == 2


def test_generate_house_render_sends_prompt_and_structured_floor_count(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(json_data={"status": "started", "job_id": "elev-2"})

    def fake_get(url, timeout=None):
        encoded = base64.b64encode(b"png").decode("ascii")
        return FakeResponse(json_data={"status": "done", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    KaggleImageProvider().generate_house_render(None, "glass balcony, teak louvers", floor_count=3)
    assert captured["payload"]["prompt"] == "glass balcony, teak louvers"
    assert captured["payload"]["floors"] == 3


def test_generate_house_render_omits_floors_when_not_given(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(json_data={"status": "started", "job_id": "elev-3"})

    def fake_get(url, timeout=None):
        encoded = base64.b64encode(b"png").decode("ascii")
        return FakeResponse(json_data={"status": "done", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    KaggleImageProvider().generate_house_render(None, "")  # no floor_count
    assert "floors" not in captured["payload"]
    assert captured["payload"]["prompt"] == ""


def test_generate_house_render_sends_structured_garage_flag(monkeypatch):
    # Real user report (2026-09): a garage appeared in the render nobody asked
    # for. The app now sends a deterministic garage bool so the notebook can
    # include/exclude one instead of the model deciding on its own.
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return FakeResponse(json_data={"status": "started", "job_id": "elev-g"})

    def fake_get(url, timeout=None):
        encoded = base64.b64encode(b"png").decode("ascii")
        return FakeResponse(json_data={"status": "done", "generated_image_base64": encoded})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    provider = KaggleImageProvider()
    provider.generate_house_render(None, "", wants_garage=False)
    assert captured["payload"]["garage"] is False

    provider.generate_house_render(None, "", wants_garage=True)
    assert captured["payload"]["garage"] is True

    # No signal (None) -> the key is omitted entirely, so an un-updated
    # notebook keeps its old behavior.
    provider.generate_house_render(None, "", wants_garage=None)
    assert "garage" not in captured["payload"]


def test_generate_house_render_raises_on_job_failed(monkeypatch):
    monkeypatch.setattr(kaggle_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "elev-4"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "failed", "detail": "CUDA out of memory"})

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_module.httpx, "get", fake_get)

    with pytest.raises(RuntimeError, match="CUDA out of memory"):
        KaggleImageProvider().generate_house_render(None, "prompt", floor_count=1)


def test_generate_house_render_raises_on_missing_job_id(monkeypatch):
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started"})  # no job_id

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)

    with pytest.raises(RuntimeError, match="unexpected Kaggle elevation submit response shape"):
        KaggleImageProvider().generate_house_render(None, "prompt", floor_count=1)


def test_generate_house_render_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        kaggle_module.settings, "kaggle_house_api_url", "https://house-example.trycloudflare.com"
    )

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={}, status_code=500)

    monkeypatch.setattr(kaggle_module.httpx, "post", fake_post)

    with pytest.raises(httpx.HTTPStatusError):
        KaggleImageProvider().generate_house_render(None, "prompt", floor_count=1)
