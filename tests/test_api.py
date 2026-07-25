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


class FakeStorage:
    """In-memory storage double. MUST be used by every test that exercises
    create_project()/the real app - get_storage() reads settings.storage_backend,
    which is whatever's actually in .env (s3 for real local dev use), so without
    this every test run would silently write real objects to the live S3 bucket.
    That's exactly what happened before this class existed - see git history/
    CLAUDE.md for the cleanup this caused.
    """

    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type="image/png"):
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]

    def url(self, key):
        return f"/media/{key}"


def _sample_image_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(100, 150, 200)).save(buf, format="PNG")
    return buf.getvalue()


def test_full_upload_and_poll_flow(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

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

        # Storage layout: uploads live under local.input/, generated tiers under
        # local.output/ - keeps the bucket organized and separates "things the user
        # gave us" from "things we generated", instead of one flat pile of UUID folders.
        assert "local.input/" in body["images"]["original"]
        for tier in ("economical", "mid", "premium"):
            assert "local.output/" in body["images"][tier]


def test_style_notes_reach_the_generated_prompts(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

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
