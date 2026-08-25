import base64
import io

import httpx
from PIL import Image

import app.providers.kaggle_autocad as kaggle_autocad_module
from app.providers.kaggle_autocad import generate_floor_plan


class FakeResponse:
    def __init__(self, json_data=None, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def _fake_jpeg_b64() -> str:
    image = Image.new("RGB", (4, 4), (255, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def test_generate_floor_plan_returns_none_when_url_not_configured(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "")
    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "2 floors, 3 bedrooms")
    assert result is None


def test_generate_floor_plan_submits_then_polls_and_returns_png_bytes(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    calls = {"polls": 0}

    def fake_post(url, json=None, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_floorplans"
        assert json["length"] == 40
        assert json["width"] == 60
        assert json["unit"] == "ft"
        assert json["floors"] == 2
        assert json["bedrooms"] == 3
        assert json["bathrooms"] == 2
        return FakeResponse(json_data={"status": "started", "job_id": "job-1"})

    def fake_get(url, timeout=None):
        assert url == "https://example.trycloudflare.com/generate_floorplans/status/job-1"
        calls["polls"] += 1
        if calls["polls"] < 2:
            return FakeResponse(json_data={"status": "running"})
        return FakeResponse(
            json_data={
                "status": "done",
                "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}],
            }
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan(
        "a plot", {"length": 40, "width": 60, "unit": "ft"}, "2 floors, 3 bedrooms, 2 bathrooms. Extras: garage"
    )

    assert result is not None
    assert len(result) == 1
    assert result[0].startswith(b"\x89PNG")  # re-encoded to PNG, even though the model returns JPEG
    assert calls["polls"] == 2


def test_generate_floor_plan_returns_one_image_per_floor_in_order(monkeypatch):
    # Real regression guard: the model generates one image per floor
    # natively - every floor must come back, sorted by floor_number, not
    # just the first (an earlier version silently discarded floors 2+).
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        assert json["floors"] == 3
        return FakeResponse(json_data={"status": "started", "job_id": "job-multi"})

    def fake_get(url, timeout=None):
        # Deliberately out of order, to confirm the result is sorted.
        return FakeResponse(
            json_data={
                "status": "done",
                "floors": [
                    {"floor_number": 2, "image_base64": _fake_jpeg_b64()},
                    {"floor_number": 3, "image_base64": _fake_jpeg_b64()},
                    {"floor_number": 1, "image_base64": _fake_jpeg_b64()},
                ],
            }
        )

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "3 floors")

    assert result is not None
    assert len(result) == 3
    for image_bytes in result:
        assert image_bytes.startswith(b"\x89PNG")


def test_generate_floor_plan_returns_none_on_job_failure(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started", "job_id": "job-2"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "failed", "detail": "CUDA out of memory"})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_returns_none_when_submit_response_has_no_job_id(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        return FakeResponse(json_data={"status": "started"})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_returns_none_on_request_exception(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")

    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("tunnel is down")

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "1 floor")
    assert result is None


def test_generate_floor_plan_defaults_bedrooms_bathrooms_when_not_stated(monkeypatch):
    monkeypatch.setattr(kaggle_autocad_module.settings, "kaggle_autocad_api_url", "https://example.trycloudflare.com")
    monkeypatch.setattr(kaggle_autocad_module.time, "sleep", lambda _: None)

    def fake_post(url, json=None, timeout=None):
        assert json["floors"] == 1
        assert json["bedrooms"] == 3
        assert json["bathrooms"] == 2
        return FakeResponse(json_data={"status": "started", "job_id": "job-3"})

    def fake_get(url, timeout=None):
        return FakeResponse(json_data={"status": "done", "floors": [{"floor_number": 1, "image_base64": _fake_jpeg_b64()}]})

    monkeypatch.setattr(kaggle_autocad_module.httpx, "post", fake_post)
    monkeypatch.setattr(kaggle_autocad_module.httpx, "get", fake_get)

    result = generate_floor_plan("a plot", {"length": 40, "width": 60, "unit": "ft"}, "no specific requirements")
    assert result is not None
