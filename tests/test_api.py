import io
import json

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app
from tests.conftest import login_as

# Generators are gated behind login now - every test hitting them needs a
# logged-in session first. login_as() (tests/conftest.py) attaches a fake
# Clerk Bearer token, standing in for the real signup+login flow Clerk now
# owns entirely (see CLAUDE.md's "Authentication" section).
_signup_and_login = login_as


class FakeProvider:
    def __init__(self):
        self.image_prompts = []
        self.materials_calls = []

    def describe_room(self, image_bytes: bytes) -> str:
        return "A small rectangular room with one window."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {}

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        self.image_prompts.append(prompt)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(200, 200, 200)).save(buf, format="PNG")
        return buf.getvalue()

    def generate_materials(self, tier, tier_spec, room_description, city, api_key=None, room_area_sqft=None):
        self.materials_calls.append((tier, city))
        return {
            "items": [{"name": "Flooring", "spec": "", "price": "$100", "currency": "USD",
                       "source_url": None, "is_estimate": True}],
            "total": "$100",
            "currency": "USD",
        }

    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        return 180.0


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


# Interior Style + Color Palette are REQUIRED form fields (see app/main.py's
# create_project) - every test that hits POST /api/projects needs valid values
# for both, same as it already needs a logged-in session.
_REQUIRED_STYLE_FIELDS = {"interior_style": "Modern", "color_palette": "Neutral"}


def test_full_upload_and_poll_flow(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        username = _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        assert status_res.status_code == 200
        body = status_res.json()
        assert body["status"] == "done"
        assert body["room_description"] == "A small rectangular room with one window."
        for key in ("original", "economical", "mid", "premium"):
            assert body["images"][key] is not None

        # Storage layout: uploads live under users/{username}/input/, generated
        # tiers under users/{username}/output/ - namespaces every user's files
        # under their own prefix (see CLAUDE.md's "Authentication & per-user
        # storage" section) while still separating "things the user gave us"
        # from "things we generated".
        assert f"users/{username}/input/" in body["images"]["original"]
        for tier in ("economical", "mid", "premium"):
            assert f"users/{username}/output/" in body["images"][tier]

        # No city was submitted - images-only path, no materials/pricing calls.
        assert body["materials_status"] == "skipped"
        assert body["materials"] is None


def test_input_metadata_json_written_to_storage(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(app) as client:
        username = _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        data = {
            "interior_style": "Modern",
            "color_palette": "Neutral",
            "additional_instructions": "add a reading nook",
            "city": "Karachi",
        }
        create_res = client.post("/api/projects", files=files, data=data)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        metadata_key = f"users/{username}/input/{project_id}/metadata.json"
        assert metadata_key in storage.objects
        saved = json.loads(storage.objects[metadata_key])
        assert saved == {
            "interior_style": "Modern",
            "color_palette": "Neutral",
            "additional_instructions": "add a reading nook",
            "city": "Karachi",
        }


def test_interior_style_and_palette_reach_the_generated_prompts(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/projects",
            files=files,
            data={"interior_style": "Japandi", "color_palette": "Sage"},
        )
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        body = status_res.json()
        assert body["status"] == "done"
        assert body["interior_style"] == "Japandi"
        assert body["color_palette"] == "Sage"

    assert len(provider.image_prompts) == 3
    for prompt in provider.image_prompts:
        assert "Japandi" in prompt
        assert "sage green" in prompt


def test_additional_instructions_reach_the_generated_prompts(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/projects",
            files=files,
            data={**_REQUIRED_STYLE_FIELDS, "additional_instructions": "blue sofa"},
        )
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        body = status_res.json()
        assert body["status"] == "done"
        assert body["additional_instructions"] == "blue sofa"

    assert len(provider.image_prompts) == 3
    for prompt in provider.image_prompts:
        assert "blue sofa" in prompt


def test_missing_interior_style_is_rejected():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/projects", files=files, data={"color_palette": "Neutral"})
        assert res.status_code == 400


def test_missing_color_palette_is_rejected():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/projects", files=files, data={"interior_style": "Modern"})
        assert res.status_code == 400


def test_invalid_interior_style_is_rejected():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post(
            "/api/projects",
            files=files,
            data={"interior_style": "Not A Real Style", "color_palette": "Neutral"},
        )
        assert res.status_code == 400


def test_invalid_color_palette_is_rejected():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post(
            "/api/projects",
            files=files,
            data={"interior_style": "Modern", "color_palette": "Not A Real Palette"},
        )
        assert res.status_code == 400


def test_city_triggers_materials_for_every_tier(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/projects", files=files, data={**_REQUIRED_STYLE_FIELDS, "city": "Karachi"}
        )
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        body = status_res.json()
        assert body["status"] == "done"
        assert body["materials_status"] == "done"
        assert set(body["materials"].keys()) == {"economical", "mid", "premium"}
        for tier_materials in body["materials"].values():
            assert tier_materials["items"][0]["name"] == "Flooring"

    called_tiers = {tier for tier, _ in provider.materials_calls}
    assert called_tiers == {"economical", "mid", "premium"}
    assert all(city == "Karachi" for _, city in provider.materials_calls)


def test_rejects_unsupported_file_type():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("doc.pdf", b"not-an-image", "application/pdf")}
        res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert res.status_code == 400


def test_create_project_requires_login():
    with TestClient(app) as client:
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert res.status_code == 401


def test_unknown_project_returns_404():
    with TestClient(app) as client:
        _signup_and_login(client)
        res = client.get("/api/projects/does-not-exist")
        assert res.status_code == 404


def test_cannot_view_another_users_project(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client_a:
        _signup_and_login(client_a)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client_a.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        project_id = create_res.json()["project_id"]

    with TestClient(app) as client_b:
        _signup_and_login(client_b)
        res = client_b.get(f"/api/projects/{project_id}")
        assert res.status_code == 404


def test_list_projects_returns_own_projects_newest_first(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        first = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS).json()["project_id"]
        second = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS).json()["project_id"]

        res = client.get("/api/projects")
        assert res.status_code == 200
        body = res.json()
        assert [p["project_id"] for p in body] == [second, first]
        assert body[0]["created_at"] is not None


def test_list_projects_requires_login():
    with TestClient(app) as client:
        res = client.get("/api/projects")
        assert res.status_code == 401


def test_list_projects_excludes_other_users(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client_a:
        _signup_and_login(client_a)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        client_a.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)

    with TestClient(app) as client_b:
        _signup_and_login(client_b)
        res = client_b.get("/api/projects")
        assert res.status_code == 200
        assert res.json() == []


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
