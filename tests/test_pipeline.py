from sqlmodel import Session, SQLModel, create_engine

from app.models import Project
from app.pipeline import generate as generate_module
from app.pipeline.generate import TIERS, run_pipeline


class FakeProvider:
    def __init__(self):
        self.image_calls = []

    def describe_room(self, image_bytes: bytes) -> str:
        return "A rectangular room with one window and one door."

    def generate_tier_notes(self, image_bytes: bytes) -> dict[str, str]:
        return {"economical": "repaint over visible stains", "mid": "replace damaged flooring"}

    def generate_image(
        self, image_bytes: bytes, prompt: str, negative_prompt: str = "", strength: float | None = None
    ) -> bytes:
        self.image_calls.append((prompt, negative_prompt, strength))
        return b"fake-image-bytes"


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
    run_pipeline("p1", provider, storage)

    with Session(engine) as session:
        project = session.get(Project, "p1")
        assert project.status == "done"
        assert project.room_description == "A rectangular room with one window and one door."
        for tier in TIERS:
            key = getattr(project, f"{tier}_key")
            assert key == f"p1/{tier}.png"
            assert storage.get(key) == b"fake-image-bytes"

    assert len(provider.image_calls) == 3

    economical_prompt = next(p for p, _, _ in provider.image_calls if "budget renovation" in p)
    assert "repaint over visible stains" in economical_prompt


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
        def generate_image(self, image_bytes, prompt, negative_prompt="", strength=None):
            raise RuntimeError("quota exceeded")

    run_pipeline("p2", FailingProvider(), storage)

    with Session(engine) as session:
        project = session.get(Project, "p2")
        assert project.status == "failed"
        assert "quota exceeded" in project.error
