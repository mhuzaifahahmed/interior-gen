import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session
from starlette.middleware.sessions import SessionMiddleware

from app.auth import (
    get_current_user,
    get_user_by_username_or_email,
    hash_password,
    require_user,
    validate_username,
    verify_password,
)
from app.config import settings
from app.db import get_session, init_db
from app.models import HouseProject, Project, User
from app.pipeline.generate import TIERS, run_pipeline
from app.pipeline.generate_house import run_house_pipeline
from app.pipeline.house_prompts import USER_PROMPT_MAX_CHARS
from app.pipeline.prompts import USER_NOTES_MAX_CHARS
from app.providers import get_provider
from app.schemas import (
    HouseProjectCreateResponse,
    HouseProjectStatusResponse,
    LoginRequest,
    ProjectCreateResponse,
    ProjectStatusResponse,
    SignupRequest,
    UserResponse,
)
from app.storage import get_storage

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Interior-Gen Backend", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.resolved_session_secret_key)

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


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id, username=user.username, email=user.email, full_name=user.full_name, role=user.role
    )


@app.post("/api/auth/signup", response_model=UserResponse)
def signup(body: SignupRequest, request: Request, session: Session = Depends(get_session)):
    try:
        username = validate_username(body.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required.")
    if len(body.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")

    if get_user_by_username_or_email(session, username) or get_user_by_username_or_email(session, email):
        raise HTTPException(409, "That username or email is already taken.")

    user = User(
        username=username,
        email=email,
        full_name=(body.full_name or "").strip() or None,
        role=(body.role or "").strip() or None,
        password_hash=hash_password(body.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    request.session["user_id"] = user.id
    return _user_response(user)


@app.post("/api/auth/login", response_model=UserResponse)
def login(body: LoginRequest, request: Request, session: Session = Depends(get_session)):
    user = get_user_by_username_or_email(session, body.identifier)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Incorrect username/email or password.")

    request.session["user_id"] = user.id
    return _user_response(user)


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserResponse)
def me(user: User = Depends(require_user)):
    return _user_response(user)


CITY_MAX_CHARS = 80


@app.post("/api/projects", response_model=ProjectCreateResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    style_notes: str = Form(""),
    city: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large (max 15MB)")

    # Defensive re-truncation (also enforced in build_prompt()) - the frontend's
    # <input maxlength> is trivially bypassable by anyone calling the API directly.
    style_notes = style_notes.strip()[:USER_NOTES_MAX_CHARS] or None
    # Empty city is a valid, deliberate choice (images-only path, confirmed via a
    # dialog on the frontend) - not an error, just no materials/pricing lookup.
    city = city.strip()[:CITY_MAX_CHARS] or None

    storage = get_storage()
    provider = get_provider()

    project = Project(status="queued", user_style_notes=style_notes, user_id=user.id)
    session.add(project)
    session.commit()
    session.refresh(project)

    # Namespaced by username (not just project id) so every user's uploads/
    # renders group under their own S3 prefix - see CLAUDE.md's "Authentication
    # & per-user storage" section for why (username is charset-validated at
    # signup, so it's always a safe path segment).
    original_key = f"users/{user.username}/input/{project.id}/original.png"
    storage.put(original_key, data, content_type=file.content_type)
    project.original_key = original_key
    session.add(project)
    session.commit()

    background_tasks.add_task(run_pipeline, project.id, provider, storage, style_notes, city, user.username)

    return ProjectCreateResponse(project_id=project.id)


@app.get("/api/projects/{project_id}", response_model=ProjectStatusResponse)
def get_project(
    project_id: str, session: Session = Depends(get_session), user: User = Depends(require_user)
):
    project = session.get(Project, project_id)
    # 404 (not 403) for both "doesn't exist" and "not yours" - doesn't let a
    # caller distinguish "wrong id" from "someone else's real project", so
    # project ids aren't enumerable across accounts.
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "project not found")

    storage = get_storage()
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
    )


@app.post("/api/house-projects", response_model=HouseProjectCreateResponse)
async def create_house_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    length: float | None = Form(None),
    width: float | None = Form(None),
    unit: str = Form("m"),
    prompt: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
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

    plot_image_key = f"users/{user.username}/input/{house_project.id}/plot.png"
    storage.put(plot_image_key, data, content_type=file.content_type)
    house_project.plot_image_key = plot_image_key
    session.add(house_project)
    session.commit()

    background_tasks.add_task(
        run_house_pipeline, house_project.id, provider, storage, dimensions, prompt, user.username
    )

    return HouseProjectCreateResponse(house_project_id=house_project.id)


@app.get("/api/house-projects/{house_project_id}", response_model=HouseProjectStatusResponse)
def get_house_project(
    house_project_id: str, session: Session = Depends(get_session), user: User = Depends(require_user)
):
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or house_project.user_id != user.id:
        raise HTTPException(404, "house project not found")

    storage = get_storage()
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
    )
