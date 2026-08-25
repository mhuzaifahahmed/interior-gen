import io
import json
import uuid

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

import app.main as main_module
from app.db import engine
from app.main import app
from app.models import HouseProject
from tests.conftest import login_as

# See tests/test_api.py's identical alias - Clerk owns signup/login now.
_signup_and_login = login_as


class FakeProvider:
    def __init__(self):
        self.render_prompts = []

    def analyze_plot(self, image_bytes, dimensions):
        return "A rectangular plot facing north."

    def generate_floor_plan(self, plot_description, dimensions, prompt):
        return None

    def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
        return {"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}]}]}

    def generate_house_render(self, image_bytes, prompt):
        self.render_prompts.append(prompt)
        buf = io.BytesIO()
        Image.new("RGB", (4, 4), color=(200, 200, 200)).save(buf, format="PNG")
        return buf.getvalue()


class FakeStorage:
    """In-memory storage double - see tests/test_api.py's FakeStorage for why
    both get_provider AND get_storage must be mocked (a hard-learned lesson
    from a real incident, documented in CLAUDE.md)."""

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


def test_full_house_upload_and_poll_flow(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        username = _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={"length": "40", "width": "60", "unit": "ft", "prompt": "2 floors, modern style"},
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        assert status_res.status_code == 200
        body = status_res.json()
        assert body["status"] == "done"
        assert body["plot_description"] == "A rectangular plot facing north."
        assert body["floor_plan_status"] == "not_configured"
        assert body["images"]["plot"] is not None
        assert body["images"]["render"] is not None
        assert "render_from_layout" not in body["images"]
        assert "cad_plan_urls" not in body
        assert body["images"]["floor_plan"] is None
        assert body["blueprint_status"] == "done"
        assert len(body["blueprint_urls"]) == 1
        # Real AutoCAD-format (.dxf) export, same floor count as the PNGs -
        # see app/pipeline/blueprint_dxf.py.
        assert len(body["blueprint_dxf_urls"]) == 1

        assert f"users/{username}/buildAHouse/input/" in body["images"]["plot"]
        assert f"users/{username}/buildAHouse/output/" in body["images"]["render"]
        assert f"users/{username}/buildAHouse/output/" in body["blueprint_urls"][0]
        assert f"users/{username}/buildAHouse/output/" in body["blueprint_dxf_urls"][0]
        assert body["blueprint_dxf_urls"][0].endswith(".dxf")


def test_house_api_exposes_one_floor_plan_url_per_floor(monkeypatch):
    # Real vendor path (app/providers/kaggle_autocad.py in production) -
    # generate_floor_plan() returning multiple images must surface as
    # multiple floor_plan_urls, floor-ordered, with images.floor_plan kept
    # as the first one for backward compatibility.
    class MultiFloorPlanProvider(FakeProvider):
        def generate_floor_plan(self, plot_description, dimensions, prompt):
            return [b"floor1-png-bytes", b"floor2-png-bytes"]

    monkeypatch.setattr(main_module, "get_provider", lambda: MultiFloorPlanProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={"length": "40", "width": "60", "unit": "ft", "prompt": "2 floors, modern style"},
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        body = client.get(f"/api/house-projects/{house_project_id}").json()
        assert body["floor_plan_status"] == "done"
        assert len(body["floor_plan_urls"]) == 2
        assert body["floor_plan_urls"][0] != body["floor_plan_urls"][1]
        assert body["images"]["floor_plan"] == body["floor_plan_urls"][0]


def test_compose_house_requirements_combines_all_fields():
    from app.main import _compose_house_requirements

    result = _compose_house_requirements(2, 3, 2, "dirty kitchen each floor, garage")
    assert result == (
        "2 floors, 3 bedrooms, 2 bathrooms. Extras: dirty kitchen each floor, garage"
    )


def test_compose_house_requirements_uses_singular_for_one():
    from app.main import _compose_house_requirements

    result = _compose_house_requirements(1, 1, 1, "")
    assert result == "1 floor, 1 bedroom, 1 bathroom"


def test_compose_house_requirements_returns_none_when_everything_empty():
    from app.main import _compose_house_requirements

    assert _compose_house_requirements(None, None, None, "") is None


def test_compose_house_requirements_extras_only():
    from app.main import _compose_house_requirements

    assert _compose_house_requirements(None, None, None, "modern style") == "modern style"


def test_structured_house_inputs_compose_the_prompt_and_persist(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={
                "length": "40",
                "width": "60",
                "unit": "ft",
                "floor_count": "2",
                "bedrooms": "3",
                "bathrooms": "2",
                "extras": "dirty kitchen each floor, garage",
            },
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        body = status_res.json()
        assert body["prompt"] == (
            "2 floors, 3 bedrooms, 2 bathrooms. Extras: dirty kitchen each floor, garage"
        )
        assert body["house_inputs"] == {
            "floor_count": 2,
            "bedrooms": 3,
            "bathrooms": 2,
            "extras": "dirty kitchen each floor, garage",
        }
        assert body["dimensions"] == {"length": 40.0, "width": 60.0, "unit": "ft"}


def test_house_floor_count_out_of_range_is_clamped(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={"floor_count": "99"},
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        assert status_res.json()["house_inputs"]["floor_count"] == 10


def test_house_input_metadata_json_written_to_storage(monkeypatch):
    # Parity with Room Redesign's input metadata.json (test_api.py's
    # test_input_metadata_json_written_to_storage) - Build a House previously
    # wrote no JSON metadata to S3 at all, only DB columns.
    storage = FakeStorage()
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(app) as client:
        username = _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        data = {
            "length": "40",
            "width": "60",
            "unit": "ft",
            "floor_count": "2",
            "bedrooms": "3",
            "bathrooms": "2",
            "extras": "modern style",
        }
        create_res = client.post("/api/house-projects", files=files, data=data)
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        metadata_key = f"users/{username}/buildAHouse/input/{house_project_id}/metadata.json"
        assert metadata_key in storage.objects
        saved = json.loads(storage.objects[metadata_key])
        assert saved["dimensions"] == {"length": 40.0, "width": 60.0, "unit": "ft"}
        assert saved["house_inputs"] == {
            "floor_count": 2,
            "bedrooms": 3,
            "bathrooms": 2,
            "extras": "modern style",
        }


def test_cancel_house_project_flips_a_running_project_to_cancelled():
    # See test_api.py's identical comment on test_cancel_project_flips_...
    # for why a random id is used - no test-DB isolation in this app.
    hid = f"hcancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            house_project = HouseProject(id=hid, status="running", user_id=user_id)
            session.add(house_project)
            session.commit()

        res = client.post(f"/api/house-projects/{hid}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "cancelled"

        with Session(engine) as session:
            house_project = session.get(HouseProject, hid)
            assert house_project.status == "cancelled"


def test_cancel_house_project_is_a_noop_on_an_already_done_project():
    hid = f"hcancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            house_project = HouseProject(id=hid, status="done", user_id=user_id)
            session.add(house_project)
            session.commit()

        res = client.post(f"/api/house-projects/{hid}/cancel")
        assert res.status_code == 200
        assert res.json()["status"] == "done"


def test_cancel_house_project_404s_for_another_users_project():
    hid = f"hcancel-{uuid.uuid4().hex}"
    with TestClient(app) as client_a:
        user_a = login_as(client_a)
        with Session(engine) as session:
            house_project = HouseProject(id=hid, status="running", user_id=user_a)
            session.add(house_project)
            session.commit()

    with TestClient(app) as client_b:
        login_as(client_b)
        res = client_b.post(f"/api/house-projects/{hid}/cancel")
        assert res.status_code == 404

    with Session(engine) as session:
        house_project = session.get(HouseProject, hid)
        assert house_project.status == "running"


def test_cancelled_house_projects_excluded_from_list_house_projects():
    hid_cancelled = f"hcancel-{uuid.uuid4().hex}"
    hid_done = f"hcancel-{uuid.uuid4().hex}"
    with TestClient(app) as client:
        user_id = login_as(client)
        with Session(engine) as session:
            session.add(HouseProject(id=hid_cancelled, status="cancelled", user_id=user_id))
            session.add(HouseProject(id=hid_done, status="done", user_id=user_id))
            session.commit()

        res = client.get("/api/house-projects")
        assert res.status_code == 200
        ids = {p["house_project_id"] for p in res.json()}
        assert hid_cancelled not in ids
        assert hid_done in ids


def test_house_display_name_appended_to_storage_namespace(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: storage)

    with TestClient(app) as client:
        user_id = _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={"prompt": "2 floors", "display_name": "Jane Doe"},
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        expected_key = f"users/{user_id}_jane_doe/buildAHouse/input/{house_project_id}/plot.png"
        assert expected_key in storage.objects


def test_house_prompt_reaches_the_render_call(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post(
            "/api/house-projects",
            files=files,
            data={"prompt": "2 floors, 3 bedrooms, modern style"},
        )
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        assert status_res.json()["status"] == "done"

    # v5: exactly one exterior render call - the floor plan itself never
    # involves an image-generation call at all.
    assert len(provider.render_prompts) == 1
    assert "2 floors, 3 bedrooms, modern style" in provider.render_prompts[0]


def test_house_project_works_without_dimensions(monkeypatch):
    # length/width are optional - a plot photo alone is still a valid submission.
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post("/api/house-projects", files=files)
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        assert status_res.json()["status"] == "done"


def test_house_project_rejects_unsupported_file_type():
    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("doc.pdf", b"not-an-image", "application/pdf")}
        res = client.post("/api/house-projects", files=files)
        assert res.status_code == 400


def test_create_house_project_requires_login():
    with TestClient(app) as client:
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        res = client.post("/api/house-projects", files=files)
        assert res.status_code == 401


def test_unknown_house_project_returns_404():
    with TestClient(app) as client:
        _signup_and_login(client)
        res = client.get("/api/house-projects/does-not-exist")
        assert res.status_code == 404


def test_cannot_view_another_users_house_project(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client_a:
        _signup_and_login(client_a)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client_a.post("/api/house-projects", files=files)
        house_project_id = create_res.json()["house_project_id"]

    with TestClient(app) as client_b:
        _signup_and_login(client_b)
        res = client_b.get(f"/api/house-projects/{house_project_id}")
        assert res.status_code == 404


def test_list_house_projects_returns_own_newest_first(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        _signup_and_login(client)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        first = client.post("/api/house-projects", files=files).json()["house_project_id"]
        second = client.post("/api/house-projects", files=files).json()["house_project_id"]

        res = client.get("/api/house-projects")
        assert res.status_code == 200
        body = res.json()
        assert [p["house_project_id"] for p in body] == [second, first]
        assert body[0]["created_at"] is not None


def test_list_house_projects_requires_login():
    with TestClient(app) as client:
        res = client.get("/api/house-projects")
        assert res.status_code == 401


def test_list_house_projects_excludes_other_users(monkeypatch):
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client_a:
        _signup_and_login(client_a)
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        client_a.post("/api/house-projects", files=files)

    with TestClient(app) as client_b:
        _signup_and_login(client_b)
        res = client_b.get("/api/house-projects")
        assert res.status_code == 200
        assert res.json() == []
