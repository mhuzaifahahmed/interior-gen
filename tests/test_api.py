import io
import json
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session
from svix.webhooks import Webhook

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
        # Chunk 3 (subscription model-choice routing) - records whichever
        # preferred_backend value (if any) each generate_image() call
        # actually received, so a test can assert on real end-to-end
        # routing, not just the HTTP response code.
        self.received_preferred_backends = []

    def describe_room(self, image_bytes: bytes) -> str:
        return "A small rectangular room with one window."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {}

    def generate_image(
        self, image_bytes: bytes, prompt: str, tier: str | None = None, preferred_backend: str | None = None
    ) -> bytes:
        self.image_prompts.append(prompt)
        self.received_preferred_backends.append(preferred_backend)
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

        # No city was submitted - materials/pricing still runs (2026-09: city no
        # longer gates this, see CLAUDE.md/app/pipeline/generate.py).
        assert body["materials_status"] == "done"
        assert body["materials"] is not None


def test_anonymous_user_cannot_generate_without_logging_in(monkeypatch):
    # A pre-login trial (1 free anonymous generation per browser) used to
    # exist here - removed at the user's explicit request ("no generation
    # before logging in"). No provider/storage mocking needed: the request
    # must 401 before the pipeline is ever reached.
    with TestClient(app) as client:
        # No login at all - authFetch() sends no Authorization header when
        # logged out (see static/app.js), so a bare TestClient call already
        # matches that.
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert res.status_code == 401


