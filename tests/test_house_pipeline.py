import json
import math
import tempfile
import time
import uuid
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.models import HouseProject
from app.pipeline import generate_house as generate_house_module
from app.pipeline.generate_house import run_house_pipeline


class FakeProvider:
    def __init__(self, floor_plan_images=None):
        self.plot_calls = []
        self.floor_plan_calls = []
        self.render_calls = []
        self.render_colors = []
        self.render_styles = []
        self.room_layout_calls = []
        self._floor_plan_images = floor_plan_images

    def analyze_plot(self, image_bytes, dimensions):
        self.plot_calls.append((image_bytes, dimensions))
        return "A rectangular plot facing north."

    def generate_floor_plan(self, plot_description, dimensions, prompt, room_layout=None, facing=None):
        self.floor_plan_calls.append((plot_description, dimensions, prompt, room_layout))
        return self._floor_plan_images

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [{"name": "Living Room", "area": 2}, {"name": "Bedroom", "area": 1}],
                }
            ]
        }

    def generate_house_render(self, image_bytes, prompt, floor_count=None, wants_garage=None, color=None, style=None):
        self.render_calls.append((image_bytes, prompt, floor_count))
        self.render_colors.append(color)
        self.render_styles.append(style)
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
    # A real temp FILE database, not sqlite:// (:memory:) - required now that
    # _run_floor_plan_stage (app/pipeline/generate_house.py, v11) opens its
    # own Session(engine) on a detached daemon thread for every non-
    # infeasible house project, genuinely concurrent with the main thread's
    # own writes. A bare in-memory engine gives each thread its own separate,
    # table-less database (the lesson CLAUDE.md's "Resilient loading" note
    # already recorded once); forcing StaticPool (one shared connection) to
    # work around THAT then causes a DIFFERENT problem - two threads issuing
    # real concurrent writes over one shared sqlite3 connection object can
    # corrupt the other's transaction state (observed live as a spurious
    # `StaleDataError: 0 rows matched`). A real file gives each thread its
    # own actual connection, with SQLite's normal file-level locking
    # serializing concurrent writers - the same shape production already
    # relies on (a real sqlite file or Postgres), so this is more faithful
    # to production than either single-connection workaround.
    db_path = Path(tempfile.gettempdir()) / f"interior_gen_test_{uuid.uuid4().hex}.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False, "timeout": 30}
    )
    SQLModel.metadata.create_all(engine)
    return engine


