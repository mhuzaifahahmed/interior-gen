import io
import json
import uuid

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

import app.main as main_module
from app.db import engine
from app.main import app
from app.models import Project
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
        self.estimate_room_area_calls = 0

    def describe_room(self, image_bytes: bytes) -> str:
        return "A small rectangular room with one window."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {}

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        self.image_prompts.append(prompt)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(200, 200, 200)).save(buf, format="PNG")
        return buf.getvalue()

    def generate_materials(
        self, tier, tier_spec, room_description, city, api_key=None, room_area_sqft=None, wall_area_sqft=None
    ):
        self.materials_calls.append((tier, city, room_area_sqft, wall_area_sqft))
        return {
            "items": [{"name": "Flooring", "spec": "", "price": "$100", "currency": "USD",
                       "source_url": None, "is_estimate": True}],
            "total": "$100",
            "currency": "USD",
        }

    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        self.estimate_room_area_calls += 1
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

        # Storage layout: uploads live under users/{username}/roomRedesign/input/,
        # generated tiers under users/{username}/roomRedesign/output/ -
        # namespaces every user's files under their own prefix (see CLAUDE.md's
        # "Authentication & per-user storage" section) while still separating
        # "things the user gave us" from "things we generated", AND separating
        # Room Redesign's files from Build a House's under a per-feature folder.
        assert f"users/{username}/roomRedesign/input/" in body["images"]["original"]
        for tier in ("economical", "mid", "premium"):
            assert f"users/{username}/roomRedesign/output/" in body["images"][tier]

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

        metadata_key = f"users/{username}/roomRedesign/input/{project_id}/metadata.json"
        assert metadata_key in storage.objects
        saved = json.loads(storage.objects[metadata_key])
        assert saved == {
            "interior_style": "Modern",
            "color_palette": "Neutral",
            "additional_instructions": "add a reading nook",
            "city": "Karachi",
            "room_dimensions": None,
        }


def test_storage_namespace_sanitizes_and_truncates_display_name():
    from app.main import _storage_namespace

    assert _storage_namespace("user_abc", "Jane Doe") == "user_abc_jane_doe"
    assert _storage_namespace("user_abc", "") == "user_abc"
    assert _storage_namespace("user_abc", "   ") == "user_abc"
    assert _storage_namespace("user_abc", "Jane!! Doe??") == "user_abc_jane_doe"
    assert _storage_namespace("user_abc", "a" * 100) == f"user_abc_{'a' * 40}"