def test_logged_in_user_can_still_generate(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        anon = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert anon.status_code == 401

        _signup_and_login(client)
        logged_in = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert logged_in.status_code == 200  # goes through the Free-plan quota path


def test_anonymous_caller_cannot_see_someone_elses_real_project(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as owner_client:
        _signup_and_login(owner_client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        owned = owner_client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        project_id = owned.json()["project_id"]

    with TestClient(app) as anon_client:
        res = anon_client.get(f"/api/projects/{project_id}")
        assert res.status_code == 404


def _set_plan(user_id: str, plan: str) -> None:
    """Chunk 5 (dev-only admin plan endpoint) doesn't exist yet - tests set a
    user's plan directly via the real app DB (same engine TestClient's
    requests use), same "no isolation, real configured DATABASE_URL"
    convention documented in CLAUDE.md for this test suite."""
    from app.plans import get_or_create_user_plan

    with Session(engine) as session:
        plan_row = get_or_create_user_plan(session, user_id)
        plan_row.plan = plan
        session.add(plan_row)
        session.commit()


def test_free_plan_room_preferred_model_is_ignored_not_honored(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        data = {**_REQUIRED_STYLE_FIELDS, "preferred_model": "openai"}
        res = client.post("/api/projects", files=files, data=data)

    # If the Free user's explicit "openai" choice had been honored, this
    # would 403 immediately (Free's OpenAI allowance is 0, per PLAN_QUOTAS) -
    # it succeeds instead, proving the choice was ignored and the request
    # ran against the Kaggle bucket like any other Free-tier generation.
    assert res.status_code == 200


def test_pro_plan_room_can_choose_openai_backend(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        _set_plan(user_id, "pro")
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        data = {**_REQUIRED_STYLE_FIELDS, "preferred_model": "openai"}
        res = client.post("/api/projects", files=files, data=data)

    assert res.status_code == 200
    assert provider.received_preferred_backends == ["openai", "openai", "openai"]


def test_pro_plan_room_with_no_preference_uses_the_configured_default(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        _set_plan(user_id, "pro")
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)

    assert res.status_code == 200
    # No preferred_model sent - the provider must receive no override at all
    # (None), same call shape as before this feature existed.
    assert provider.received_preferred_backends == [None, None, None]


def test_free_plan_room_quota_is_enforced(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}

        for _ in range(5):
            res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
            assert res.status_code == 200

        sixth = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert sixth.status_code == 403
        assert "Upgrade" in sixth.json()["detail"]


def test_get_plan_reports_free_plan_and_usage(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)

        before = client.get("/api/plan")
        assert before.status_code == 200
        body = before.json()
        assert body["plan"] == "free"
        assert body["used"]["room_kaggle"] == 0
        assert body["remaining"]["room_kaggle"] == 5
        assert body["remaining"]["room_openai"] == 0

        files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
        client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)

        after = client.get("/api/plan").json()
        assert after["used"]["room_kaggle"] == 1
        assert after["remaining"]["room_kaggle"] == 4


def test_get_plan_requires_login():
    with TestClient(app) as client:
        res = client.get("/api/plan")
    assert res.status_code == 401


def test_room_model_status_not_configured_when_image_provider_is_openai(monkeypatch):
    monkeypatch.setattr(main_module.settings, "image_provider", "openai")
    with TestClient(app) as client:
        res = client.get("/api/room-model-status")
    assert res.status_code == 200
    assert res.json() == {"configured": False, "connected": False}


def test_room_model_status_reports_connected(monkeypatch):
    import app.providers.kaggle as kaggle_module

    monkeypatch.setattr(main_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(kaggle_module, "is_room_kaggle_connected", lambda: True)
    with TestClient(app) as client:
        res = client.get("/api/room-model-status")
    assert res.status_code == 200
    assert res.json() == {"configured": True, "connected": True}


def test_room_model_status_reports_not_connected(monkeypatch):
    import app.providers.kaggle as kaggle_module

    monkeypatch.setattr(main_module.settings, "image_provider", "kaggle")
    monkeypatch.setattr(kaggle_module, "is_room_kaggle_connected", lambda: False)
    with TestClient(app) as client:
        res = client.get("/api/room-model-status")
    assert res.status_code == 200
    assert res.json() == {"configured": True, "connected": False}


def test_room_model_status_requires_no_login():
    with TestClient(app) as client:
        res = client.get("/api/room-model-status")
    assert res.status_code == 200


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


def _create_done_project(client, monkeypatch, provider=None) -> str:
    provider = provider or FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    files = {"file": ("room.png", _sample_image_bytes(), "image/png")}
    create_res = client.post(
        "/api/projects", files=files, data={**_REQUIRED_STYLE_FIELDS, "city": "Karachi"}
    )
    assert create_res.status_code == 200
    return create_res.json()["project_id"]


def test_materials_retry_updates_only_the_requested_tier(monkeypatch):
    from sqlmodel import Session

    from app.db import engine
    from app.plans import set_plan

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        project_id = _create_done_project(client, monkeypatch)

        with Session(engine) as session:
            set_plan(session, user_id, "pro")

        retry_provider = FakeProvider()

        def different_materials(tier, tier_spec, room_description, city, api_key=None, room_area_sqft=None, wall_area_sqft=None):
            return {
                "items": [{"name": "Retried Item", "spec": "", "price": "$999", "currency": "USD",
                           "source_url": None, "is_estimate": False}],
                "total": "$999",
                "currency": "USD",
            }

        retry_provider.generate_materials = different_materials
        monkeypatch.setattr(main_module, "get_provider", lambda: retry_provider)

        res = client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "mid"})
        assert res.status_code == 200
        body = res.json()
        assert body["materials"]["mid"]["items"][0]["name"] == "Retried Item"
        # Untouched tiers keep their original materials from generation.
        assert body["materials"]["economical"]["items"][0]["name"] == "Flooring"
        assert body["materials_retry_used"] == 1
        assert body["materials_retry_limit"] == 2


def test_materials_retry_rejected_on_free_plan(monkeypatch):
    with TestClient(app) as client:
        _signup_and_login(client)
        project_id = _create_done_project(client, monkeypatch)

        res = client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "mid"})
    assert res.status_code == 403


def test_materials_retry_rejects_an_invalid_tier(monkeypatch):
    from sqlmodel import Session

    from app.db import engine
    from app.plans import set_plan

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        project_id = _create_done_project(client, monkeypatch)
        with Session(engine) as session:
            set_plan(session, user_id, "pro")

        res = client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "luxury"})
    assert res.status_code == 400


def test_materials_retry_enforces_the_plan_limit(monkeypatch):
    from sqlmodel import Session

    from app.db import engine
    from app.plans import set_plan

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        project_id = _create_done_project(client, monkeypatch)
        with Session(engine) as session:
            set_plan(session, user_id, "pro")  # 2 retries/generation

        monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())

        for _ in range(2):
            res = client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "mid"})
            assert res.status_code == 200

        third = client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "mid"})
    assert third.status_code == 403


