import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select

from app.auth import AuthUser, require_user
from app.config import settings
from app.db import get_session, init_db
from app.models import HouseProject, Project
from app.pipeline.generate import TIERS, run_pipeline
from app.pipeline.generate_house import run_house_pipeline
from app.pipeline.house_prompts import USER_PROMPT_MAX_CHARS
from app.pipeline.prompts import ADDITIONAL_INSTRUCTIONS_MAX_CHARS, COLOR_PALETTES, STYLE_OPTIONS
from app.providers import get_provider
from app.schemas import (
    HouseProjectCreateResponse,
    HouseProjectStatusResponse,
    ProjectCreateResponse,
    ProjectStatusResponse,
)
from app.storage import get_storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Interior-Gen Backend", lifespan=lifespan)

# No more session cookies/SessionMiddleware since the Clerk migration - auth
# is a plain `Authorization: Bearer <clerk-jwt>` header now (see
# app/auth.py), which isn't subject to cookie SameSite/cross-site rules at
# all. CORS is kept as a defensive fallback for direct API calls that don't
# go through the Vercel proxy (vercel.json) - allow_credentials stays off
# since Bearer auth carries no cookie for the browser to need permission to
# send.
if settings.frontend_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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


@app.get("/login")
def login_page():
    from fastapi.responses import FileResponse

    return FileResponse("static/login.html")


@app.get("/signup")
def signup_page():
    from fastapi.responses import FileResponse

    return FileResponse("static/signup.html")


@app.get("/terms")
def terms():
    from fastapi.responses import FileResponse

    return FileResponse("static/terms.html")


@app.get("/privacy")
def privacy():
    from fastapi.responses import FileResponse

    return FileResponse("static/privacy.html")


CITY_MAX_CHARS = 80
DISPLAY_NAME_MAX_CHARS = 40
_DISPLAY_NAME_UNSAFE_CHARS = re.compile(r"[^a-z0-9]+")


def _storage_namespace(user_id: str, display_name: str) -> str:
    """Folder-friendly S3 prefix for a user's uploads/renders - the Clerk
    user id (e.g. "user_2abc...") stays the authoritative, stable part (so
    existing uploads are never orphaned even if the person's display name
    later changes), with a sanitized human-readable label appended so
    browsing the bucket doesn't mean cross-referencing opaque Clerk ids
    against the Clerk dashboard every time. `display_name` comes straight
    from Clerk's own client-side profile (app.js's currentUserDisplayName())
    - not re-verified server-side since it's only ever used for a folder
    label, never for auth/ownership (that's still user_id, compared exactly
    elsewhere in this file). Falls back to the bare id if no usable name was
    supplied (e.g. a brand-new Clerk profile with no name/email loaded yet).
    """
    label = _DISPLAY_NAME_UNSAFE_CHARS.sub("_", display_name.strip().lower()).strip("_")
    label = label[:DISPLAY_NAME_MAX_CHARS]
    return f"{user_id}_{label}" if label else user_id


