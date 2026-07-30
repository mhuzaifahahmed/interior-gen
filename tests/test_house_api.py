import io

from fastapi.testclient import TestClient
from PIL import Image

import app.main as main_module
from app.main import app


class FakeProvider:
    def __init__(self):
        self.render_prompts = []

    def analyze_plot(self, image_bytes, dimensions):
        return "A rectangular plot facing north."

    def generate_floor_plan(self, plot_description, dimensions, prompt):
        return None

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
        assert body["images"]["floor_plan"] is None

        assert "local.input/" in body["images"]["plot"]
        assert "local.output/" in body["images"]["render"]


def test_house_prompt_reaches_the_render_call(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(main_module, "get_provider", lambda: provider)
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
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

    assert len(provider.render_prompts) == 1
    assert "2 floors, 3 bedrooms, modern style" in provider.render_prompts[0]


def test_house_project_works_without_dimensions(monkeypatch):
    # length/width are optional - a plot photo alone is still a valid submission.
    monkeypatch.setattr(main_module, "get_provider", lambda: FakeProvider())
    monkeypatch.setattr(main_module, "get_storage", lambda: FakeStorage())

    with TestClient(app) as client:
        files = {"file": ("plot.png", _sample_image_bytes(), "image/png")}
        create_res = client.post("/api/house-projects", files=files)
        assert create_res.status_code == 200
        house_project_id = create_res.json()["house_project_id"]

        status_res = client.get(f"/api/house-projects/{house_project_id}")
        assert status_res.json()["status"] == "done"


def test_house_project_rejects_unsupported_file_type():
    with TestClient(app) as client:
        files = {"file": ("doc.pdf", b"not-an-image", "application/pdf")}
        res = client.post("/api/house-projects", files=files)
        assert res.status_code == 400


def test_unknown_house_project_returns_404():
    with TestClient(app) as client:
        res = client.get("/api/house-projects/does-not-exist")
        assert res.status_code == 404