def wait_for_floor_plan_status(engine, house_project_id, expected, timeout=10.0):
    """generate_floor_plan (v11) now runs on a detached daemon thread that
    run_house_pipeline() never joins - tests that care about its outcome
    (not just that the project completed without it) must poll instead of
    asserting immediately after run_house_pipeline() returns."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with Session(engine) as session:
            house_project = session.get(HouseProject, house_project_id)
            if house_project.floor_plan_status == expected:
                return house_project
        time.sleep(0.02)
    raise AssertionError(f"floor_plan_status never reached {expected!r} within {timeout}s")


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

    house_project = wait_for_floor_plan_status(engine, "h1", "not_configured")
    assert house_project.status == "done"
    assert house_project.plot_description == "A rectangular plot facing north."
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

    house_project = wait_for_floor_plan_status(engine, "h2", "done")
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

    house_project = wait_for_floor_plan_status(engine, "h9", "done")
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
        def generate_house_render(self, image_bytes, prompt, floor_count=None, wants_garage=None, color=None, style=None):
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
        def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
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
        def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
            call_order.append("room_layout")
            return super().generate_room_layout(dimensions, prompt, plot_description, floor_count)

        def generate_floor_plan(self, plot_description, dimensions, prompt, room_layout=None, facing=None):
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
        def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
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


def test_run_house_pipeline_skips_concept_layout_when_autocad_generation_enabled_is_false(monkeypatch):
    # Dev-only escape hatch (app/config.py's autocad_generation_enabled) -
    # added 2026-09-30 at the user's explicit request to locally test the
    # Kaggle/OpenAI room-redesign model toggle ("Model A"/"Model B") without
    # also waiting on the separate, slow AutoCAD Concept Layout stage every
    # run. Mirrors house_render_enabled's own test exactly.
    from app.config import settings

    monkeypatch.setattr(settings, "autocad_generation_enabled", False)

    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hautocadtoggle1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hautocadtoggle1", status="queued", plot_image_key="hautocadtoggle1/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "hautocadtoggle1", provider, storage, {"length": 40, "width": 60, "unit": "ft"}, prompt="1 floor"
    )

    assert provider.floor_plan_calls == []

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hautocadtoggle1")
        assert house_project.status == "done"
        assert house_project.floor_plan_status == "not_configured"
        # The rest of the pipeline (blueprint, DXF, and - unlike
        # house_render_enabled=False - the exterior render) still ran
        # normally; this toggle only ever skips the Concept Layout call.
        assert house_project.blueprint_status == "done"
        assert house_project.render_key is not None


def test_run_house_pipeline_renders_elevation_without_a_photo_for_text_to_image_backend(monkeypatch):
    # 2026-09-14: the Kaggle backend is a TEXT-TO-IMAGE elevation model that
    # needs no plot photo. A provider reporting house_render_needs_photo()==
    # False must have generate_house_render called even for a photo-less
    # project (unlike the default edit backend, which skips it) - and must
    # receive the structured floor count + the minimal (build_house_elevation_
    # prompt) prompt, NOT the long build_house_prompt edit paragraph.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()  # note: NO plot image stored for this project

    with Session(engine) as session:
        # plot_image_key=None -> plot_bytes stays None (no photo uploaded).
        house_project = HouseProject(id="helev1", status="queued")
        session.add(house_project)
        session.commit()

    class ElevationProvider(FakeProvider):
        def house_render_needs_photo(self, preferred_backend=None):
            return False

        def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
            self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
            # Two floors, so the resolved story count handed to
            # generate_house_render is a real 2 (the computed layout is
            # authoritative over the dropdown value).
            return {
                "floors": [
                    {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1}]},
                    {"floor_number": 2, "rooms": [{"name": "Bedroom", "area": 2}, {"name": "Bathroom", "area": 1}]},
                ]
            }

    provider = ElevationProvider()
    run_house_pipeline(
        "helev1",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="2 floors. Extras: modern car porch, glass balcony",
        floor_count=2,
    )

    assert len(provider.render_calls) == 1
    image_bytes, render_prompt, floor_count = provider.render_calls[0]
    assert image_bytes is None  # no photo, and this backend ignores it anyway
    assert floor_count == 2  # structured story count reached the provider
    # Minimal prompt = the user's own requirements text, NOT build_house_prompt's
    # long "Edit this photograph..." edit paragraph.
    assert "Edit this photograph" not in render_prompt
    assert "car porch" in render_prompt

    with Session(engine) as session:
        house_project = session.get(HouseProject, "helev1")
        assert house_project.status == "done"
        assert house_project.render_key is not None


def test_run_house_pipeline_threads_color_palette_as_a_structured_field_for_elevation(monkeypatch):
    # v15 (2026-09-18): for the TEXT-TO-IMAGE elevation backend, color is sent
    # as a STRUCTURED `color` kwarg to generate_house_render() (like
    # wants_garage), NOT embedded in the minimal elevation prompt text - a
    # trailing text clause was too weak against the notebook's own hardcoded
    # color vocabulary. So the prompt string must NOT contain the color words,
    # and provider.render_colors must carry the real resolved COLOR_PROFILE
    # text instead.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()

    with Session(engine) as session:
        house_project = HouseProject(id="helevpalette", status="queued")
        session.add(house_project)
        session.commit()

    class ElevationProvider(FakeProvider):
        def house_render_needs_photo(self, preferred_backend=None):
            return False

    provider = ElevationProvider()
    run_house_pipeline(
        "helevpalette",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        floor_count=1,
        color_palette="Earthy",
    )

    assert len(provider.render_calls) == 1
    _, render_prompt, _ = provider.render_calls[0]
    assert "clay" not in render_prompt.lower()
    assert "color" not in render_prompt.lower()
    assert provider.render_colors == ["clay, terracotta, olive green, warm brown, sand, stone, and natural wood tones"]


def test_run_house_pipeline_passes_none_color_when_no_palette_chosen(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()

    with Session(engine) as session:
        house_project = HouseProject(id="helevnopalette", status="queued")
        session.add(house_project)
        session.commit()

    class ElevationProvider(FakeProvider):
        def house_render_needs_photo(self, preferred_backend=None):
            return False

    provider = ElevationProvider()
    run_house_pipeline(
        "helevnopalette",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        floor_count=1,
    )

    assert provider.render_colors == [None]


def test_run_house_pipeline_threads_color_palette_into_the_edit_prompt(monkeypatch):
    # Default (photo-EDIT) backend path - build_house_prompt(), not
    # build_house_elevation_prompt(). Requires a real plot photo since the
    # default FakeProvider's house_render_needs_photo() defaults to True.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.put("users/u/buildAHouse/input/heditpalette/plot.png", b"fake-plot-bytes")

    with Session(engine) as session:
        house_project = HouseProject(
            id="heditpalette",
            status="queued",
            plot_image_key="users/u/buildAHouse/input/heditpalette/plot.png",
        )
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "heditpalette",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        color_palette="Cool",
    )

    assert len(provider.render_calls) == 1
    _, render_prompt, _ = provider.render_calls[0]
    assert "cool gray" in render_prompt.lower()
    assert "Edit this photograph" in render_prompt


def test_run_house_pipeline_threads_architectural_style_as_a_structured_field_for_elevation(monkeypatch):
    # v16 (2026-09-18): for the TEXT-TO-IMAGE elevation backend, architectural
    # style is sent as a STRUCTURED `style` kwarg to generate_house_render()
    # (like color/wants_garage), NOT embedded in the minimal elevation prompt
    # text - see house_prompts.py's v16 docstring note. So the prompt string
    # must NOT contain the style words, and provider.render_styles must carry
    # the real resolved HOUSE_STYLE_PROFILES text instead.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()

    with Session(engine) as session:
        house_project = HouseProject(id="helevstyle", status="queued")
        session.add(house_project)
        session.commit()

    class ElevationProvider(FakeProvider):
        def house_render_needs_photo(self, preferred_backend=None):
            return False

    provider = ElevationProvider()
    run_house_pipeline(
        "helevstyle",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        floor_count=1,
        architectural_style="Mediterranean",
    )

    assert len(provider.render_calls) == 1
    _, render_prompt, _ = provider.render_calls[0]
    assert "mediterranean" not in render_prompt.lower()
    assert "villa" not in render_prompt.lower()
    from app.pipeline.house_prompts import HOUSE_STYLE_PROFILES

    assert provider.render_styles == [HOUSE_STYLE_PROFILES["Mediterranean"]]


def test_run_house_pipeline_passes_none_style_when_no_style_chosen(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()

    with Session(engine) as session:
        house_project = HouseProject(id="helevnostyle", status="queued")
        session.add(house_project)
        session.commit()

    class ElevationProvider(FakeProvider):
        def house_render_needs_photo(self, preferred_backend=None):
            return False

    provider = ElevationProvider()
    run_house_pipeline(
        "helevnostyle",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        floor_count=1,
    )

    assert provider.render_styles == [None]


def test_run_house_pipeline_threads_architectural_style_into_the_edit_prompt(monkeypatch):
    # Default (photo-EDIT) backend path - build_house_prompt(), not
    # build_house_elevation_prompt(). Requires a real plot photo since the
    # default FakeProvider's house_render_needs_photo() defaults to True.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.put("users/u/buildAHouse/input/heditstyle/plot.png", b"fake-plot-bytes")

    with Session(engine) as session:
        house_project = HouseProject(
            id="heditstyle",
            status="queued",
            plot_image_key="users/u/buildAHouse/input/heditstyle/plot.png",
        )
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "heditstyle",
        provider,
        storage,
        {"length": 30, "width": 30, "unit": "ft"},
        prompt="1 floor",
        architectural_style="Industrial",
    )

    assert len(provider.render_calls) == 1
    _, render_prompt, _ = provider.render_calls[0]
    assert "industrial architecture" in render_prompt.lower()
    assert "Edit this photograph" in render_prompt


class ManyRoomsProvider(FakeProvider):
    """Returns a room program too large for a tiny plot - used to exercise
    the feasibility hard gate (2026-08-27)."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
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


