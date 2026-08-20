import json
import time

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import Project
from app.pipeline import generate as generate_module
from app.pipeline.generate import TIERS, run_pipeline


class FakeProvider:
    def __init__(self):
        self.image_calls = []
        self.materials_calls = []

    def describe_room(self, image_bytes: bytes) -> str:
        return "A rectangular room with one window and one door."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {"economical": "repaint over visible stains", "mid": "replace damaged flooring"}

    def generate_image(self, image_bytes: bytes, prompt: str, tier: str | None = None) -> bytes:
        self.image_calls.append(prompt)
        return b"fake-image-bytes"

    def generate_materials(
        self, tier, tier_spec, room_description, city, api_key=None, room_area_sqft=None, wall_area_sqft=None
    ):
        self.materials_calls.append((tier, city, api_key, room_area_sqft, wall_area_sqft))
        return {
            "items": [
                {
                    "name": "Flooring",
                    "spec": tier_spec.get("flooring", ""),
                    "price": "100",
                    "currency": "USD",
                    "source_url": None,
                    "is_estimate": True,
                }
            ],
            "total": "100",
            "currency": "USD",
        }

    def estimate_room_area(self, image_bytes: bytes) -> float | None:
        return 180.0


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


def test_run_pipeline_success(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p1/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p1", status="queued", original_key="p1/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p1", provider, storage, "Modern", "Neutral")

    with Session(engine) as session:
        project = session.get(Project, "p1")
        assert project.status == "done"
        assert project.room_description == "A rectangular room with one window and one door."
        for tier in TIERS:
            key = getattr(project, f"{tier}_key")
            assert key == f"local.output/p1/{tier}.png"
            assert storage.get(key) == b"fake-image-bytes"

    assert len(provider.image_calls) == 3

    economical_prompt = next(p for p in provider.image_calls if "budget renovation" in p)
    assert "repaint over visible stains" in economical_prompt


def test_run_pipeline_stores_image_model_label_when_provider_supports_it(monkeypatch):
    # FakeProvider (the default in this file) has no get_image_model_label(),
    # so project.image_model stays None - that's the normal/expected case
    # for a provider that doesn't track this (asserted implicitly by every
    # other test in this file still passing). This test covers the case
    # where the provider DOES support it (the real HybridProvider).
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p1c/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p1c", status="queued", original_key="p1c/original.png")
        session.add(project)
        session.commit()

    class LabeledProvider(FakeProvider):
        def get_image_model_label(self):
            return "our model"

    run_pipeline("p1c", LabeledProvider(), storage, "Modern", "Neutral")

    with Session(engine) as session:
        project = session.get(Project, "p1c")
        assert project.image_model == "our model"
        meta = json.loads(project.meta_json)
        assert meta["image_model"] == "our model"


def test_run_pipeline_image_model_stays_none_when_provider_does_not_track_it(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p1d/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p1d", status="queued", original_key="p1d/original.png")
        session.add(project)
        session.commit()

    run_pipeline("p1d", FakeProvider(), storage, "Modern", "Neutral")

    with Session(engine) as session:
        project = session.get(Project, "p1d")
        assert project.image_model is None


def test_run_pipeline_stops_when_cancelled_before_image_generation(monkeypatch):
    # Simulates a real cancel request (app/main.py's cancel_project) landing
    # via a SEPARATE session while the pipeline is mid-run - the pipeline's
    # own _is_cancelled() checkpoint must see it (via session.refresh(), not
    # a stale in-memory read) and stop before the costly image-generation
    # step, without overwriting the cancelled status.
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["pcancel1/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="pcancel1", status="queued", original_key="pcancel1/original.png")
        session.add(project)
        session.commit()

    class CancellingProvider(FakeProvider):
        def generate_tier_notes(self, image_bytes):
            with Session(engine) as cancel_session:
                p = cancel_session.get(Project, "pcancel1")
                p.status = "cancelled"
                cancel_session.add(p)
                cancel_session.commit()
            return {}

    provider = CancellingProvider()
    run_pipeline("pcancel1", provider, storage, "Modern", "Neutral")

    assert provider.image_calls == []

    with Session(engine) as session:
        project = session.get(Project, "pcancel1")
        assert project.status == "cancelled"
        for tier in TIERS:
            assert getattr(project, f"{tier}_key") is None


def test_run_pipeline_stops_when_cancelled_during_image_generation(monkeypatch):
    # StaticPool (not make_test_engine()'s default pooling) is required here
    # specifically because this test's fake provider opens a nested Session
    # from INSIDE generate_image(), which runs on a worker thread (the
    # per-tier ThreadPoolExecutor) - plain SQLite in-memory pooling gives
    # each thread its own separate :memory: database, which would silently
    # "lose" the project table. Real production code never does this (only
    # the main pipeline thread ever touches the session - see run_pipeline's
    # comment on that), this is purely a test-simulation need.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["pcancel2/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="pcancel2", status="queued", original_key="pcancel2/original.png")
        session.add(project)
        session.commit()

    class CancellingDuringImageProvider(FakeProvider):
        def generate_image(self, image_bytes, prompt, tier=None):
            if tier == "economical":
                with Session(engine) as cancel_session:
                    p = cancel_session.get(Project, "pcancel2")
                    p.status = "cancelled"
                    cancel_session.add(p)
                    cancel_session.commit()
            return super().generate_image(image_bytes, prompt, tier)

    run_pipeline("pcancel2", CancellingDuringImageProvider(), storage, "Modern", "Neutral", city="Karachi")

    with Session(engine) as session:
        project = session.get(Project, "pcancel2")
        assert project.status == "cancelled"
        # Materials/final "done" write never happened - the checkpoint after
        # image generation caught the cancellation first.
        assert project.materials_json is None


def test_run_pipeline_generates_tier_images_concurrently(monkeypatch):
    # Regression guard: the 3 tiers' generate_image calls used to run in a
    # plain sequential loop, so total wait time was ~3x a single call's -
    # the single biggest lever on perceived generation speed. Proves they
    # now genuinely overlap (wall-clock time well under the sum of all 3
    # simulated call durations), not just that the end result still works.
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p3/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p3", status="queued", original_key="p3/original.png")
        session.add(project)
        session.commit()

    class SlowProvider(FakeProvider):
        def generate_image(self, image_bytes, prompt, tier=None):
            time.sleep(0.3)
            return super().generate_image(image_bytes, prompt, tier)

    start = time.monotonic()
    run_pipeline("p3", SlowProvider(), storage, "Modern", "Neutral")
    elapsed = time.monotonic() - start

    # Sequential would take >= 0.9s (3 x 0.3s); concurrent should land close
    # to a single call's duration. 0.7s leaves generous margin for CI jitter
    # while still failing hard if it silently regresses to sequential.
    assert elapsed < 0.7, f"expected concurrent image generation, took {elapsed:.2f}s"

    with Session(engine) as session:
        project = session.get(Project, "p3")
        assert project.status == "done"
        for tier in TIERS:
            assert getattr(project, f"{tier}_key") == f"local.output/p3/{tier}.png"


def test_run_pipeline_stores_each_tiers_own_bytes_even_when_finishing_out_of_order(monkeypatch):
    # Regression guard for the concurrent generate_image + overlapped
    # storage.put rewrite (as_completed(), not a fixed-order loop): proves
    # tier identity is never lost/mixed up when tiers finish in a different
    # order than TIERS lists them - premium finishes fastest here, economical
    # slowest, the opposite of iteration order.
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p4/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p4", status="queued", original_key="p4/original.png")
        session.add(project)
        session.commit()

    DELAYS = {"economical": 0.15, "mid": 0.08, "premium": 0.0}

    class ReverseOrderProvider(FakeProvider):
        def generate_image(self, image_bytes, prompt, tier=None):
            time.sleep(DELAYS[tier])
            self.image_calls.append(prompt)
            return f"bytes-for-{tier}".encode()

    run_pipeline("p4", ReverseOrderProvider(), storage, "Modern", "Neutral")

    with Session(engine) as session:
        project = session.get(Project, "p4")
        assert project.status == "done"
        for tier in TIERS:
            key = getattr(project, f"{tier}_key")
            assert key == f"local.output/p4/{tier}.png"
            assert storage.get(key) == f"bytes-for-{tier}".encode()


def test_run_pipeline_passes_interior_style_and_palette_to_every_tier(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p3/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p3", status="queued", original_key="p3/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p3", provider, storage, "Japandi", "Sage")

    assert len(provider.image_calls) == 3
    for prompt in provider.image_calls:
        assert "Japandi" in prompt
        assert "sage green" in prompt

    with Session(engine) as session:
        project = session.get(Project, "p3")
        meta = json.loads(project.meta_json)
        assert meta["interior_style"] == "Japandi"
        assert meta["color_palette"] == "Sage"


def test_run_pipeline_passes_additional_instructions_to_every_tier(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p3b/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p3b", status="queued", original_key="p3b/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p3b", provider, storage, "Modern", "Neutral", additional_instructions="more indoor plants")

    assert len(provider.image_calls) == 3
    for prompt in provider.image_calls:
        assert "more indoor plants" in prompt

    with Session(engine) as session:
        project = session.get(Project, "p3b")
        meta = json.loads(project.meta_json)
        assert meta["additional_instructions"] == "more indoor plants"


def test_run_pipeline_marks_failed_on_provider_error(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p2/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p2", status="queued", original_key="p2/original.png")
        session.add(project)
        session.commit()

    class FailingProvider(FakeProvider):
        def generate_image(self, image_bytes, prompt, tier=None):
            raise RuntimeError("quota exceeded")

    run_pipeline("p2", FailingProvider(), storage, "Modern", "Neutral")

    with Session(engine) as session:
        project = session.get(Project, "p2")
        assert project.status == "failed"
        assert "quota exceeded" in project.error


def test_run_pipeline_without_city_skips_materials(monkeypatch):
    # No city => images-only path: materials_status must be "skipped" and
    # generate_materials must NEVER be called (no Gemini price calls at all).
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p4/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p4", status="queued", original_key="p4/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p4", provider, storage, "Modern", "Neutral")  # city defaults to None

    assert provider.materials_calls == []

    with Session(engine) as session:
        project = session.get(Project, "p4")
        assert project.status == "done"
        assert project.materials_status == "skipped"
        assert project.materials_json is None


def test_run_pipeline_with_city_runs_materials_for_every_tier(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p5/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p5", status="queued", original_key="p5/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p5", provider, storage, "Modern", "Neutral", city="Karachi")

    called_tiers = {tier for tier, _, _, _, _ in provider.materials_calls}
    assert called_tiers == set(TIERS)
    assert all(city == "Karachi" for _, city, _, _, _ in provider.materials_calls)
    assert all(area == 180.0 for _, _, _, area, _ in provider.materials_calls)

    with Session(engine) as session:
        project = session.get(Project, "p5")
        assert project.status == "done"
        assert project.materials_status == "done"
        assert project.city == "Karachi"
        materials = json.loads(project.materials_json)
        assert set(materials.keys()) == set(TIERS)
        for tier in TIERS:
            assert materials[tier]["items"][0]["name"] == "Flooring"


def test_run_pipeline_assigns_a_dedicated_key_per_tier_when_configured(monkeypatch):
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)
    # gemini_materials_api_keys is a read-only property derived from these 3
    # fields (see app/config.py) - patch the underlying fields, not the property.
    monkeypatch.setattr(generate_module.settings, "gemini_materials_api_key_economical", "key-econ")
    monkeypatch.setattr(generate_module.settings, "gemini_materials_api_key_mid", "key-mid")
    monkeypatch.setattr(generate_module.settings, "gemini_materials_api_key_premium", "key-premium")

    storage = FakeStorage()
    storage.objects["p6/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p6", status="queued", original_key="p6/original.png")
        session.add(project)
        session.commit()

    provider = FakeProvider()
    run_pipeline("p6", provider, storage, "Modern", "Neutral", city="Lahore")

    used_keys = {tier: key for tier, _, key, _, _ in provider.materials_calls}
    assert used_keys == {"economical": "key-econ", "mid": "key-mid", "premium": "key-premium"}


def test_run_pipeline_falls_back_when_materials_future_raises(monkeypatch):
    # Even if a tier's generate_materials call itself blows up unexpectedly
    # (beyond what its own internal try/except would normally catch), the
    # pipeline must still store a never-empty fallback rather than leaving
    # materials_json missing for that tier.
    engine = make_test_engine()
    monkeypatch.setattr(generate_module, "engine", engine)

    storage = FakeStorage()
    storage.objects["p7/original.png"] = b"original-bytes"

    with Session(engine) as session:
        project = Project(id="p7", status="queued", original_key="p7/original.png")
        session.add(project)
        session.commit()

    class ExplodingProvider(FakeProvider):
        def generate_materials(
            self, tier, tier_spec, room_description, city, api_key=None, room_area_sqft=None, wall_area_sqft=None
        ):
            raise RuntimeError("grounding service unavailable")

    run_pipeline("p7", ExplodingProvider(), storage, "Modern", "Neutral", city="Karachi")

    with Session(engine) as session:
        project = session.get(Project, "p7")
        assert project.status == "done"
        assert project.materials_status == "done"
        materials = json.loads(project.materials_json)
        for tier in TIERS:
            assert len(materials[tier]["items"]) > 0
            assert all(item["price"] for item in materials[tier]["items"])
