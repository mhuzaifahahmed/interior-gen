import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session

from app.config import settings
from app.db import get_session, init_db
from app.models import Project
from app.pipeline.generate import TIERS, run_pipeline
from app.pipeline.prompts import USER_NOTES_MAX_CHARS
from app.providers import get_provider
from app.schemas import ProjectCreateResponse, ProjectStatusResponse
from app.storage import get_storage

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Interior-Gen Backend", lifespan=lifespan)

ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


if settings.storage_backend == "local":
    Path(settings.local_storage_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=settings.local_storage_dir), name="media")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    from fastapi.responses import FileResponse

    return FileResponse("static/index.html")


@app.get("/terms")
def terms():
    from fastapi.responses import FileResponse

    return FileResponse("static/terms.html")


@app.get("/privacy")
def privacy():
    from fastapi.responses import FileResponse

    return FileResponse("static/privacy.html")


@app.post("/api/projects", response_model=ProjectCreateResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    style_notes: str = Form(""),
    session: Session = Depends(get_session),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large (max 15MB)")

    # Defensive re-truncation (also enforced in build_prompt()) - the frontend's
    # <input maxlength> is trivially bypassable by anyone calling the API directly.
    style_notes = style_notes.strip()[:USER_NOTES_MAX_CHARS] or None

    storage = get_storage()
    provider = get_provider()

    project = Project(status="queued", user_style_notes=style_notes)
    session.add(project)
    session.commit()
    session.refresh(project)

    original_key = f"local.input/{project.id}/original.png"
    storage.put(original_key, data, content_type=file.content_type)
    project.original_key = original_key
    session.add(project)
    session.commit()

    background_tasks.add_task(run_pipeline, project.id, provider, storage, style_notes)

    return ProjectCreateResponse(project_id=project.id)


@app.get("/api/projects/{project_id}", response_model=ProjectStatusResponse)
def get_project(project_id: str, session: Session = Depends(get_session)):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")

    storage = get_storage()
    images: dict[str, str | None] = {
        "original": storage.url(project.original_key) if project.original_key else None
    }
    for tier in TIERS:
        key = getattr(project, f"{tier}_key")
        images[tier] = storage.url(key) if key else None

    return ProjectStatusResponse(
        project_id=project.id,
        status=project.status,
        error=project.error,
        room_description=project.room_description,
        images=images,
    )
