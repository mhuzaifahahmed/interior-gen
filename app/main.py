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
    room_length: float | None = Form(None),
    room_width: float | None = Form(None),
    room_height: float | None = Form(None),
    dimension_unit: str = Form("ft"),
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

    # Optional user-supplied room measurements - see _compute_room_dimensions()'s
    # docstring. Length + width are both required for ANY of this to apply
    # (an area needs both); height is independently optional on top of that
    # (only unlocks wall-area/paint accuracy). Bad/nonsensical input (e.g.
    # negative or absurdly large) is silently ignored rather than erroring -
    # this is a "nice to have if given" field, not a required one, so it
    # degrades to the existing Gemini-estimate fallback exactly like omitting
    # it entirely.
    room_dimensions = _compute_room_dimensions(room_length, room_width, room_height, dimension_unit)

    storage = get_storage()
    provider = get_provider()

    project = Project(
        status="queued",
        interior_style=interior_style,
        color_palette=color_palette,
        additional_instructions=additional_instructions,
        user_id=user.id,
        room_dimensions_json=json.dumps(room_dimensions) if room_dimensions else None,
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
    original_key = f"users/{storage_namespace}/roomRedesign/input/{project.id}/original.png"
    storage.put(original_key, data, content_type=file.content_type)
    project.original_key = original_key
    session.add(project)
    session.commit()

    # Best-effort: also mirror the chosen inputs into S3 next to the upload
    # itself (in addition to the SQLite row above), so a user's full input
    # history is browsable directly from their own S3 prefix, not just via
    # the DB. Never blocks/fails project creation if storage write hiccups -
    # the SQLite row remains the source of truth either way.
    metadata_key = f"users/{storage_namespace}/roomRedesign/input/{project.id}/metadata.json"
    metadata = {
        "interior_style": interior_style,
        "color_palette": color_palette,
        "additional_instructions": additional_instructions,
        "city": city,
        "room_dimensions": room_dimensions,
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
        room_dimensions.get("area_sqft") if room_dimensions else None,
        room_dimensions.get("wall_area_sqft") if room_dimensions else None,
    )

    return ProjectCreateResponse(project_id=project.id)


# Sanity bounds for a real-world room, in feet (after unit conversion) - not a
# strict validation contract, just enough to reject obvious garbage (0,
# negative, or absurd values from a mistyped unit) without erroring the whole
# request. Anything outside these bounds is silently dropped, same
# "best-effort, degrade to the existing fallback" treatment as a missing value.
_MIN_ROOM_DIMENSION_FT = 1.0
_MAX_ROOM_DIMENSION_FT = 200.0


def _compute_room_dimensions(
    length: float | None, width: float | None, height: float | None, unit: str
) -> dict | None:
    """Turns optional user-supplied Length x Width (x Height) + unit into a
    dict of {"length", "width", "height", "unit", "area_sqft", "wall_area_sqft"}
    - the AUTHORITATIVE materials-pricing quantity when present, replacing
    provider.estimate_room_area()'s Gemini vision guess (see run_pipeline()).

    Length AND width are both required (an area needs both) - if either is
    missing, returns None and the pipeline falls back to the existing
    Gemini-vision estimate exactly as if this feature didn't exist. Height is
    independently optional ON TOP of that: given, it additionally computes
    wall_area_sqft (2*(L+W)*H, a rectangular-room assumption) which feeds
    Paint/wall-finish's own quantity rule in generate_materials(); omitted,
    wall_area_sqft is None and Paint keeps behaving as it does today (no wall
    area at all, area_block's Flooring/Ceiling/Paint sqft comes from
    area_sqft only - see gemini.py's generate_materials()).

    unit is "ft" (default) or "m" - meters are converted to feet since every
    other sqft/pricing calculation in this codebase (LIGHT_FIXTURE_COVERAGE_SQFT,
    the materials prompt's "square feet" wording) already assumes feet.
    """
    if length is None or width is None:
        return None

    unit = unit if unit in ("ft", "m") else "ft"
    factor = 3.28084 if unit == "m" else 1.0
    length_ft = length * factor
    width_ft = width * factor
    height_ft = height * factor if height is not None else None

    for value in (length_ft, width_ft, *([height_ft] if height_ft is not None else [])):
        if not (_MIN_ROOM_DIMENSION_FT <= value <= _MAX_ROOM_DIMENSION_FT):
            return None

    result = {
        "length": length,
        "width": width,
        "height": height,
        "unit": unit,
        "area_sqft": round(length_ft * width_ft, 1),
        "wall_area_sqft": round(2 * (length_ft + width_ft) * height_ft, 1) if height_ft else None,
    }
    return result


def _project_to_response(project: Project, storage) -> ProjectStatusResponse:
    images: dict[str, str | None] = {
        "original": storage.url(project.original_key) if project.original_key else None
    }
    for tier in TIERS:
        key = getattr(project, f"{tier}_key")
        images[tier] = storage.url(key) if key else None

    materials = json.loads(project.materials_json) if project.materials_json else None
    room_dimensions = json.loads(project.room_dimensions_json) if project.room_dimensions_json else None

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
        room_dimensions=room_dimensions,
        image_model=project.image_model,
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


@app.post("/api/projects/{project_id}/cancel", response_model=ProjectStatusResponse)
def cancel_project(
    project_id: str, session: Session = Depends(get_session), user: AuthUser = Depends(require_user)
):
    """Best-effort cancel-and-discard for an in-progress generation (the
    "Cancel generating..." button on the progress screen). Generation itself
    is a server-side BackgroundTask that can't be killed mid-request (an
    image call already in flight finishes regardless) - this only flips
    `status` to "cancelled", which run_pipeline/run_house_pipeline poll for
    at the next stage boundary (see their `_is_cancelled()` checks) and stop
    at, discarding whatever partial result exists. Idempotent and a no-op on
    an already-terminal project (done/failed/cancelled) - only queued/running
    projects actually change state. Same 404-not-403 owner scoping as
    get_project.
    """
    project = session.get(Project, project_id)
    if project is None or project.user_id != user.id:
        raise HTTPException(404, "project not found")

    if project.status in ("queued", "running"):
        project.status = "cancelled"
        session.add(project)
        session.commit()

    return _project_to_response(project, get_storage())


@app.get("/api/projects", response_model=list[ProjectStatusResponse])
def list_projects(session: Session = Depends(get_session), user: AuthUser = Depends(require_user)):
    """History dropdown (static/app.js's history modal) - every Room Redesign
    project this user has ever created, newest first. Same 404-not-403 owner
    scoping as get_project applies implicitly here since the query is already
    filtered to user.id - there's no way to see another user's rows at all.
    Cancelled projects are excluded - a user explicitly discarded them, so
    they shouldn't reappear in history (see cancel_project's docstring).
    """
    projects = session.exec(
        select(Project)
        .where(Project.user_id == user.id, Project.status != "cancelled")
        .order_by(Project.created_at.desc())
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
    floor_count: int | None = Form(None),
    bedrooms: int | None = Form(None),
    bathrooms: int | None = Form(None),
    extras: str = Form(""),
    session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    """Mirrors create_project()'s validate -> create-row -> commit ->
    upload-original -> commit -> background_tasks.add_task shape. length/width
    are optional (a plot photo alone is still a valid, if less useful,
    submission) - dimensions.json degrades to an empty dict rather than
    rejecting the request.

    floor_count/bedrooms/bathrooms/extras are the structured inputs that
    replaced the old single free-text `prompt` field (see static/index.html's
    "Plot Parameters" panel) - _compose_house_requirements() turns them into a
    natural-language requirements string, stored as `prompt` for full
    backward compatibility with every downstream consumer (run_house_pipeline,
    build_house_prompt, meta_json, etc. all still just read `prompt`). Things
    like a garage or a kitchen on every floor are NOT their own dedicated
    fields - a dedicated Garage/Kitchen-each-floor checkbox pair was tried and
    dropped (felt like an arbitrarily incomplete amenities list next to the
    real dropdowns) - a user just types them into `extras` like any other
    requirement. The raw `prompt` Form field is kept as a fallback for direct
    API callers that don't supply any structured field at all - a request
    with neither is simply "no specific requirements", same as before this
    feature existed.
    """
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(400, f"unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "file too large (max 15MB)")

    # Sanity-clamp, same "silently correct obvious nonsense rather than
    # error the whole request" treatment as _compute_room_dimensions() above.
    # 1-10 floors matches the existing regex-guess's own clamp
    # (app/providers/gemini.py's _explicit_floor_count) so behavior stays
    # consistent whichever path derives the count.
    if floor_count is not None:
        floor_count = max(1, min(floor_count, 10))
    if bedrooms is not None:
        bedrooms = max(0, min(bedrooms, 20))
    if bathrooms is not None:
        bathrooms = max(0, min(bathrooms, 20))
    extras = extras.strip()[:USER_PROMPT_MAX_CHARS]

    house_inputs = {
        "floor_count": floor_count,
        "bedrooms": bedrooms,
        "bathrooms": bathrooms,
        "extras": extras or None,
    }
    composed_requirements = _compose_house_requirements(floor_count, bedrooms, bathrooms, extras)
    # Defensive re-truncation, same reasoning as style_notes/city above - the
    # frontend's <input maxlength> is trivially bypassable by a direct API call.
    # Falls back to the raw legacy `prompt` field only when NO structured
    # input was given at all (a direct API call that predates this feature).
    prompt = composed_requirements or (prompt.strip()[:USER_PROMPT_MAX_CHARS] or None)

    dimensions: dict = {}
    if length and width:
        dimensions = {"length": length, "width": width, "unit": unit.strip()[:10]}

    storage = get_storage()
    provider = get_provider()

    house_project = HouseProject(
        status="queued",
        dimensions_json=json.dumps(dimensions),
        prompt=prompt,
        user_id=user.id,
        house_inputs_json=json.dumps(house_inputs),
    )
    session.add(house_project)
    session.commit()
    session.refresh(house_project)

    storage_namespace = _storage_namespace(user.id, display_name)
    plot_image_key = f"users/{storage_namespace}/buildAHouse/input/{house_project.id}/plot.png"
    storage.put(plot_image_key, data, content_type=file.content_type)
    house_project.plot_image_key = plot_image_key
    session.add(house_project)
    session.commit()

    # Best-effort: mirror the chosen inputs into S3 next to the plot upload,
    # same parity/reasoning as Room Redesign's input metadata.json above -
    # never blocks/fails house-project creation if storage write hiccups.
    house_metadata_key = f"users/{storage_namespace}/buildAHouse/input/{house_project.id}/metadata.json"
    house_metadata = {"dimensions": dimensions, "house_inputs": house_inputs}
    try:
        storage.put(
            house_metadata_key, json.dumps(house_metadata).encode("utf-8"), content_type="application/json"
        )
    except Exception:
        logger.exception(
            "failed to write input metadata.json for house project %s - continuing", house_project.id
        )

    background_tasks.add_task(
        run_house_pipeline,
        house_project.id,
        provider,
        storage,
        dimensions,
        prompt,
        storage_namespace,
        floor_count,
    )

    return HouseProjectCreateResponse(house_project_id=house_project.id)


def _compose_house_requirements(
    floor_count: int | None,
    bedrooms: int | None,
    bathrooms: int | None,
    extras: str,
) -> str | None:
    """Turns the structured "Plot Parameters" selections into one natural-
    language requirements sentence, stored as HouseProject.prompt (see
    create_house_project's docstring for why that column is reused rather
    than added-to). Deliberately includes the literal word "floor(s)" next
    to the number (e.g. "2 floors") - app/pipeline/house_prompts.py's
    _resolve_floor_count() falls back to regex-parsing this exact text
    pattern when room_layout is unavailable (e.g. the blueprint step
    failed), so this composed sentence must stay parseable by that regex
    even though floor_count is ALSO passed as a real int elsewhere
    (run_house_pipeline -> generate_room_layout) - belt and suspenders,
    not redundant.

    Only 3 structured dropdowns feed this (floors/bedrooms/bathrooms) - a
    Garage + Kitchen-on-every-floor checkbox pair was tried and dropped (felt
    like an arbitrary, incomplete amenities list sitting next to real
    dropdowns); anything beyond the 3 counts is just free text in `extras`.

    Returns None (not "") when every field is empty - the pipeline already
    treats an empty/None prompt as "no specific requirements", identical to
    today's behavior when a user left the old free-text field blank.
    """
    parts = []
    if floor_count:
        parts.append(f"{floor_count} floor{'s' if floor_count != 1 else ''}")
    if bedrooms:
        parts.append(f"{bedrooms} bedroom{'s' if bedrooms != 1 else ''}")
    if bathrooms:
        parts.append(f"{bathrooms} bathroom{'s' if bathrooms != 1 else ''}")

    requirements = ", ".join(parts)
    if extras:
        requirements = f"{requirements}. Extras: {extras}" if requirements else extras

    return requirements or None


def _house_project_to_response(house_project: HouseProject, storage) -> HouseProjectStatusResponse:
    images: dict[str, str | None] = {
        "plot": storage.url(house_project.plot_image_key) if house_project.plot_image_key else None,
        "render": storage.url(house_project.render_key) if house_project.render_key else None,
        "floor_plan": storage.url(house_project.floor_plan_key) if house_project.floor_plan_key else None,
    }

    blueprint_keys = json.loads(house_project.blueprint_keys_json) if house_project.blueprint_keys_json else []
    blueprint_urls = [storage.url(key) for key in blueprint_keys]

    blueprint_dxf_keys = (
        json.loads(house_project.blueprint_dxf_keys_json) if house_project.blueprint_dxf_keys_json else []
    )
    blueprint_dxf_urls = [storage.url(key) for key in blueprint_dxf_keys]

    floor_plan_keys = (
        json.loads(house_project.floor_plan_keys_json) if house_project.floor_plan_keys_json else []
    )
    floor_plan_urls = [storage.url(key) for key in floor_plan_keys]

    dimensions = json.loads(house_project.dimensions_json) if house_project.dimensions_json else None
    house_inputs = json.loads(house_project.house_inputs_json) if house_project.house_inputs_json else None

    return HouseProjectStatusResponse(
        house_project_id=house_project.id,
        status=house_project.status,
        error=house_project.error,
        plot_description=house_project.plot_description,
        images=images,
        floor_plan_status=house_project.floor_plan_status,
        floor_plan_urls=floor_plan_urls,
        blueprint_status=house_project.blueprint_status,
        blueprint_urls=blueprint_urls,
        blueprint_dxf_urls=blueprint_dxf_urls,
        created_at=house_project.created_at.isoformat(),
        prompt=house_project.prompt,
        dimensions=dimensions,
        house_inputs=house_inputs,
        render_model=house_project.render_model,
    )


@app.get("/api/house-projects/{house_project_id}", response_model=HouseProjectStatusResponse)
def get_house_project(
    house_project_id: str, session: Session = Depends(get_session), user: AuthUser = Depends(require_user)
):
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or house_project.user_id != user.id:
        raise HTTPException(404, "house project not found")

    return _house_project_to_response(house_project, get_storage())


@app.post("/api/house-projects/{house_project_id}/cancel", response_model=HouseProjectStatusResponse)
def cancel_house_project(
    house_project_id: str, session: Session = Depends(get_session), user: AuthUser = Depends(require_user)
):
    """Mirrors cancel_project() above - see its docstring for the full
    best-effort cancel-and-discard contract."""
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or house_project.user_id != user.id:
        raise HTTPException(404, "house project not found")

    if house_project.status in ("queued", "running"):
        house_project.status = "cancelled"
        session.add(house_project)
        session.commit()

    return _house_project_to_response(house_project, get_storage())


@app.get("/api/house-projects", response_model=list[HouseProjectStatusResponse])
def list_house_projects(session: Session = Depends(get_session), user: AuthUser = Depends(require_user)):
    """History dropdown - every Build a House project this user has ever
    created, newest first. Mirrors list_projects() above. Cancelled projects
    are excluded (see cancel_house_project's docstring)."""
    house_projects = session.exec(
        select(HouseProject)
        .where(HouseProject.user_id == user.id, HouseProject.status != "cancelled")
        .order_by(HouseProject.created_at.desc())
    ).all()
    storage = get_storage()
    return [_house_project_to_response(hp, storage) for hp in house_projects]