def test_run_house_pipeline_injects_an_entry_room_when_garage_requested_but_no_foyer(monkeypatch):
    # 2026-09-04: a garage requested but no foyer/entry/lobby room present
    # must guarantee a real Entry room too, so blueprint_svg.py's garage-
    # door-suppression rule has a real room to route garage traffic through
    # instead of straight into Living Room.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hentry1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hentry1", status="queued", plot_image_key="hentry1/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "hentry1", provider, storage, {"length": 60, "width": 80, "unit": "ft"}, prompt="Extras: 2 car garage"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hentry1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Garage" in room_names
        assert "Entry" in room_names


class GeminiAddsUnrequestedGarageProvider(FakeProvider):
    """Returns a room_layout that ALREADY includes a Garage room the model
    invented on its own (ROOM_LAYOUT_PROMPT_TEMPLATE explicitly leaves adding
    one up to Gemini's own judgement) - used to verify that an unrequested
    garage is stripped out rather than reaching layout_floor() (2026-09-04,
    real user report: an unrequested garage was rendered as an oversized,
    full-depth strip that cramped every other room)."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [
                        {"name": "Entry", "area": 1},
                        {"name": "Living Room", "area": 3},
                        {"name": "Kitchen", "area": 1.5},
                        {"name": "Dining Room", "area": 1.5},
                        {"name": "Garage", "area": 2},
                    ],
                }
            ]
        }


def test_run_house_pipeline_strips_an_unrequested_garage_gemini_invented(monkeypatch):
    # The user never mentioned "garage" anywhere in their requirements text,
    # but Gemini's own room_layout included one anyway - it must be removed
    # before layout_floor() runs, since ANY room named "Garage" gets the
    # real, full-box-height carve-out treatment in _slice_reserving_garage()
    # regardless of whether the user actually asked for it.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hnogarage1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hnogarage1", status="queued", plot_image_key="hnogarage1/plot.png")
        session.add(house_project)
        session.commit()

    provider = GeminiAddsUnrequestedGarageProvider()
    run_house_pipeline(
        "hnogarage1", provider, storage, {"length": 60, "width": 40, "unit": "ft"}, prompt="2 floors"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hnogarage1")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Garage" not in room_names


def test_run_house_pipeline_injects_a_utility_room_when_requested_but_missing(monkeypatch):
    # 2026-09-19: mirrors the garage injection test above exactly - FakeProvider's
    # default room_layout has no utility/laundry room, so mentioning one in the
    # requirements text must guarantee it exists on the ground floor.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hutility1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hutility1", status="queued", plot_image_key="hutility1/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()
    run_house_pipeline(
        "hutility1", provider, storage, {"length": 60, "width": 80, "unit": "ft"}, prompt="Extras: utility room"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hutility1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Utility Room" in room_names


class GeminiAddsUnrequestedUtilityProvider(FakeProvider):
    """Returns a room_layout that already includes a laundry room the model
    invented on its own initiative, mirroring GeminiAddsUnrequestedGarageProvider
    above - used to verify an unrequested utility/laundry room is stripped."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [
                        {"name": "Entry", "area": 1},
                        {"name": "Living Room", "area": 3},
                        {"name": "Kitchen", "area": 1.5},
                        {"name": "Laundry Room", "area": 1},
                    ],
                }
            ]
        }


