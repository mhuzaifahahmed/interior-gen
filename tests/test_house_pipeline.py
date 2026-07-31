import json

from sqlmodel import Session, SQLModel, create_engine

from app.models import HouseProject
from app.pipeline import generate_house as generate_house_module
from app.pipeline.generate_house import run_house_pipeline


class FakeProvider:
    def __init__(self, floor_plan_bytes=None):
        self.plot_calls = []
        self.floor_plan_calls = []
        self.render_calls = []
        self.room_layout_calls = []
        self._floor_plan_bytes = floor_plan_bytes

    def analyze_plot(self, image_bytes, dimensions):
        self.plot_calls.append((image_bytes, dimensions))
        return "A rectangular plot facing north."

    def generate_floor_plan(self, plot_description, dimensions, prompt):
        self.floor_plan_calls.append((plot_description, dimensions, prompt))
        return self._floor_plan_bytes

    def generate_room_layout(self, dimensions, prompt, plot_description=None):
        self.room_layout_calls.append((dimensions, prompt, plot_description))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}],
                }
            ]
        }

    def generate_house_render(self, image_bytes, prompt):
        self.render_calls.append((image_bytes, prompt))
        return b"fake-render-bytes"


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type="image/png"):
        self.objects[key] = data

    def get(self, key):
        return self.objects[key]

    def url(self, key):
        return f"/media/{key}"


def make_test_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


def test_run_house_pipeline_success_without_floor_plan_vendor(monkeypatch):
    # The expected path today - no floor-plan vendor configured, so
    # generate_floor_plan returns None and the pipeline must still succeed.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h1", status="queued", plot_image_key="h1/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider(floor_plan_bytes=None)
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h1", provider, storage, dimensions, prompt="2 floors, modern style")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h1")
        assert house_project.status == "done"
        assert house_project.plot_description == "A rectangular plot facing north."
        assert house_project.floor_plan_status == "not_configured"
        assert house_project.floor_plan_key is None
        assert house_project.render_key == "local.output/h1/render.png"
        assert storage.get(house_project.render_key) == b"fake-render-bytes"

    assert len(provider.plot_calls) == 1
    assert len(provider.floor_plan_calls) == 1
    assert len(provider.render_calls) == 1
    assert "2 floors, modern style" in provider.render_calls[0][1]


def test_run_house_pipeline_stores_floor_plan_when_vendor_available(monkeypatch):
    # Forward-looking: once a real vendor is wired in and returns bytes, the
    # pipeline should store it and mark floor_plan_status="done" - exercising
    # this path now (with a fake that returns bytes) confirms that branch works
    # even though no real vendor is configured yet.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h2", status="queued", plot_image_key="h2/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider(floor_plan_bytes=b"fake-floor-plan-bytes")
    run_house_pipeline("h2", provider, storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h2")
        assert house_project.floor_plan_status == "done"
        assert house_project.floor_plan_key == "local.output/h2/floor_plan.png"
        assert storage.get(house_project.floor_plan_key) == b"fake-floor-plan-bytes"


def test_run_house_pipeline_marks_failed_on_render_error(monkeypatch):
    # generate_house_render is NOT best-effort - a failure there must fail the
    # whole house-project, mirroring the room-redesign image loop's treatment.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h3/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h3", status="queued", plot_image_key="h3/plot.png")
        session.add(house_project)
        session.commit()

    class FailingProvider(FakeProvider):
        def generate_house_render(self, image_bytes, prompt):
            raise RuntimeError("OpenAI quota exceeded")

    run_house_pipeline("h3", FailingProvider(), storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h3")
        assert house_project.status == "failed"
        assert "OpenAI quota exceeded" in house_project.error


def test_run_house_pipeline_degrades_gracefully_when_analyze_plot_fails(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h4/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h4", status="queued", plot_image_key="h4/plot.png")
        session.add(house_project)
        session.commit()

    class NoAnalysisProvider(FakeProvider):
        def analyze_plot(self, image_bytes, dimensions):
            raise RuntimeError("Gemini unavailable")

    run_house_pipeline("h4", NoAnalysisProvider(), storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h4")
        # Still succeeds overall - analyze_plot is best-effort, same contract
        # as describe_room in the room-redesign pipeline.
        assert house_project.status == "done"
        assert house_project.plot_description is None
        assert house_project.render_key is not None


def test_run_house_pipeline_stores_meta_json(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h5/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h5", status="queued", plot_image_key="h5/plot.png")
        session.add(house_project)
        session.commit()

    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h5", FakeProvider(), storage, dimensions, prompt="modern style")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h5")
        meta = json.loads(house_project.meta_json)
        assert meta["dimensions"] == dimensions
        assert meta["prompt"] == "modern style"
        assert meta["floor_plan_generated"] is False
        assert meta["blueprint_generated"] is True
        assert meta["floor_count"] == 1


def test_run_house_pipeline_generates_blueprint_and_uses_it_as_render_reference(monkeypatch):
    # Confirms the confirmed design: the render step's image input is the
    # GROUND FLOOR's drawn blueprint, not the raw plot photo, whenever
    # blueprint generation succeeds.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h6/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h6", status="queued", plot_image_key="h6/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h6", provider, storage, dimensions, prompt="2 floors, modern style")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h6")
        assert house_project.blueprint_status == "done"
        blueprint_keys = json.loads(house_project.blueprint_keys_json)
        assert blueprint_keys == ["local.output/h6/blueprint_floor1.png"]
        assert house_project.room_layout_json is not None

    assert len(provider.room_layout_calls) == 1
    render_input_bytes = provider.render_calls[0][0]
    assert render_input_bytes == storage.get("local.output/h6/blueprint_floor1.png")
    assert render_input_bytes != b"plot-bytes"
    # The blueprint is a real drawn PNG, not just an arbitrary byte string.
    assert render_input_bytes.startswith(b"\x89PNG")


def test_run_house_pipeline_falls_back_to_plot_photo_when_blueprint_generation_fails(monkeypatch):
    # Blueprint generation is best-effort - a bug/failure there must not take
    # down the render step (the core paid deliverable); it should just fall
    # back to editing the raw plot photo like the original behavior.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h7/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h7", status="queued", plot_image_key="h7/plot.png")
        session.add(house_project)
        session.commit()

    class FailingRoomLayoutProvider(FakeProvider):
        def generate_room_layout(self, dimensions, prompt, plot_description=None):
            raise RuntimeError("Gemini quota exceeded")

    provider = FailingRoomLayoutProvider()
    run_house_pipeline("h7", provider, storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h7")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "failed"
        assert house_project.room_layout_json is None
        assert house_project.blueprint_keys_json is None

    assert provider.render_calls[0][0] == b"plot-bytes"