def test_materials_retry_404s_for_someone_elses_project(monkeypatch):
    with TestClient(app) as client:
        _signup_and_login(client)
        project_id = _create_done_project(client, monkeypatch)

    with TestClient(app) as other_client:
        _signup_and_login(other_client)
        res = other_client.post(f"/api/projects/{project_id}/materials/retry", data={"tier": "mid"})
    assert res.status_code == 404


def test_rejects_unsupported_file_type():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("doc.pdf", b"not-an-image", "application/pdf")}
        res = client.post("/api/projects", files=files, data=_REQUIRED_STYLE_FIELDS)
        assert res.status_code == 400


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
        # Real bug regression guard (2026-09-04): SQLite drops tzinfo on
        # round-trip, so a naive .isoformat() call produced an ambiguous
        # string the frontend misread as local time instead of UTC. The
        # serialized value must always carry an explicit UTC offset.
        assert datetime.fromisoformat(body[0]["created_at"]).tzinfo is not None


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


def test_contact_page_serves():
    with TestClient(app) as client:
        res = client.get("/contact")
        assert res.status_code == 200
        assert "Contact" in res.text


def test_unknown_page_serves_styled_404():
    with TestClient(app) as client:
        res = client.get("/this-page-does-not-exist")
        assert res.status_code == 404
        assert "text/html" in res.headers["content-type"]
        assert "Page not found" in res.text


def test_unknown_api_route_still_returns_json_404():
    # /api/... callers do fetch(...).ok/status checks and may json()-parse
    # the body - they must never get the styled HTML 404 page.
    with TestClient(app) as client:
        res = client.get("/api/this-does-not-exist")
        assert res.status_code == 404
        assert res.headers["content-type"].startswith("application/json")


def test_admin_page_serves():
    with TestClient(app) as client:
        res = client.get("/admin")
        assert res.status_code == 200
        assert "Admin" in res.text


def test_get_plan_captures_email_and_name_from_query_params(monkeypatch):
    with TestClient(app) as client:
        user_id = login_as(client)
        res = client.get("/api/plan", params={"email": "jane@example.com", "name": "Jane Doe"})
        assert res.status_code == 200

    from app.db import engine as db_engine
    from app.models import UserPlan

    with Session(db_engine) as session:
        row = session.get(UserPlan, user_id)
        assert row.email == "jane@example.com"
        assert row.display_name == "Jane Doe"


def test_admin_endpoints_reject_a_non_admin_user():
    with TestClient(app) as client:
        login_as(client)
        res = client.get("/api/admin/users")
        assert res.status_code == 403


def test_admin_endpoints_reject_an_unauthenticated_caller():
    with TestClient(app) as client:
        res = client.get("/api/admin/users")
        assert res.status_code == 401