def test_run_house_pipeline_strips_an_unrequested_utility_room_gemini_invented(monkeypatch):
    # The user never mentioned "utility"/"laundry" anywhere in their
    # requirements text, but Gemini's own room_layout included one anyway -
    # it must be removed, same deterministic-overrides-probabilistic pattern
    # already established for garage.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hnoutility1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hnoutility1", status="queued", plot_image_key="hnoutility1/plot.png")
        session.add(house_project)
        session.commit()

    provider = GeminiAddsUnrequestedUtilityProvider()
    run_house_pipeline(
        "hnoutility1", provider, storage, {"length": 60, "width": 40, "unit": "ft"}, prompt="2 floors"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hnoutility1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Laundry Room" not in room_names


class TwoFloorProvider(FakeProvider):
    """Returns a real 2-floor room_layout, neither floor including a
    staircase - used to verify the real per-floor staircase injection
    (2026-08-29)."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [{"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1}],
                },
                {
                    "floor_number": 2,
                    "rooms": [{"name": "Bedroom 1", "area": 2}, {"name": "Bedroom 2", "area": 2}],
                },
            ]
        }


def test_run_house_pipeline_injects_a_staircase_on_every_floor_of_a_multi_floor_building(monkeypatch):
    # Neither floor in TwoFloorProvider's room_layout includes a staircase -
    # a real one (not just a symbol) must be guaranteed on EVERY floor, not
    # just the ground floor (unlike garage, which only makes sense there).
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hstair1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hstair1", status="queued", plot_image_key="hstair1/plot.png")
        session.add(house_project)
        session.commit()

    provider = TwoFloorProvider()
    run_house_pipeline(
        "hstair1", provider, storage, {"length": 60, "width": 80, "unit": "ft"}, prompt="2 floors, modern style"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hstair1")
        assert house_project.status == "done"
        assert house_project.blueprint_status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        for floor in room_layout["floors"]:
            room_names = [r["name"] for r in floor["rooms"]]
            assert "Staircase" in room_names
        blueprint_keys = json.loads(house_project.blueprint_keys_json)
        assert len(blueprint_keys) == 2


def test_run_house_pipeline_does_not_inject_staircase_for_single_floor(monkeypatch):
    # A single-storey building has nothing to connect - no staircase room
    # should be injected.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hstair2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hstair2", status="queued", plot_image_key="hstair2/plot.png")
        session.add(house_project)
        session.commit()

    provider = FakeProvider()  # default: single floor, Living Room + Bedroom
    run_house_pipeline("hstair2", provider, storage, {"length": 60, "width": 80, "unit": "ft"})

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hstair2")
        room_layout = json.loads(house_project.room_layout_json)
        room_names = [r["name"] for r in room_layout["floors"][0]["rooms"]]
        assert "Staircase" not in room_names


# ---- _enforce_room_type_count() / _enforce_room_counts() - pure unit tests ----
# ---- (app/pipeline/generate_house.py) - the real deterministic backstop  ----
# ---- for a live-reproduced bug: a 2-floor/3-bedroom/2-bathroom request   ----
# ---- came back with floor 2 containing only a single "Hallway" room.    ----


def test_enforce_room_type_count_pads_shortfall():
    rooms = [{"name": "Hallway", "area": 5}]
    generate_house_module._enforce_room_type_count(rooms, "bedroom", 3, "Bedroom")
    from app.pipeline.room_specs import classify_room_category

    beds = [r for r in rooms if classify_room_category(r["name"]) == "bedroom"]
    assert len(beds) == 3
    # The pre-existing Hallway must be untouched, not replaced.
    assert any(r["name"] == "Hallway" for r in rooms)


def test_enforce_room_type_count_trims_surplus_keeping_the_first_matches():
    rooms = [
        {"name": "Bedroom 1", "area": 1},
        {"name": "Bedroom 2", "area": 1},
        {"name": "Bedroom 3", "area": 1},
        {"name": "Living Room", "area": 2},
    ]
    generate_house_module._enforce_room_type_count(rooms, "bedroom", 1, "Bedroom")
    from app.pipeline.room_specs import classify_room_category

    beds = [r for r in rooms if classify_room_category(r["name"]) == "bedroom"]
    assert len(beds) == 1
    assert beds[0]["name"] == "Bedroom 1"
    # Non-matching rooms are never touched by a trim pass for a different category.
    assert any(r["name"] == "Living Room" for r in rooms)


def test_enforce_room_type_count_noop_when_already_correct():
    rooms = [{"name": "Bedroom 1", "area": 1}, {"name": "Bedroom 2", "area": 1}]
    generate_house_module._enforce_room_type_count(rooms, "bedroom", 2, "Bedroom")
    assert len(rooms) == 2


def test_enforce_room_counts_fixes_a_floor_collapsed_to_only_a_hallway():
    # The exact reported bug: floor 2 has NOTHING but a Hallway - no
    # bedrooms, no bathrooms at all.
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Hallway", "area": 5}]},
        ]
    }
    generate_house_module._enforce_room_counts(room_layout, [0, 3], [0, 2])

    from app.pipeline.room_specs import classify_room_category

    floor2_rooms = room_layout["floors"][1]["rooms"]
    beds = [r for r in floor2_rooms if classify_room_category(r["name"]) == "bedroom"]
    baths = [r for r in floor2_rooms if classify_room_category(r["name"]) == "bathroom"]
    assert len(beds) == 3
    assert len(baths) == 2
    # Ground floor's own target (0/0) leaves it untouched - no bedrooms
    # added to a floor that wasn't asked for any.
    ground_rooms = room_layout["floors"][0]["rooms"]
    assert not any(classify_room_category(r["name"]) == "bedroom" for r in ground_rooms)


def test_enforce_room_counts_applies_to_ground_floor_too():
    # Explicit user decision: bedrooms/bathrooms apply to EVERY floor
    # including ground, not just upper floors.
    room_layout = {
        "floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}],
    }
    generate_house_module._enforce_room_counts(room_layout, [2], [1])

    from app.pipeline.room_specs import classify_room_category

    rooms = room_layout["floors"][0]["rooms"]
    beds = [r for r in rooms if classify_room_category(r["name"]) == "bedroom"]
    baths = [r for r in rooms if classify_room_category(r["name"]) == "bathroom"]
    assert len(beds) == 2
    assert len(baths) == 1


def test_enforce_room_counts_is_noop_when_neither_array_given():
    room_layout = {"floors": [{"floor_number": 1, "rooms": [{"name": "Hallway", "area": 5}]}]}
    generate_house_module._enforce_room_counts(room_layout, None, None)
    assert room_layout["floors"][0]["rooms"] == [{"name": "Hallway", "area": 5}]


class DeficientFloorProvider(FakeProvider):
    """Reproduces the exact live-reported bug: floor 2 of a 2-floor request
    comes back with only a single "Hallway" room - no bedrooms, no
    bathrooms at all, despite the user asking for 3 bedrooms/2 bathrooms."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [{"name": "Living Room", "area": 2}, {"name": "Kitchen", "area": 1}],
                },
                {
                    "floor_number": 2,
                    "rooms": [{"name": "Hallway", "area": 5}],
                },
            ]
        }


