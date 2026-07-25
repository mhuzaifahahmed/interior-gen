import io

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app


class FakeProvider:
    def __init__(self):
        self.image_prompts = []

    def describe_room(self, image_bytes: bytes) -> str:
        return "A small rectangular room with one window."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {}

    def generate_image(
        self, image_bytes: bytes, prompt: str, negative_prompt: str = "", strength: float | None = None
    ) -> bytes:
        self.image_prompts.append(prompt)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(200, 200, 200)).save(buf, format="PNG")
        return buf.getvalue()


def _sample_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_full_upload_and_poll_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())

    from app.config import settings

    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))

    with TestClient(app) as client:
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post("/api/projects", files=files)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        assert status_res.status_code == 200
        body = status_res.json()
        assert body["status"] == "done"
        assert body["room_description"] == "A small rectangular room with one window."
        for key in ("original", "economical", "mid", "premium"):
            assert body["images"][key] is not None


def test_style_notes_reach_the_generated_prompts(monkeypatch, tmp_path):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)

    from app.config import settings

    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path))

    with TestClient(app) as client:
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/projects", files=files, data={"style_notes": "modern, blue accents"}
        )
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        assert status_res.json()["status"] == "done"

    assert len(provider.image_prompts) == 3
    for prompt in provider.image_prompts:
        assert "modern, blue accents" in prompt


def test_rejects_unsupported_file_type():
    with TestClient(app) as client:
        files = {"file": ("doc.pdf", b"not-an-image", "application/pdf")}
        res = client.post("/api/projects", files=files)
        assert res.status_code == 400


def test_unknown_project_returns_404():
    with TestClient(app) as client:
        res = client.get("/api/projects/does-not-exist")
        assert res.status_code == 404


def test_terms_page_serves():
    with TestClient(app) as client:
        res = client.get("/terms")
        assert res.status_code == 200
        assert "Terms" in res.text


def test_privacy_page_serves():
    with TestClient(app) as client:
        res = client.get("/privacy")
        assert res.status_code == 200
        assert "Privacy" in res.text