def test_display_name_appended_to_storage_namespace(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        data = {**_REQUIRED_STYLE_FIELDS, "display_name": "Jane Doe"}
        create_res = client.post("/api/projects", files=files, data=data)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        expected_key = f"users/{user_id}_jane_doe/roomRedesign/input/{project_id}/original.png"
        assert expected_key in storage.objects


def test_no_display_name_falls_back_to_bare_user_id(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        create_res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        expected_key = f"users/{user_id}/roomRedesign/input/{project_id}/original.png"
        assert expected_key in storage.objects


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

    called_tiers = {tier for tier, _, _, _ in provider.materials_calls}
    assert called_tiers == {"economical", "mid", "premium"}
    assert all(city == "Karachi" for _, city, _, _ in provider.materials_calls)


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


def test_cancel_project_flips_a_running_project_to_cancelled():
    # POST /api/projects normally completes its background task
    # synchronously within TestClient's request (see test_full_upload_and_
    # poll_flow - the project is already "done" by the time the create
    # response returns), so there's no way to catch a project mid-generation
    # through the real endpoint in this test setup. Create the row directly
    # in "running" state instead, mirroring what a real in-progress
    # generation looks like from the DB's perspective. Uses a real random id
    # (not a fixed literal) since this app has no test-DB isolation - the
    # real dev database persists across test runs, so a fixed id would
    # collide with the row this same test left behind last time.
    pid = f"cancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            project = Project(id=pid, status="running", user_id=user_id)
            session.add(project)
            session.commit()

        res = client.post(f"/api/projects/{pid}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

        with Session(engine) as session:
            project = session.get(Project, pid)
            assert project.status == "cancelled"


def test_cancel_project_is_a_noop_on_an_already_done_project():
    pid = f"cancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            project = Project(id=pid, status="done", user_id=user_id)
            session.add(project)
            session.commit()

        res = client.post(f"/api/projects/{pid}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "done"


def test_cancel_project_404s_for_another_users_project():
    pid = f"cancel-{uuid.uuid4().hex}"
    with TestClient(app) as client_a:
        user_a = login_as(client_a)
        with Session(engine) as session:
            project = Project(id=pid, status="running", user_id=user_a)
            session.add(project)
            session.commit()

    with TestClient(app) as client_b:
        login_as(client_b)
        res = client_b.post(f"/api/projects/{pid}/cancel")
        assert res.status_code == 404

    with Session(engine) as session:
        project = session.get(Project, pid)
        assert project.status == "running"  # unchanged


def test_cancelled_projects_excluded_from_list_projects():
    pid_cancelled = f"cancel-{uuid.uuid4().hex}"
    pid_done = f"cancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            session.add(Project(id=pid_cancelled, status="cancelled", user_id=user_id))
            session.add(Project(id=pid_done, status="done", user_id=user_id))
            session.commit()

        res = client.get("/api/projects")
        assert res.status_code == 200
        ids = {p["project_id"] for p in res.json()}
        assert pid_cancelled not in ids
        assert pid_done in ids


def test_compute_room_dimensions_returns_none_without_both_length_and_width():
    from app.main import _compute_room_dimensions

    assert _compute_room_dimensions(None, None, None, "ft") is None
    assert _compute_room_dimensions(12, None, None, "ft") is None
    assert _compute_room_dimensions(None, 10, None, "ft") is None


def test_compute_room_dimensions_computes_area_in_feet():
    from app.main import _compute_room_dimensions

    result = _compute_room_dimensions(12, 10, None, "ft")
    assert result["area_sqft"] == 120.0
    assert result["wall_area_sqft"] is None
    assert result["unit"] == "ft"


def test_compute_room_dimensions_computes_wall_area_when_height_given():
    from app.main import _compute_room_dimensions

    result = _compute_room_dimensions(12, 10, 9, "ft")
    assert result["area_sqft"] == 120.0
    # 2 * (12 + 10) * 9 = 396
    assert result["wall_area_sqft"] == 396.0


def test_compute_room_dimensions_converts_meters_to_feet():
    from app.main import _compute_room_dimensions

    result = _compute_room_dimensions(4, 3, None, "m")
    # 4m -> 13.12ft, 3m -> 9.84ft, area ~= 129.2 sqft
    assert 125 < result["area_sqft"] < 135
    # Original values preserved as entered, not converted, for display.
    assert result["length"] == 4
    assert result["unit"] == "m"


def test_compute_room_dimensions_rejects_out_of_range_values():
    from app.main import _compute_room_dimensions

    assert _compute_room_dimensions(0, 10, None, "ft") is None
    assert _compute_room_dimensions(-5, 10, None, "ft") is None
    assert _compute_room_dimensions(10000, 10, None, "ft") is None


def test_room_measurements_are_used_instead_of_the_gemini_estimate(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        data = {
            **_REQUIRED_STYLE_FIELDS,
            "city": "Karachi",
            "room_length": "12",
            "room_width": "10",
            "room_height": "9",
            "dimension_unit": "ft",
        }
        create_res = client.post("/api/projects", files=files, data=data)
        assert create_res.status_code == 200
        project_id = create_res.json()["project_id"]

        status_res = client.get(f"/api/projects/{project_id}")
        body = status_res.json()
        assert body["room_dimensions"]["area_sqft"] == 120.0
        assert body["room_dimensions"]["wall_area_sqft"] == 396.0

    # The Gemini vision guess must NOT run when a real measurement was given.
    assert provider.estimate_room_area_calls == 0
    assert all(area == 120.0 for _, _, area, _ in provider.materials_calls)
    assert all(wall_area == 396.0 for _, _, _, wall_area in provider.materials_calls)


def test_no_room_measurements_falls_back_to_gemini_estimate(monkeypatch):
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
        assert status_res.json()["room_dimensions"] is None

    assert provider.estimate_room_area_calls == 1
    assert all(area == 180.0 for _, _, area, _ in provider.materials_calls)
    assert all(wall_area is None for _, _, _, wall_area in provider.materials_calls)


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