def test_run_house_pipeline_fixes_a_floor_that_came_back_with_no_bedrooms(monkeypatch):
    # Direct reproduction of the real, live-reported bug (2026-09-28): with
    # floor_bedrooms/floor_bathrooms given, a floor that Gemini returned as
    # just a bare "Hallway" must end up with the real requested counts by
    # the time the project completes - the whole point of
    # _enforce_room_counts() as a deterministic backstop.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hbedbath1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hbedbath1", status="queued", plot_image_key="hbedbath1/plot.png")
        session.add(house_project)
        session.commit()

    provider = DeficientFloorProvider()
    run_house_pipeline(
        "hbedbath1",
        provider,
        storage,
        {"length": 60, "width": 80, "unit": "ft"},
        prompt="2 floors, 3 bedrooms, 2 bathrooms",
        floor_count=2,
        floor_bedrooms=[0, 3],
        floor_bathrooms=[0, 2],
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hbedbath1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)

    from app.pipeline.room_specs import classify_room_category

    floor2_rooms = room_layout["floors"][1]["rooms"]
    beds = [r for r in floor2_rooms if classify_room_category(r["name"]) == "bedroom"]
    baths = [r for r in floor2_rooms if classify_room_category(r["name"]) == "bathroom"]
    assert len(beds) == 3
    assert len(baths) == 2
    # A hallway/corridor, if any, is IN ADDITION to the real rooms, not a
    # replacement for them - the original Hallway room may still be present.


