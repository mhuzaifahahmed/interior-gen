import json

from sqlmodel import Session, SQLModel, create_engine

from app.models import HouseProject
from app.pipeline import generate_house as generate_house_module
from app.pipeline.generate_house import run_house_pipeline


class FakeProvider:
    def __init__(self, floor_plan_images=None):
        self.plot_calls = []
        self.floor_plan_calls = []
        self.render_calls = []
        self.room_layout_calls = []
        self._floor_plan_images = floor_plan_images

    def analyze_plot(self, image_bytes, dimensions):
        self.plot_calls.append((image_bytes, dimensions))
        return "A rectangular plot facing north."

    def generate_floor_plan(self, plot_description, dimensions, prompt, room_layout=None):
        self.floor_plan_calls.append((plot_description, dimensions, prompt, room_layout))
        return self._floor_plan_images

    def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
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

    provider = FakeProvider(floor_plan_images=None)
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
    # v5: exactly ONE generate_house_render call (the exterior photo render) -
    # no second render, no AI-drawn floor-plan call of any kind. The floor
    # plan itself is 100% deterministic (app/pipeline/blueprint_svg.py).
    assert len(provider.render_calls) == 1
    assert "2 floors, modern style" in provider.render_calls[0][1]


def test_run_house_pipeline_stores_floor_plan_when_vendor_available(monkeypatch):
    # Forward-looking: once a real vendor is wired in and returns images, the
    # pipeline should store them and mark floor_plan_status="done" - exercising
    # this path now (with a fake that returns images) confirms that branch
    # works even though no real vendor is configured by default.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h2", status="queued", plot_image_key="h2/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider(floor_plan_images=[b"fake-floor-plan-bytes"])
    run_house_pipeline("h2", provider, storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h2")
        assert house_project.floor_plan_status == "done"
        assert house_project.floor_plan_key == "local.output/h2/floor_plan_floor1.png"
        assert storage.get(house_project.floor_plan_key) == b"fake-floor-plan-bytes"
        assert json.loads(house_project.floor_plan_keys_json) == ["local.output/h2/floor_plan_floor1.png"]


def test_run_house_pipeline_stores_one_floor_plan_image_per_floor(monkeypatch):
    # Real regression guard: a vendor (like kaggle_autocad.py) that generates
    # one image per floor must have EVERY floor stored, not just the first -
    # an earlier version of the single-image contract silently discarded
    # floors 2+ for any multi-floor request.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h9/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h9", status="queued", plot_image_key="h9/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider(floor_plan_images=[b"floor1-bytes", b"floor2-bytes", b"floor3-bytes"])
    run_house_pipeline("h9", provider, storage, {"length": 40, "width": 60, "unit": "ft"}, prompt="3 floors")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h9")
        assert house_project.floor_plan_status == "done"
        keys = json.loads(house_project.floor_plan_keys_json)
        assert keys == [
            "local.output/h9/floor_plan_floor1.png",
            "local.output/h9/floor_plan_floor2.png",
            "local.output/h9/floor_plan_floor3.png",
        ]
        assert storage.get(keys[0]) == b"floor1-bytes"
        assert storage.get(keys[1]) == b"floor2-bytes"
        assert storage.get(keys[2]) == b"floor3-bytes"
        # Legacy single-key field stays populated with the first floor only.
        assert house_project.floor_plan_key == keys[0]


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


def test_run_house_pipeline_stores_render_model_when_provider_supports_it(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h5b/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h5b", status="queued", plot_image_key="h5b/plot.png")
        session.add(house_project)
        session.commit()

    class LabeledProvider(FakeProvider):
        def get_house_render_model_label(self):
            return "our model"

    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h5b", LabeledProvider(), storage, dimensions, prompt="modern style")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h5b")
        assert house_project.render_model == "our model"
        meta = json.loads(house_project.meta_json)
        assert meta["render_model"] == "our model"


def test_run_house_pipeline_render_model_stays_none_when_provider_does_not_track_it(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h5c/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h5c", status="queued", plot_image_key="h5c/plot.png")
        session.add(house_project)
        session.commit()

    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h5c", FakeProvider(), storage, dimensions, prompt="modern style")

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h5c")
        assert house_project.render_model is None


def test_run_house_pipeline_stops_when_cancelled_before_layout_stage(monkeypatch):
    # Simulates a real cancel request (app/main.py's cancel_house_project)
    # landing via a SEPARATE session while the pipeline is mid-run. No
    # worker threads in this pipeline (unlike room redesign's per-tier
    # executor), so a plain make_test_engine() (no StaticPool needed) works.
    # v7 reorder: the layout/blueprint stage now runs BEFORE the floor-plan
    # (AI Concept Layout) stage, so this checkpoint - the first of two - sits
    # right after analyze_plot, before either of them starts.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hcancel1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hcancel1", status="queued", plot_image_key="hcancel1/plot.png")
        session.add(house_project)
        session.commit()

    class CancellingProvider(FakeProvider):
        def analyze_plot(self, image_bytes, dimensions):
            with Session(engine) as cancel_session:
                hp = cancel_session.get(HouseProject, "hcancel1")
                hp.status = "cancelled"
                cancel_session.add(hp)
                cancel_session.commit()
            return super().analyze_plot(image_bytes, dimensions)

    provider = CancellingProvider()
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("hcancel1", provider, storage, dimensions, prompt="modern style")

    assert provider.room_layout_calls == []
    assert provider.floor_plan_calls == []
    assert provider.render_calls == []

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hcancel1")
        assert house_project.status == "cancelled"
        assert house_project.render_key is None


def test_run_house_pipeline_stops_when_cancelled_before_render(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hcancel2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hcancel2", status="queued", plot_image_key="hcancel2/plot.png")
        session.add(house_project)
        session.commit()

    class CancellingProvider(FakeProvider):
        def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
            with Session(engine) as cancel_session:
                hp = cancel_session.get(HouseProject, "hcancel2")
                hp.status = "cancelled"
                cancel_session.add(hp)
                cancel_session.commit()
            return super().generate_room_layout(dimensions, prompt, plot_description, floor_count)

    provider = CancellingProvider()
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("hcancel2", provider, storage, dimensions, prompt="modern style")

    assert provider.render_calls == []

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hcancel2")
        assert house_project.status == "cancelled"
        assert house_project.render_key is None
        # The blueprint step itself DID complete before the checkpoint caught
        # the cancellation - its result is just never used for a render.
        assert house_project.blueprint_status == "done"


def test_run_house_pipeline_generates_blueprint_per_floor(monkeypatch):
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
        # Real AutoCAD-format (.dxf) export of the same floor - see
        # app/pipeline/blueprint_dxf.py.
        blueprint_dxf_keys = json.loads(house_project.blueprint_dxf_keys_json)
        assert blueprint_dxf_keys == ["local.output/h6/blueprint_floor1.dxf"]

    assert len(provider.room_layout_calls) == 1
    # The exterior render is always edited from the real plot photo - the
    # blueprint is never used as an image-edit reference for anything.
    assert provider.render_calls[0][0] == b"plot-bytes"
    blueprint_bytes = storage.get("local.output/h6/blueprint_floor1.png")
    assert blueprint_bytes.startswith(b"\x89PNG")
    dxf_bytes = storage.get("local.output/h6/blueprint_floor1.dxf")
    assert dxf_bytes.startswith(b"  0\nSECTION")


def test_run_house_pipeline_passes_room_layout_into_generate_floor_plan(monkeypatch):
    # v7 reorder / concept-layout-controlnet-conditioning.md Phase 1: the
    # real room_layout computed by the blueprint stage must be threaded into
    # generate_floor_plan() so a vendor (kaggle_autocad.py) can build
    # ControlNet conditioning images from our real geometry - AND the
    # blueprint stage must run first (room_layout_calls before floor_plan_calls).
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h6b/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h6b", status="queued", plot_image_key="h6b/plot.png")
        session.add(house_project)
        session.commit()

    call_order = []

    class OrderTrackingProvider(FakeProvider):
        def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
            call_order.append("room_layout")
            return super().generate_room_layout(dimensions, prompt, plot_description, floor_count)

        def generate_floor_plan(self, plot_description, dimensions, prompt, room_layout=None):
            call_order.append("floor_plan")
            return super().generate_floor_plan(plot_description, dimensions, prompt, room_layout)

    provider = OrderTrackingProvider()
    dimensions = {"length": 40, "width": 60, "unit": "ft"}
    run_house_pipeline("h6b", provider, storage, dimensions, prompt="modern style")

    assert call_order == ["room_layout", "floor_plan"]
    room_layout_arg = provider.floor_plan_calls[0][3]
    assert room_layout_arg is not None
    assert room_layout_arg["floors"][0]["rooms"][0]["name"] == "Living Room"


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
        def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
            raise RuntimeError("Gemini quota exceeded")

    provider = FailingRoomLayoutProvider()
    run_house_pipeline("h7", provider, storage, {"length": 40, "width": 60, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h7")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "failed"
        assert house_project.room_layout_json is None
        assert house_project.blueprint_keys_json is None
        assert house_project.blueprint_dxf_keys_json is None

    assert provider.render_calls[0][0] == b"plot-bytes"


def test_run_house_pipeline_skips_render_when_house_render_enabled_is_false(monkeypatch):
    # Dev-only escape hatch (app/config.py's house_render_enabled) - lets the
    # blueprint/DXF/AutoCAD-concept work be checked end-to-end without ever
    # calling generate_house_render() (no Kaggle/OpenAI call, no risk of a
    # failed project from a dead elevation-model tunnel or a stray charge).
    from app.config import settings

    monkeypatch.setattr(settings, "house_render_enabled", False)

    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["h8/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="h8", status="queued", plot_image_key="h8/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline("h8", provider, storage, {"length": 40, "width": 60, "unit": "ft"}, prompt="1 floor")

    assert provider.render_calls == []

    with Session(engine) as session:
        house_project = session.get(HouseProject, "h8")
        assert house_project.status == "done"
        assert house_project.render_key is None
        assert house_project.render_model is None
        # The rest of the pipeline still ran normally.
        assert house_project.blueprint_status == "done"
        assert json.loads(house_project.blueprint_keys_json)


class ManyRoomsProvider(FakeProvider):
    """Returns a room program too large for a tiny plot - used to exercise
    the feasibility hard gate (2026-08-27)."""

    def generate_room_layout(self, dimensions, prompt, plot_description=None, floor_count=None):
        rooms = [
            {"name": "Bedroom 1", "area": 1},
            {"name": "Bedroom 2", "area": 1},
            {"name": "Bedroom 3", "area": 1},
            {"name": "Bathroom 1", "area": 1},
            {"name": "Bathroom 2", "area": 1},
            {"name": "Kitchen", "area": 1},
            {"name": "Living Room", "area": 1},
            {"name": "Garage", "area": 1},
        ]
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {"floors": [{"floor_number": 1, "rooms": rooms}]}


def test_run_house_pipeline_hard_gates_an_infeasible_room_program(monkeypatch):
    # A room program that cannot physically fit a tiny plot must never be
    # silently laid out/rendered - explicit user decision, 2026-08-27.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hfeas1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hfeas1", status="queued", plot_image_key="hfeas1/plot.png")
        session.add(house_project)
        session.commit()

    provider = ManyRoomsProvider()
    run_house_pipeline("hfeas1", provider, storage, {"length": 15, "width": 15, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hfeas1")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "infeasible"
        assert house_project.floor_plan_status == "infeasible"
        assert house_project.blueprint_keys_json is None
        feasibility = json.loads(house_project.feasibility_json)
        assert feasibility["verdict"] == "not_feasible"
        assert feasibility["explanation"]
        meta = json.loads(house_project.meta_json)
        assert meta["feasibility_verdict"] == "not_feasible"

    # The AI Concept Layout stage must also be skipped entirely - no
    # conditioning geometry exists for an infeasible layout.
    assert provider.floor_plan_calls == []
    # The exterior render is still best-effort-allowed to run (a photoreal
    # visualization of the plot itself isn't misleading the same way a
    # blueprint of rooms that don't fit would be).
    assert len(provider.render_calls) == 1


def test_run_house_pipeline_injects_a_garage_room_when_requested_but_missing(monkeypatch):
    # FakeProvider's default room_layout has no garage - mentioning "garage"
    # in the requirements text must guarantee one exists on the ground floor
    # rather than relying on Gemini to have included it.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hgarage1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hgarage1", status="queued", plot_image_key="hgarage1/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "hgarage1", provider, storage, {"length": 60, "width": 80, "unit": "ft"}, prompt="Extras: 2 car garage"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hgarage1")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Garage" in room_names


def test_run_house_pipeline_reserves_front_yard_before_layout(monkeypatch):
    # A requested front yard must reduce the actual building footprint
    # passed to layout_floor()/the blueprint renderers - reserved BEFORE any
    # room is placed, not squeezed into leftover space afterward.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hyard1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hyard1", status="queued", plot_image_key="hyard1/plot.png")
        session.add(house_project)
        session.commit()

    captured_dimensions = []
    real_layout_floor = generate_house_module.layout_floor

    def spy_layout_floor(rooms, dimensions, garage_cars=None):
        captured_dimensions.append(dict(dimensions))
        return real_layout_floor(rooms, dimensions, garage_cars)

    monkeypatch.setattr(generate_house_module, "layout_floor", spy_layout_floor)

    provider = FakeProvider()
    run_house_pipeline(
        "hyard1", provider, storage, {"length": 60, "width": 80, "unit": "ft"}, prompt="Extras: 20ft front yard"
    )

    assert len(captured_dimensions) == 1
    # Width (the axis floor_layout.py treats as the plot's "depth") must be
    # reduced by the requested 20ft - the length is untouched.
    assert captured_dimensions[0]["length"] == 60
    assert captured_dimensions[0]["width"] == 60

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hyard1")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "done"
