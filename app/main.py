import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.middleware.sessions import SessionMiddleware

from app import google_oauth
from app.auth import (
    derive_username_from_email,
    get_current_user,
    get_user_by_google_sub,
    get_user_by_username_or_email,
    hash_password,
    require_user,
    unusable_password_hash,
    validate_email,
    validate_username,
    verify_password,
)
from app.config import settings
from app.db import get_session, init_db
from app.models import HouseProject, Project, User
from app.pipeline.generate import TIERS, run_pipeline
from app.pipeline.generate_house import run_house_pipeline
from app.pipeline.house_prompts import USER_PROMPT_MAX_CHARS
from app.pipeline.prompts import ADDITIONAL_INSTRUCTIONS_MAX_CHARS, COLOR_PALETTES, STYLE_OPTIONS
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
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Interior-Gen Backend", lifespan=lifespan)

# Cross-site cookie requirements ONLY when the frontend is on a different
# origin (settings.frontend_origin set - see its own comment in config.py):
# browsers reject SameSite=None cookies unless also Secure, so both flip
# together. Same-origin (frontend_origin blank - local dev, or this app
# serving its own static/) keeps the plain Lax/non-Secure defaults, since
# forcing Secure would break plain-HTTP local dev.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.resolved_session_secret_key,
    same_site="none" if settings.frontend_origin else "lax",
    https_only=bool(settings.frontend_origin),
)

# Added AFTER SessionMiddleware so it ends up OUTERMOST in the stack (each
# add_middleware call wraps the existing stack) - CORS needs to see and
# handle preflight OPTIONS requests before anything else runs. Exact origin
# only (never "*") + allow_credentials=True is what actually lets the
# session cookie survive a cross-site fetch() with credentials: "include"
# (see static/app.js's API_BASE) - browsers reject the combination of "*"
# with allow_credentials entirely, so this only activates for a real,
# specific configured frontend origin, never a blanket allow-all.
if settings.frontend_origin:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
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

    try:
        email = validate_email(body.email)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
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


@app.get("/api/auth/google/login")
def google_login(request: Request):
    """Redirects to Google's consent screen. Sits alongside password auth,
    not a replacement for it - see CLAUDE.md's "Authentication" section.
    """
    if not google_oauth.is_configured():
        raise HTTPException(503, "Google sign-in is not configured.")
    state = secrets.token_urlsafe(24)
    request.session["oauth_state"] = state
    return RedirectResponse(google_oauth.build_authorize_url(state))


@app.get("/api/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    session: Session = Depends(get_session),
):
    # A mismatched/missing state means this request didn't originate from our
    # own google_login() redirect (CSRF / replay) - fail closed, same as any
    # other error path below: back to /login with a flag, never a 500.
    expected_state = request.session.pop("oauth_state", None)
    if error or not code or not state or state != expected_state:
        return RedirectResponse("/login?error=google_auth_failed")

    try:
        access_token = google_oauth.exchange_code_for_token(code)
        userinfo = google_oauth.fetch_userinfo(access_token)
    except httpx.HTTPError:
        return RedirectResponse("/login?error=google_auth_failed")

    google_sub = userinfo.get("sub")
    email = (userinfo.get("email") or "").strip().lower()
    if not google_sub or not email:
        return RedirectResponse("/login?error=google_auth_failed")

    user = get_user_by_google_sub(session, google_sub)
    if user is None:
        # Find-or-create by email: a Google sign-in with an email that
        # already has a password account links to it (sets google_sub)
        # instead of creating a duplicate account for the same person.
        user = get_user_by_username_or_email(session, email)
        if user is not None:
            user.google_sub = google_sub
        else:
            user = User(
                username=derive_username_from_email(email, session),
                email=email,
                full_name=(userinfo.get("name") or "").strip() or None,
                password_hash=unusable_password_hash(),
                google_sub=google_sub,
            )
        session.add(user)
        session.commit()
        session.refresh(user)

    request.session["user_id"] = user.id
    # Redirect back to the frontend's own root when it's hosted separately
    # (settings.frontend_origin set - e.g. Vercel) - a bare "/" would resolve
    # relative to THIS backend's own host, landing the user back on Render's
    # copy of the frontend instead of the one they actually started on.
    # Same-origin (frontend_origin blank) keeps the plain relative redirect.
    return RedirectResponse(settings.frontend_origin or "/")


CITY_MAX_CHARS = 80


@app.post("/api/projects", response_model=ProjectCreateResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    interior_style: str = Form(""),
    color_palette: str = Form(""),
    additional_instructions: str = Form(""),
    city: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
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

    # Namespaced by username (not just project id) so every user's uploads/
    # renders group under their own S3 prefix - see CLAUDE.md's "Authentication
    # & per-user storage" section for why (username is charset-validated at
    # signup, so it's always a safe path segment).
    original_key = f"users/{user.username}/input/{project.id}/original.png"
    storage.put(original_key, data, content_type=file.content_type)
    project.original_key = original_key
    session.add(project)
    session.commit()

    # Best-effort: also mirror the chosen inputs into S3 next to the upload
    # itself (in addition to the SQLite row above), so a user's full input
    # history is browsable directly from their own S3 prefix, not just via
    # the DB. Never blocks/fails project creation if storage write hiccups -
    # the SQLite row remains the source of truth either way.
    metadata_key = f"users/{user.username}/input/{project.id}/metadata.json"
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
        user.username,
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
    project_id: str, session: Session = Depends(get_session), user: User = Depends(require_user)
):
    project = session.get(Project, project_id)
    # 404 (not 403) for both "doesn't exist" and "not yours" - doesn't let a
    # caller distinguish "wrong id" from "someone else's real project", so
    # project ids aren't enumerable across accounts.
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "project not found")

    return _project_to_response(project, get_storage())


@app.get("/api/projects", response_model=list[ProjectStatusResponse])
def list_projects(session: Session = Depends(get_session), user: User = Depends(require_user)):
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
    house_project_id: str, session: Session = Depends(get_session), user: User = Depends(require_user)
):
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or house_project.user_id != user.id:
        raise HTTPException(404, "house project not found")

    return _house_project_to_response(house_project, get_storage())


@app.get("/api/house-projects", response_model=list[HouseProjectStatusResponse])
def list_house_projects(session: Session = Depends(get_session), user: User = Depends(require_user)):
    """History dropdown - every Build a House project this user has ever
    created, newest first. Mirrors list_projects() above."""
    house_projects = session.exec(
        select(HouseProject).where(HouseProject.user_id == user.id).order_by(HouseProject.created_at.desc())
    ).all()
    storage = get_storage()
    return [_house_project_to_response(hp, storage) for hp in house_projects]