@app.post("/api/projects", response_model=ProjectCreateResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    interior_style: str = Form(""),
    color_palette: str = Form(""),
    additional_instructions: str = Form(""),
    city: str = Form(""),
    display_name: str = Form(""),
    session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large (max 15MB)")

    # Interior Style + Color Palette are REQUIRED selections (the frontend's
    # Generate button is disabled until both are picked) - re-validated here
    # since a client could call the API directly bypassing that gating. Returned
    # as a 400, not FastAPI's default 422, matching this endpoint's other manual
    # validation below.
    if interior_style not in STYLE_OPTIONS:
        raise HTTPException(400, "interior_style must be one of the supported styles")
    if color_palette not in COLOR_PALETTES:
        raise HTTPException(400, "color_palette must be one of the supported palettes")

    # Defensive re-truncation (also enforced in build_prompt()) - the frontend's
    # <textarea maxlength> is trivially bypassable by anyone calling the API directly.
    additional_instructions = additional_instructions.strip()[:ADDITIONAL_INSTRUCTIONS_MAX_CHARS] or None
    # Empty city is a valid, deliberate choice (images-only path, confirmed via a
    # dialog on the frontend) - not an error, just no materials/pricing lookup.
    city = city.strip()[:CITY_MAX_CHARS] or None

    storage = get_storage()
    provider = get_provider()

    project = Project(
        status="queued",
        interior_style=interior_style,
        color_palette=color_palette,
        additional_instructions=additional_instructions,
        user_id=user.id,
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    # Namespaced by the Clerk user id, optionally with a human-readable label
    # appended (see _storage_namespace) so every user's uploads/renders group
    # under their own S3 prefix, and that prefix is actually identifiable by
    # name when browsing the bucket - see CLAUDE.md's "Authentication"
    # section.
    storage_namespace = _storage_namespace(user.id, display_name)
    original_key = f"users/{storage_namespace}/input/{project.id}/original.png"
    storage.put(original_key, data, content_type=file.content_type)
    project.original_key = original_key
    session.add(project)
    session.commit()

    # Best-effort: also mirror the chosen inputs into S3 next to the upload
    # itself (in addition to the SQLite row above), so a user's full input
    # history is browsable directly from their own S3 prefix, not just via
    # the DB. Never blocks/fails project creation if storage write hiccups -
    # the SQLite row remains the source of truth either way.
    metadata_key = f"users/{storage_namespace}/input/{project.id}/metadata.json"
    metadata = {
        "interior_style": interior_style,
        "color_palette": color_palette,
        "additional_instructions": additional_instructions,
        "city": city,
    }
    try:
        storage.put(metadata_key, json.dumps(metadata).encode("utf-8"), content_type="application/json")
    except Exception:
        logger.exception("failed to write input metadata.json for project %s - continuing", project.id)

    background_tasks.add_task(
        run_pipeline,
        project.id,
        provider,
        storage,
        interior_style,
        color_palette,
        additional_instructions,
        city,
        storage_namespace,
    )

    return ProjectCreateResponse(project_id=project.id)


def _project_to_response(project: Project, storage) -> ProjectStatusResponse:
    images: dict[str, str | None] = {
        "original": storage.url(project.original_key) if project.original_key else None
    }
    for tier in TIERS:
        key = getattr(project, f"{tier}_key")
        images[tier] = storage.url(key) if key else None

    materials = json.loads(project.materials_json) if project.materials_json else None

    return ProjectStatusResponse(
        project_id=project.id,
        status=project.status,
        error=project.error,
        room_description=project.room_description,
        images=images,
        materials=materials,
        materials_status=project.materials_status,
        created_at=project.created_at.isoformat(),
        city=project.city,
        interior_style=project.interior_style,
        color_palette=project.color_palette,
        additional_instructions=project.additional_instructions,
    )


@app.get("/api/projects/{project_id}", response_model=ProjectStatusResponse)
def get_project(
    project_id: str, session: Session = Depends(get_session), user: AuthUser = Depends(require_user)
):
    project = session.get(Project, project_id)
    # 404 (not 403) for both "doesn't exist" and "not yours" - doesn't let a
    # caller distinguish "wrong id" from "someone else's real project", so
    # project ids aren't enumerable across accounts.
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "project not found")

    return _project_to_response(project, get_storage())


@app.get("/api/projects", response_model=list[ProjectStatusResponse])
def list_projects(session: Session = Depends(get_session), user: AuthUser = Depends(require_user)):
    """History dropdown (static/app.js's history modal) - every Room Redesign
    project this user has ever created, newest first. Same 404-not-403 owner
    scoping as get_project applies implicitly here since the query is already
    filtered to user.id - there's no way to see another user's rows at all.
    """
    projects = session.exec(
        select(Project).where(Project.user_id == user.id).order_by(Project.created_at.desc())
    ).all()
    storage = get_storage()
    return [_project_to_response(p, storage) for p in projects]


@app.post("/api/house-projects", response_model=HouseProjectCreateResponse)
async def create_house_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    length: float | None = Form(None),
    width: float | None = Form(None),
    unit: str = Form("m"),
    prompt: str = Form(""),
    display_name: str = Form(""),
    session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    """Mirrors create_project()'s validate -> create-row -> commit ->
    upload-original -> commit -> background_tasks.add_task shape. length/width
    are optional (a plot photo alone is still a valid, if less useful,
    submission) - dimensions.json degrades to an empty dict rather than
    rejecting the request.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large (max 15MB)")

    # Defensive re-truncation, same reasoning as style_notes/city above - the
    # frontend's <input maxlength> is trivially bypassable by a direct API call.
    prompt = prompt.strip()[:USER_PROMPT_MAX_CHARS] or None

    dimensions: dict = {}
    if length and width:
        dimensions = {"length": length, "width": width, "unit": unit.strip()[:10]}

    storage = get_storage()
    provider = get_provider()

    house_project = HouseProject(
        status="queued", dimensions_json=json.dumps(dimensions), prompt=prompt, user_id=user.id
    )
    session.add(house_project)
    session.commit()
    session.refresh(house_project)

    storage_namespace = _storage_namespace(user.id, display_name)
    plot_image_key = f"users/{storage_namespace}/input/{house_project.id}/plot.png"
    storage.put(plot_image_key, data, content_type=file.content_type)
    house_project.plot_image_key = plot_image_key
    session.add(house_project)
    session.commit()

    background_tasks.add_task(
        run_house_pipeline, house_project.id, provider, storage, dimensions, prompt, storage_namespace
    )

    return HouseProjectCreateResponse(house_project_id=house_project.id)


def _house_project_to_response(house_project: HouseProject, storage) -> HouseProjectStatusResponse:
    images: dict[str, str | None] = {
        "plot": storage.url(house_project.plot_image_key) if house_project.plot_image_key else None,
        "render": storage.url(house_project.render_key) if house_project.render_key else None,
        "floor_plan": storage.url(house_project.floor_plan_key) if house_project.floor_plan_key else None,
    }

    blueprint_keys = json.loads(house_project.blueprint_keys_json) if house_project.blueprint_keys_json else []
    blueprint_urls = [storage.url(key) for key in blueprint_keys]

    return HouseProjectStatusResponse(
        house_project_id=house_project.id,
        status=house_project.status,
        error=house_project.error,
        plot_description=house_project.plot_description,
        images=images,
        floor_plan_status=house_project.floor_plan_status,
        blueprint_status=house_project.blueprint_status,
        blueprint_urls=blueprint_urls,
        created_at=house_project.created_at.isoformat(),
        prompt=house_project.prompt,
    )


@app.get("/api/house-projects/{house_project_id}", response_model=HouseProjectStatusResponse)
def get_house_project(
    house_project_id: str, session: Session = Depends(get_session), user: AuthUser = Depends(require_user)
):
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or house_project.user_id != user.id:
        raise HTTPException(404, "house project not found")

    return _house_project_to_response(house_project, get_storage())


@app.get("/api/house-projects", response_model=list[HouseProjectStatusResponse])
def list_house_projects(session: Session = Depends(get_session), user: AuthUser = Depends(require_user)):
    """History dropdown - every Build a House project this user has ever
    created, newest first. Mirrors list_projects() above."""
    house_projects = session.exec(
        select(HouseProject).where(HouseProject.user_id == user.id).order_by(HouseProject.created_at.desc())
    ).all()
    storage = get_storage()
    return [_house_project_to_response(hp, storage) for hp in house_projects]