def test_admin_list_users_includes_an_allowlisted_admin(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        admin_id = login_as(client)
        monkeypatch.setattr(main_module.settings, "admin_user_ids", admin_id)
        # Give this admin a real, findable row first (mirrors a normal page
        # load hitting GET /api/plan before ever reaching /admin).
        client.get("/api/plan", params={"email": "admin@example.com"})

        res = client.get("/api/admin/users")
        assert res.status_code == 200
        emails = [u["email"] for u in res.json()["users"]]
        assert "admin@example.com" in emails


def test_admin_set_plan_actually_changes_the_users_plan(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        admin_id = login_as(client)
        monkeypatch.setattr(main_module.settings, "admin_user_ids", admin_id)
        client.get("/api/plan")  # ensure the admin's own row exists

        target_user = f"user_{uuid.uuid4().hex[:24]}"
        res = client.post(f"/api/admin/users/{target_user}/plan", json={"plan": "pro"})
        assert res.status_code == 200
        assert res.json()["plan"] == "pro"

    # Confirm it actually persisted, not just echoed back.
    from app.db import engine as db_engine
    from app.models import UserPlan

    with Session(db_engine) as session:
        row = session.get(UserPlan, target_user)
        assert row.plan == "pro"


def test_admin_set_plan_rejects_an_invalid_plan_string(monkeypatch):
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        admin_id = login_as(client)
        monkeypatch.setattr(main_module.settings, "admin_user_ids", admin_id)
        client.get("/api/plan")

        res = client.post(f"/api/admin/users/{admin_id}/plan", json={"plan": "enterprise"})
        assert res.status_code == 400


def test_admin_reset_usage_zeroes_a_users_counters(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())
    with TestClient(app) as client:
        user_id = login_as(client)
        image_bytes = _sample_image_bytes()
        client.post(
            "/api/projects",
            files={"file": ("room.png", image_bytes, "image/png")},
            data=_REQUIRED_STYLE_FIELDS,
        )

        admin_id = login_as(client, f"admin_{uuid.uuid4().hex[:24]}")
        monkeypatch.setattr(main_module.settings, "admin_user_ids", admin_id)

        res = client.post(f"/api/admin/users/{user_id}/reset-usage")
        assert res.status_code == 200
        assert res.json()["used"]["room_kaggle"] == 0
        # lifetime_generations is never zeroed by a reset.
        assert res.json()["lifetime_generations"] == 1


def _sign_clerk_webhook(secret: str, body: bytes) -> dict:
    """Builds real, valid Svix headers for a test webhook delivery - Clerk
    webhooks are signed via Svix, and there's no way to construct a
    passing signature without the library itself. Mirrors exactly what
    app/main.py's clerk_webhook() expects to receive."""
    wh = Webhook(secret)
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    ts = datetime.now(timezone.utc)
    signature = wh.sign(msg_id, ts, body.decode())
    return {
        "svix-id": msg_id,
        "svix-timestamp": str(int(ts.timestamp())),
        "svix-signature": signature,
    }


_TEST_WEBHOOK_SECRET = "whsec_dGVzdHNlY3JldGtleWZvcnRlc3Rpbmc="


def _clerk_user_created_payload(user_id: str, email: str, first_name: str, last_name: str) -> bytes:
    return json.dumps(
        {
            "type": "user.created",
            "data": {
                "id": user_id,
                "primary_email_address_id": "idn_primary",
                "email_addresses": [{"id": "idn_primary", "email_address": email}],
                "first_name": first_name,
                "last_name": last_name,
            },
        }
    ).encode()


def test_clerk_webhook_creates_userplan_row_on_user_created(monkeypatch):
    monkeypatch.setattr(main_module.settings, "clerk_webhook_secret", _TEST_WEBHOOK_SECRET)
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    body = _clerk_user_created_payload(user_id, "newsignup@example.com", "New", "Signup")
    headers = _sign_clerk_webhook(_TEST_WEBHOOK_SECRET, body)

    with TestClient(app) as client:
        res = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

    from app.db import engine as db_engine
    from app.models import UserPlan

    with Session(db_engine) as session:
        row = session.get(UserPlan, user_id)
        assert row is not None
        assert row.email == "newsignup@example.com"
        assert row.display_name == "New Signup"
        assert row.plan == "free"


def test_clerk_webhook_rejects_an_invalid_signature(monkeypatch):
    monkeypatch.setattr(main_module.settings, "clerk_webhook_secret", _TEST_WEBHOOK_SECRET)
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    body = _clerk_user_created_payload(user_id, "forged@example.com", "Forged", "User")
    # Sign with the WRONG secret - simulates a forged/tampered delivery.
    headers = _sign_clerk_webhook("whsec_d29ybGRzIGFwYXJ0LXNlY3JldA==", body)

    with TestClient(app) as client:
        res = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert res.status_code == 400

    from app.db import engine as db_engine
    from app.models import UserPlan

    with Session(db_engine) as session:
        assert session.get(UserPlan, user_id) is None


def test_clerk_webhook_ignores_non_user_created_events(monkeypatch):
    monkeypatch.setattr(main_module.settings, "clerk_webhook_secret", _TEST_WEBHOOK_SECRET)
    user_id = f"user_{uuid.uuid4().hex[:24]}"
    body = json.dumps({"type": "session.created", "data": {"id": user_id}}).encode()
    headers = _sign_clerk_webhook(_TEST_WEBHOOK_SECRET, body)

    with TestClient(app) as client:
        res = client.post("/api/webhooks/clerk", content=body, headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "ignored"

    from app.db import engine as db_engine
    from app.models import UserPlan

    with Session(db_engine) as session:
        assert session.get(UserPlan, user_id) is None


def test_clerk_webhook_400_when_not_configured(monkeypatch):
    monkeypatch.setattr(main_module.settings, "clerk_webhook_secret", "")
    body = _clerk_user_created_payload(f"user_{uuid.uuid4().hex[:24]}", "x@example.com", "X", "Y")

    with TestClient(app) as client:
        res = client.post("/api/webhooks/clerk", content=body, headers={})
        assert res.status_code == 400