class SurplusBedroomProvider(FakeProvider):
    """Returns MORE bedrooms on floor 1 than the user actually requested -
    verifies _enforce_room_counts() trims surplus, not just pads shortfalls."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [
                        {"name": "Bedroom 1", "area": 1},
                        {"name": "Bedroom 2", "area": 1},
                        {"name": "Bedroom 3", "area": 1},
                        {"name": "Bedroom 4", "area": 1},
                        {"name": "Bathroom", "area": 0.5},
                    ],
                }
            ]
        }


def test_run_house_pipeline_trims_surplus_bedrooms_to_the_requested_count(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hbedbath2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hbedbath2", status="queued", plot_image_key="hbedbath2/plot.png")
        session.add(house_project)
        session.commit()

    provider = SurplusBedroomProvider()
    run_house_pipeline(
        "hbedbath2",
        provider,
        storage,
        {"length": 60, "width": 80, "unit": "ft"},
        prompt="1 floor, 2 bedrooms, 1 bathroom",
        floor_count=1,
        floor_bedrooms=[2],
        floor_bathrooms=[1],
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hbedbath2")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)

    from app.pipeline.room_specs import classify_room_category

    rooms = room_layout["floors"][0]["rooms"]
    beds = [r for r in rooms if classify_room_category(r["name"]) == "bedroom"]
    assert len(beds) == 2


# ---- _ensure_ground_floor_public_rooms() - pure unit tests + a direct    ----
# ---- reproduction of the "Hallway balloons and crowds out real rooms"    ----
# ---- bug (2026-09-29), the second, separate real bug found after the     ----
# ---- bedroom/bathroom-count fix above shipped.                           ----


def test_ensure_ground_floor_public_rooms_adds_missing_rooms():
    room_layout = {"floors": [{"floor_number": 1, "rooms": [{"name": "Hallway", "area": 5}]}]}
    generate_house_module._ensure_ground_floor_public_rooms(room_layout)

    from app.pipeline.room_specs import classify_room_category

    rooms = room_layout["floors"][0]["rooms"]
    categories = {classify_room_category(r["name"]) for r in rooms}
    assert {"living", "kitchen", "dining"} <= categories
    # The pre-existing Hallway is untouched, not replaced.
    assert any(r["name"] == "Hallway" for r in rooms)


def test_ensure_ground_floor_public_rooms_is_noop_when_already_present():
    room_layout = {
        "floors": [
            {
                "floor_number": 1,
                "rooms": [
                    {"name": "Living Room", "area": 3},
                    {"name": "Kitchen", "area": 1},
                    {"name": "Dining Room", "area": 1},
                ],
            }
        ]
    }
    before = json.loads(json.dumps(room_layout))
    generate_house_module._ensure_ground_floor_public_rooms(room_layout)
    assert room_layout == before


def test_ensure_ground_floor_public_rooms_only_touches_the_ground_floor():
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Garage", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 1", "area": 2}]},
        ]
    }
    generate_house_module._ensure_ground_floor_public_rooms(room_layout)

    from app.pipeline.room_specs import classify_room_category

    floor2_categories = {classify_room_category(r["name"]) for r in room_layout["floors"][1]["rooms"]}
    assert "living" not in floor2_categories
    assert "kitchen" not in floor2_categories
    assert "dining" not in floor2_categories


def test_ensure_ground_floor_public_rooms_noop_for_empty_floors():
    room_layout = {"floors": []}
    generate_house_module._ensure_ground_floor_public_rooms(room_layout)
    assert room_layout == {"floors": []}


# ---- _ensure_upper_floor_lounge() - explicit user request (2026-09-30):  ----
# ---- "i want a lounge area to be on the floors above the first one"     ----


def test_ensure_upper_floor_lounge_adds_a_lounge_to_every_upper_floor():
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 1", "area": 2}]},
            {"floor_number": 3, "rooms": [{"name": "Bedroom 2", "area": 2}]},
        ]
    }
    generate_house_module._ensure_upper_floor_lounge(room_layout)

    from app.pipeline.room_specs import classify_room_category

    for floor in room_layout["floors"][1:]:
        categories = {classify_room_category(r["name"]) for r in floor["rooms"]}
        assert "living" in categories


def test_ensure_upper_floor_lounge_uses_a_label_that_is_not_the_public_zone():
    # Real, live-reported bug (2026-09-30), caught the SAME DAY this feature
    # shipped: the room was first labeled "Lounge", which
    # floor_layout._PUBLIC_ZONE_KEYWORDS ALSO matches as a ground-floor-
    # style PUBLIC room - on an upper floor with no other public room, the
    # injected Lounge became the entire top-level "front" zone by itself,
    # and (being "living" category, the most generous ROOM_MAX_MULTIPLIER
    # in the whole system) absorbed nearly all of the floor's redistributed
    # excess area - a real reproduction came back with a 9,451 sq ft
    # "Lounge" and 5 real bedrooms squeezed into 30x77ft slivers. The label
    # this function injects must classify as "living" (for sizing/
    # furniture) but NOT match the public-zone keyword list.
    from app.pipeline.floor_layout import _zone_key
    from app.pipeline.room_specs import classify_room_category

    room_layout = {"floors": [{"floor_number": 2, "rooms": [{"name": "Bedroom 1", "area": 2}]}]}
    generate_house_module._ensure_upper_floor_lounge(room_layout)

    injected = next(r for r in room_layout["floors"][0]["rooms"] if r["name"] != "Bedroom 1")
    assert classify_room_category(injected["name"]) == "living"
    assert _zone_key(injected["name"]) != 0  # must NOT be treated as a public/front-zone room


def test_run_house_pipeline_upper_floor_lounge_does_not_dominate_a_sparse_floor(monkeypatch):
    # End-to-end reproduction of the exact live-reported scenario: a large
    # plot with only bedrooms on an upper floor. Before the fix, the
    # injected lounge alone claimed nearly half the floor while bedrooms
    # were squeezed into degenerate slivers - after the fix, the lounge is
    # just one more room sharing the private zone's row-packing, bounded by
    # the same min/max-area machinery as every sibling room.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hsparseupper1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hsparseupper1", status="queued", plot_image_key="hsparseupper1/plot.png")
        session.add(house_project)
        session.commit()

    class SparseUpperFloorProvider(FakeProvider):
        def generate_room_layout(
            self, dimensions, prompt, plot_description=None, floor_count=None,
            floor_bedrooms=None, floor_bathrooms=None, extra_rooms=None,
        ):
            self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
            return {
                "floors": [
                    {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
                    {
                        "floor_number": 2,
                        "rooms": [
                            {"name": "Bedroom 1", "area": 1.5},
                            {"name": "Bedroom 2", "area": 1.5},
                            {"name": "Bedroom 3", "area": 1.5},
                        ],
                    },
                ]
            }

    provider = SparseUpperFloorProvider()
    run_house_pipeline(
        "hsparseupper1", provider, storage, {"length": 150.0, "width": 150.0, "unit": "ft"},
        prompt="2 floors", floor_count=2,
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hsparseupper1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)

    from app.pipeline.blueprint_svg import _shared_edge
    from app.pipeline.floor_layout import layout_floor

    floor2_rooms = room_layout["floors"][1]["rooms"]
    rects = layout_floor(floor2_rooms, {"length": 150.0, "width": 150.0, "unit": "ft"}, facing="south")
    lounge_rect = next(r for r in rects if r["name"] == "Sitting Area")
    bedroom_rects = [r for r in rects if r["name"].startswith("Bedroom")]
    hallway_rects = [r for r in rects if r["name"] == "Hallway"]

    # The REAL, now-fixed bug: the lounge used to be split off into its own
    # top-level "front" zone, structurally disconnected from the bedrooms/
    # hallway it should belong with (a separate recursive split, not a
    # shared row). After the fix, it's one more item packed in the SAME row
    # as its sibling bedrooms - same cross-dimension (row depth) as every
    # bedroom, and it genuinely borders the hallway, instead of floating in
    # its own disconnected region.
    assert bedroom_rects
    for bedroom in bedroom_rects:
        assert abs(bedroom["h"] - lounge_rect["h"]) < 1e-6  # same row, same cross-dimension
    assert hallway_rects
    assert any(_shared_edge(lounge_rect, h) is not None for h in hallway_rects)


def test_ensure_upper_floor_lounge_never_touches_the_ground_floor():
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Bedroom 1", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Bedroom 2", "area": 2}]},
        ]
    }
    generate_house_module._ensure_upper_floor_lounge(room_layout)

    from app.pipeline.room_specs import classify_room_category

    ground_categories = {classify_room_category(r["name"]) for r in room_layout["floors"][0]["rooms"]}
    assert "living" not in ground_categories


def test_ensure_upper_floor_lounge_is_noop_when_already_present():
    room_layout = {
        "floors": [
            {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
            {"floor_number": 2, "rooms": [{"name": "Family Lounge", "area": 1.5}, {"name": "Bedroom 1", "area": 2}]},
        ]
    }
    before = json.loads(json.dumps(room_layout))
    generate_house_module._ensure_upper_floor_lounge(room_layout)
    assert room_layout == before


def test_ensure_upper_floor_lounge_noop_for_single_storey():
    room_layout = {"floors": [{"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]}]}
    before = json.loads(json.dumps(room_layout))
    generate_house_module._ensure_upper_floor_lounge(room_layout)
    assert room_layout == before


def test_run_house_pipeline_guarantees_a_lounge_on_every_upper_floor(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hlounge1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hlounge1", status="queued", plot_image_key="hlounge1/plot.png")
        session.add(house_project)
        session.commit()

    provider = IgnoresExtraRoomRequestProvider()  # floor 2: just bedroom + bathroom, no lounge
    run_house_pipeline(
        "hlounge1",
        provider,
        storage,
        {"length": 60, "width": 80, "unit": "ft"},
        prompt="2 floors",
        floor_count=2,
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hlounge1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)

    from app.pipeline.room_specs import classify_room_category

    floor2_rooms = room_layout["floors"][1]["rooms"]
    assert any(classify_room_category(r["name"]) == "living" for r in floor2_rooms)


class HallwayDominatedProvider(FakeProvider):
    """Reproduces the real, live-reported bug (2026-09-29): a ground floor
    whose room list is almost entirely a generic, unrecognized "Hallway"
    room, with real public rooms (Living Room/Kitchen/Dining) entirely
    absent - exactly the shape that, before room_specs.py's "default"
    category max-multiplier was capped, let the Hallway balloon to roughly
    the size of the whole floor via layout_floor()'s weight-based
    redistribution."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {
                    "floor_number": 1,
                    "rooms": [
                        {"name": "Hallway", "area": 10},
                        {"name": "Bedroom 1", "area": 1},
                        {"name": "Bathroom", "area": 1},
                    ],
                }
            ]
        }


