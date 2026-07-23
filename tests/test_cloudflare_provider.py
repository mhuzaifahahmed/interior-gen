import base64

import httpx

import app.providers.cloudflare as cf_module
from app.providers.cloudflare import CloudflareImageProvider


class FakeResponse:
    def __init__(self, content=b"", headers=None, json_data=None, status_code=200):
        self.content = content
        self.headers = headers or {}
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


def test_generate_image_raw_bytes_response(monkeypatch):
    fake_bytes = b"raw-png-bytes"

    def fake_post(url, headers=None, json=None, timeout=None):
        assert "Authorization" in headers
        assert json["prompt"] == "make it premium"
        assert "image_b64" in json
        return FakeResponse(content=fake_bytes, headers={"content-type": "image/png"})

    monkeypatch.setattr(cf_module.httpx, "post", fake_post)

    provider = CloudflareImageProvider()
    result = provider.generate_image(b"input-bytes", "make it premium")
    assert result == fake_bytes


def test_generate_image_base64_json_response(monkeypatch):
    encoded = base64.b64encode(b"decoded-png-bytes").decode("ascii")

    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(
            headers={"content-type": "application/json"},
            json_data={"success": True, "result": {"image": encoded}},
        )

    monkeypatch.setattr(cf_module.httpx, "post", fake_post)

    provider = CloudflareImageProvider()
    result = provider.generate_image(b"input-bytes", "make it economical")
    assert result == b"decoded-png-bytes"


def test_generate_image_combines_base_and_tier_negative_prompt(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["negative_prompt"] = json["negative_prompt"]
        return FakeResponse(content=b"x", headers={"content-type": "image/png"})

    monkeypatch.setattr(cf_module.httpx, "post", fake_post)

    provider = CloudflareImageProvider()
    provider.generate_image(b"input-bytes", "prompt", negative_prompt="chandelier, marble")

    assert "chandelier, marble" in captured["negative_prompt"]
    assert cf_module.BASE_NEGATIVE_PROMPT in captured["negative_prompt"]


def test_generate_image_error_response_raises(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return FakeResponse(
            headers={"content-type": "application/json"},
            json_data={"success": False, "errors": ["quota exceeded"]},
        )

    monkeypatch.setattr(cf_module.httpx, "post", fake_post)

    provider = CloudflareImageProvider()
    try:
        provider.generate_image(b"input-bytes", "prompt")
    except RuntimeError as exc:
        assert "quota exceeded" in str(exc)
        return
    assert False, "expected RuntimeError"