def test_run_house_pipeline_guarantees_living_kitchen_dining_and_caps_the_hallway(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hhallway1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hhallway1", status="queued", plot_image_key="hhallway1/plot.png")
        session.add(house_project)
        session.commit()

    provider = HallwayDominatedProvider()
    run_house_pipeline(
        "hhallway1",
        provider,
        storage,
        {"length": 50, "width": 40, "unit": "ft"},
        prompt="1 floor",
        floor_count=1,
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hhallway1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)
        blueprint_keys = json.loads(house_project.blueprint_keys_json)

    from app.pipeline.room_specs import classify_room_category

    rooms = room_layout["floors"][0]["rooms"]
    categories = {classify_room_category(r["name"]) for r in rooms}
    assert {"living", "kitchen", "dining"} <= categories
    assert len(blueprint_keys) == 1

    # The Hallway's own ceiling is now bounded (room_specs.py's "default"
    # category max-multiplier is finite, not math.inf) - the room mix as a
    # whole may still legitimately put a lot of area into Living Room (its
    # own 4.0x cap is deliberately generous - it's meant to be the dominant
    # everyday social space, see room_specs.ROOM_MAX_MULTIPLIER's own
    # docstring), but the unrecognized Hallway itself can no longer grow
    # past its own finite ceiling (with a little slack for the redistribution
    # pass's own tie-breaking) the way it could when it was uncapped.
    from app.pipeline.floor_layout import layout_floor
    from app.pipeline.room_specs import max_area_for_room

    hallway_cap = max_area_for_room("Hallway", "ft")
    assert math.isfinite(hallway_cap)
    rects = layout_floor(rooms, {"length": 50, "width": 40, "unit": "ft"})
    hallway_total_area = sum(r["w"] * r["h"] for r in rects if classify_room_category(r["name"]) == "default")
    assert hallway_total_area < hallway_cap * 2.5


# ---- Explicit per-floor room requests (2026-09-30) - real user complaint:  ----
# ---- "you are hardcoding everything... make it not hallucinate when I     ----
# ---- tell it to add a kitchen on 2nd floor or a dining room on 3rd floor" ----


class IgnoresExtraRoomRequestProvider(FakeProvider):
    """Simulates Gemini NOT honoring the user's explicit per-floor request -
    floor 2 comes back with only bedrooms/bathrooms, no Kitchen at all,
    despite the user's prompt asking for one there. This is the realistic
    failure mode this feature protects against (the LLM call is inherently
    probabilistic) - the deterministic backstop must still guarantee it."""

    def generate_room_layout(
        self,
        dimensions,
        prompt,
        plot_description=None,
        floor_count=None,
        floor_bedrooms=None,
        floor_bathrooms=None,
        extra_rooms=None,
    ):
        self.room_layout_calls.append((dimensions, prompt, plot_description, floor_count))
        return {
            "floors": [
                {"floor_number": 1, "rooms": [{"name": "Living Room", "area": 2}]},
                {
                    "floor_number": 2,
                    "rooms": [{"name": "Bedroom 1", "area": 2}, {"name": "Bathroom 1", "area": 1}],
                },
            ]
        }


def test_run_house_pipeline_guarantees_an_explicit_kitchen_on_floor_2(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hextraroom1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hextraroom1", status="queued", plot_image_key="hextraroom1/plot.png")
        session.add(house_project)
        session.commit()

    provider = IgnoresExtraRoomRequestProvider()
    run_house_pipeline(
        "hextraroom1",
        provider,
        storage,
        {"length": 60, "width": 80, "unit": "ft"},
        prompt="2 floors. Extras: kitchen on the 2nd floor",
        floor_count=2,
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hextraroom1")
        assert house_project.status == "done"
        room_layout = json.loads(house_project.room_layout_json)

    from app.pipeline.room_specs import classify_room_category

    floor2_rooms = room_layout["floors"][1]["rooms"]
    assert any(classify_room_category(r["name"]) == "kitchen" for r in floor2_rooms)


def test_run_house_pipeline_passes_extra_rooms_to_generate_room_layout(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hextraroom2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hextraroom2", status="queued", plot_image_key="hextraroom2/plot.png")
        session.add(house_project)
        session.commit()

    captured = []
    real_fake = FakeProvider()

    def spy_generate_room_layout(
        dimensions, prompt, plot_description=None, floor_count=None,
        floor_bedrooms=None, floor_bathrooms=None, extra_rooms=None,
    ):
        captured.append(extra_rooms)
        return real_fake.generate_room_layout(
            dimensions, prompt, plot_description, floor_count, floor_bedrooms, floor_bathrooms, extra_rooms
        )

    provider = FakeProvider()
    provider.generate_room_layout = spy_generate_room_layout

    run_house_pipeline(
        "hextraroom2",
        provider,
        storage,
        {"length": 60, "width": 80, "unit": "ft"},
        prompt="2 floors. Extras: dining room on floor 2",
        floor_count=2,
    )

    assert captured
    assert ("dining", "Dining Room", 2) in captured[0]


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

    def spy_layout_floor(rooms, dimensions, garage_cars=None, facing=None):
        captured_dimensions.append(dict(dimensions))
        return real_layout_floor(rooms, dimensions, garage_cars, facing)

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


def test_run_house_pipeline_defaults_facing_to_south_when_unspecified(monkeypatch):
    # Real user request (2026-09-04): "if the user doesnt choose anything
    # keep the entrance from south" - facing_input=None (no form selection)
    # must resolve to a real "south" passed into layout_floor(), not silently
    # stay unset/None (which would mean layout_floor()'s OWN neutral "north"
    # default instead).
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hfacing1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hfacing1", status="queued", plot_image_key="hfacing1/plot.png")
        session.add(house_project)
        session.commit()

    captured_facings = []
    real_layout_floor = generate_house_module.layout_floor

    def spy_layout_floor(rooms, dimensions, garage_cars=None, facing=None):
        captured_facings.append(facing)
        return real_layout_floor(rooms, dimensions, garage_cars, facing)

    monkeypatch.setattr(generate_house_module, "layout_floor", spy_layout_floor)

    provider = FakeProvider()
    run_house_pipeline("hfacing1", provider, storage, {"length": 40, "width": 60, "unit": "ft"})

    assert captured_facings == ["south"]


def test_run_house_pipeline_honors_an_explicit_facing_selection(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hfacing2/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hfacing2", status="queued", plot_image_key="hfacing2/plot.png")
        session.add(house_project)
        session.commit()

    captured_facings = []
    real_layout_floor = generate_house_module.layout_floor

    def spy_layout_floor(rooms, dimensions, garage_cars=None, facing=None):
        captured_facings.append(facing)
        return real_layout_floor(rooms, dimensions, garage_cars, facing)

    monkeypatch.setattr(generate_house_module, "layout_floor", spy_layout_floor)

    provider = FakeProvider()
    run_house_pipeline(
        "hfacing2", provider, storage, {"length": 40, "width": 60, "unit": "ft"}, facing_input="East"
    )

    assert captured_facings == ["east"]


def test_run_house_pipeline_passes_facing_to_blueprint_and_dxf_renderers(monkeypatch):
    # 2026-09-19: facing must reach render_floor_blueprint()/
    # render_floor_blueprint_dxf() too, not just layout_floor() - without
    # this, the deterministic renderers would never draw the real front
    # entrance blueprint_svg._front_door_opening() computes.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hfacing3/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hfacing3", status="queued", plot_image_key="hfacing3/plot.png")
        session.add(house_project)
        session.commit()

    captured_blueprint_facings = []
    real_render_floor_blueprint = generate_house_module.render_floor_blueprint

    def spy_render_floor_blueprint(floor_number, rects, dimensions, total_floors=1, facing=None):
        captured_blueprint_facings.append(facing)
        return real_render_floor_blueprint(floor_number, rects, dimensions, total_floors, facing)

    captured_dxf_facings = []
    real_render_floor_blueprint_dxf = generate_house_module.render_floor_blueprint_dxf

    def spy_render_floor_blueprint_dxf(floor_number, rects, dimensions, facing=None):
        captured_dxf_facings.append(facing)
        return real_render_floor_blueprint_dxf(floor_number, rects, dimensions, facing)

    monkeypatch.setattr(generate_house_module, "render_floor_blueprint", spy_render_floor_blueprint)
    monkeypatch.setattr(generate_house_module, "render_floor_blueprint_dxf", spy_render_floor_blueprint_dxf)

    provider = FakeProvider()
    run_house_pipeline(
        "hfacing3", provider, storage, {"length": 40, "width": 60, "unit": "ft"}, facing_input="West"
    )

    assert captured_blueprint_facings == ["west"]
    assert captured_dxf_facings == ["west"]


def test_run_house_pipeline_does_not_wait_for_floor_plan_before_completing(monkeypatch):
    # v11 (2026-09-01): real regression guard for the decoupling - a live
    # Kaggle Concept Layout call was measured at ~2min/floor, and the whole
    # point of v11 is that the house project must NOT wait on it.
    # generate_floor_plan sleeps a full 2s (deliberately large - the real
    # file-backed test engine, see make_test_engine()'s docstring, has
    # genuine per-commit disk I/O overhead that a tight sub-second threshold
    # flaked against) - if it were still awaited (v10's concurrent-but-joined
    # design, or a sequential regression), the pipeline would take >=2s. It
    # should return in well under that, since only generate_house_render
    # (0.1s) is actually awaited.
    engine = make_test_engine()
    monkeypatch.setattr(generate_house_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["hconcurrent1/plot.png"] = b"plot-bytes"

    with Session(engine) as session:
        house_project = HouseProject(id="hconcurrent1", status="queued", plot_image_key="hconcurrent1/plot.png")
        session.add(house_project)
        session.commit()

    class SlowProvider(FakeProvider):
        def generate_floor_plan(self, plot_description, dimensions, prompt, room_layout=None, facing=None):
            time.sleep(2.0)
            return super().generate_floor_plan(plot_description, dimensions, prompt, room_layout)

        def generate_house_render(self, image_bytes, prompt, floor_count=None, wants_garage=None, color=None, style=None):
            time.sleep(0.1)
            return super().generate_house_render(image_bytes, prompt, floor_count)

    provider = SlowProvider(floor_plan_images=[b"floor1-bytes"])
    start = time.monotonic()
    run_house_pipeline("hconcurrent1", provider, storage, {"length": 40, "width": 60, "unit": "ft"})
    elapsed = time.monotonic() - start

    assert elapsed < 1.5, (
        f"run_house_pipeline took {elapsed:.2f}s - expected it to return without waiting on the "
        "2s generate_floor_plan call at all"
    )

    with Session(engine) as session:
        house_project = session.get(HouseProject, "hconcurrent1")
        assert house_project.status == "done"
        assert house_project.render_key is not None
        # The detached thread hasn't necessarily finished yet - "running" is
        # the expected state immediately after the pipeline itself completes.
        assert house_project.floor_plan_status == "running"

    # Give the detached thread time to finish and commit on its own (it sleeps
    # 2.0s itself, so the default wait_for_floor_plan_status timeout needs
    # real headroom above that).
    house_project = wait_for_floor_plan_status(engine, "hconcurrent1", "done", timeout=4.0)
    assert house_project.floor_plan_key is not None
