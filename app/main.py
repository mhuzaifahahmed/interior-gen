import json
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Response, UploadFile, File
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request
from svix.webhooks import Webhook, WebhookVerificationError

from app.auth import AuthUser, get_current_user, require_admin, require_user
from app.config import settings
from app.db import get_session, init_db
from app.models import HouseProject, Project
from app.pipeline.generate import TIERS, run_pipeline
from app.pipeline.generate_house import run_house_pipeline
from app.pipeline.house_prompts import USER_PROMPT_MAX_CHARS
from app.pipeline.prompts import ADDITIONAL_INSTRUCTIONS_MAX_CHARS, COLOR_PALETTES, STYLE_OPTIONS
from app.plans import (
    QuotaExceededError,
    backend_bucket,
    capture_identity,
    consume_quota,
    get_or_create_user_plan,
    list_all_user_plans,
    plan_status,
    reset_usage,
    resolve_preferred_backend,
    set_plan,
)
from app.plans import VALID_PLANS
from app.providers import get_provider
from app.schemas import (
    AdminSetPlanRequest,
    AdminUserRow,
    AdminUsersResponse,
    HouseProjectCreateResponse,
    HouseProjectStatusResponse,
    PlanStatusResponse,
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


@app.exception_handler(StarletteHTTPException)
async def custom_404_handler(request: Request, exc: StarletteHTTPException):
    """Only real page navigations get the styled static/404.html - API
    callers (/api/...) still get FastAPI's normal {"detail": ...} JSON (a
    frontend fetch() checking res.ok/res.status must not have to parse HTML),
    and /static or /media misses stay plain 404s too (those are asset
    requests, not page navigations, so a full HTML page for a missing image
    would be actively wrong)."""
    if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/static/", "/media/")):
        return FileResponse("static/404.html", status_code=404)
    return await http_exception_handler(request, exc)


@app.get("/")
def index():
    return FileResponse("static/index.html")


@app.get("/login")
def login_page():
    return FileResponse("static/login.html")


@app.get("/signup")
def signup_page():
    return FileResponse("static/signup.html")


@app.get("/terms")
def terms():
    return FileResponse("static/terms.html")


@app.get("/privacy")
def privacy():
    return FileResponse("static/privacy.html")


@app.get("/admin")
def admin_page():
    # Serves the page unauthenticated, same as /login /signup /terms /privacy
    # above - the real gate is require_admin() on every /api/admin/* call the
    # page makes; this route just returns markup that will show "access
    # denied" client-side for anyone not on settings.admin_user_id_set.
    return FileResponse("static/admin.html")


CITY_MAX_CHARS = 80
DISPLAY_NAME_MAX_CHARS = 40
_DISPLAY_NAME_UNSAFE_CHARS = re.compile(r"[^a-z0-9]+")


def _utc_isoformat(dt: datetime) -> str:
    """Real bug fixed here (2026-09-04): every `created_at` is written with
    `datetime.now(timezone.utc)` (see app/models.py's `_now()`), but SQLite
    silently drops the tzinfo on round-trip - a plain `dt.isoformat()` on the
    value read back from the DB produces a string with NO 'Z'/offset suffix
    (confirmed live: `datetime.datetime(2026, 9, 4, 6, 18, 18, ...)`,
    `tzinfo=None`). The frontend's `new Date(iso)` then misinterprets that
    ambiguous string as the BROWSER's own local time instead of UTC, before
    static/app.js's formatHistoryDate() converts "as if UTC" to Asia/Karachi
    - compounding into a wrong displayed time. Since every value stored here
    is UTC by construction regardless of what the DB gives back, a naive
    datetime is safely reattached to UTC before formatting - this doesn't
    change the underlying moment in time, only makes it unambiguous once
    serialized."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


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


def _write_account_json(storage, storage_namespace: str, plan_row) -> None:
    """Best-effort, additive S3 write: a small identity+plan summary at the
    user's folder ROOT (users/{namespace}/account.json), sitting above the
    roomRedesign/buildAHouse subfolders - so browsing a person's bucket
    prefix immediately shows who they are and what plan/usage they're on,
    next to everything they've generated. Called from both generator
    endpoints (after a fresh generation) and the admin plan-change endpoint
    (so an upgrade is reflected at rest too). Never blocks/fails the calling
    request - same posture as the existing per-project metadata.json writes.
    Deliberately does NOT rename/move any existing keys - every
    Project/HouseProject row already stores its own exact keys at creation
    time, so this is purely additive.
    """
    account = {
        "user_id": plan_row.user_id,
        "email": plan_row.email,
        "display_name": plan_row.display_name,
        "plan": plan_row.plan,
        "plan_updated_at": _utc_isoformat(plan_row.plan_updated_at),
        "lifetime_generations": plan_row.lifetime_generations,
        "room_kaggle_used_this_window": plan_row.room_kaggle_used,
        "room_openai_used_this_window": plan_row.room_openai_used,
        "house_kaggle_used_this_window": plan_row.house_kaggle_used,
        "house_openai_used_this_window": plan_row.house_openai_used,
        "first_seen": _utc_isoformat(plan_row.created_at),
        "last_updated": _utc_isoformat(datetime.now(timezone.utc)),
    }
    try:
        storage.put(
            f"users/{storage_namespace}/account.json",
            json.dumps(account).encode("utf-8"),
            content_type="application/json",
        )
    except Exception:
        logger.exception("failed to write account.json for user %s - continuing", plan_row.user_id)


# Shared S3 prefix for every anonymous (pre-login) generation - see
# _consume_anonymous_trial() below. Deliberately ONE fixed namespace, not a
# per-visitor id: these are one-off trial generations with no History/
# ownership feature attached, so there's no need to distinguish one
# anonymous visitor's S3 objects from another's the way a real per-user
# namespace does.
ANONYMOUS_STORAGE_NAMESPACE = "anonymous"

# Pre-login trial cookie (future-plans/subscription-and-access-roadmap.md):
# 1 free Room + 1 free House generation before login is required, tracked by
# a plain browser cookie - the user's own explicit choice over IP/fingerprint
# tracking (simple, no new infra, accepted as trivially bypassable via
# clearing cookies/private browsing in exchange for zero friction - matches
# this app's existing localStorage-based low-friction conventions
# elsewhere). A long max-age (1 year) since the whole point is "used once,
# ever", not a session-scoped allowance.
_ANON_TRIAL_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365


def _anon_trial_cookie_name(kind: str) -> str:
    return f"ig_anon_trial_{kind}"


def _owns_project(project_user_id: str | None, user: AuthUser | None) -> bool:
    """Ownership check that also covers anonymous (pre-login trial) projects,
    which have user_id=None. An anonymous caller (also `user=None`, since
    static/app.js's authFetch() sends no Authorization header when nobody's
    logged in) can view/cancel an anonymous project - there's no per-visitor
    id to scope by (see ANONYMOUS_STORAGE_NAMESPACE's comment), so this is a
    deliberately coarse "any anonymous caller can see any anonymous project"
    check, same low-stakes/low-security posture already accepted for the
    cookie-based trial gate itself. A real project (user_id set) still
    requires an exact Clerk id match, unchanged from before this feature."""
    return project_user_id == (user.id if user else None)


def _consume_anonymous_trial(request: Request, response: Response, kind: str) -> None:
    """Called only when there's no logged-in user at all (create_project/
    create_house_project's `user` dependency resolved to None). Raises 401
    if this browser's pre-login trial for `kind` ("room"/"house") was already
    used - the SAME status code the frontend already handles for "you need to
    log in" (see static/app.js's savePendingGeneration() + redirect-to-login
    flow on a 401 from these endpoints), so no new frontend error-handling
    path was needed for this case. Otherwise marks the trial used via the
    cookie and lets the request proceed anonymously.
    """
    cookie_name = _anon_trial_cookie_name(kind)
    if request.cookies.get(cookie_name) == "used":
        raise HTTPException(401, "Free trial already used - please log in to continue.")
    response.set_cookie(
        cookie_name,
        "used",
        max_age=_ANON_TRIAL_COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
    )


@app.get("/api/plan", response_model=PlanStatusResponse)
def get_plan(
    email: str = "",
    name: str = "",
    session: Session = Depends(get_session),
    user: AuthUser = Depends(require_user),
):
    """Current user's subscription plan + rolling quota usage - see
    app/plans.py. Plan changes now happen via the admin panel
    (/api/admin/users/{id}/plan) - a real, human-operated stopgap until a
    payment processor is chosen.

    email/name are optional, client-supplied (from Clerk.user - the backend
    has no other way to learn either, see AuthUser's docstring) - captured
    best-effort onto the user's UserPlan row so the admin panel can find them
    by email. This runs on every authenticated page load
    (static/app.js's fetchPlanStatus()), so most logged-in users' rows fill
    in quickly even if they never generate anything."""
    capture_identity(session, user.id, email=email, display_name=name)
    return PlanStatusResponse(**plan_status(session, user.id))


def _admin_row(session: Session, plan_row) -> AdminUserRow:
    status = plan_status(session, plan_row.user_id)
    return AdminUserRow(
        user_id=plan_row.user_id,
        email=plan_row.email,
        display_name=plan_row.display_name,
        plan=status["plan"],
        plan_updated_at=plan_row.plan_updated_at,
        created_at=plan_row.created_at,
        lifetime_generations=plan_row.lifetime_generations,
        quotas=status["quotas"],
        used=status["used"],
        remaining=status["remaining"],
        quota_window_start=status["quota_window_start"],
        quota_window_reset_at=status["quota_window_reset_at"],
    )


@app.get("/api/admin/users", response_model=AdminUsersResponse)
def admin_list_users(session: Session = Depends(get_session), _admin: AuthUser = Depends(require_admin)):
    """Every known user + their real plan/usage, for the admin panel's user
    table (static/admin.html). See app/auth.py's require_admin() for the
    access gate."""
    rows = list_all_user_plans(session)
    return AdminUsersResponse(users=[_admin_row(session, row) for row in rows])


@app.post("/api/admin/users/{user_id}/plan", response_model=AdminUserRow)
def admin_set_plan(
    user_id: str,
    body: AdminSetPlanRequest,
    session: Session = Depends(get_session),
    _admin: AuthUser = Depends(require_admin),
):
    if body.plan not in VALID_PLANS:
        raise HTTPException(400, f"invalid plan {body.plan!r} - must be one of {VALID_PLANS}")
    plan_row = set_plan(session, user_id, body.plan)
    # Best-effort - reflect the new plan in the user's S3 account.json too,
    # so the bucket never shows a stale plan after an admin upgrade. Uses
    # whatever display_name is already on file (may be "" for a user who
    # never supplied one) - same _storage_namespace() convention as the
    # generator endpoints, so this always resolves to the SAME prefix their
    # uploads already live under.
    try:
        storage = get_storage()
        storage_namespace = _storage_namespace(plan_row.user_id, plan_row.display_name)
        _write_account_json(storage, storage_namespace, plan_row)
    except Exception:
        logger.exception("failed to refresh account.json after plan change for user %s", user_id)
    return _admin_row(session, plan_row)


@app.post("/api/admin/users/{user_id}/reset-usage", response_model=AdminUserRow)
def admin_reset_usage(
    user_id: str, session: Session = Depends(get_session), _admin: AuthUser = Depends(require_admin)
):
    plan_row = reset_usage(session, user_id)
    return _admin_row(session, plan_row)


@app.post("/api/webhooks/clerk")
async def clerk_webhook(request: Request, session: Session = Depends(get_session)):
    """Real-time counterpart to the lazy row-creation GET /api/plan already
    does - without this, a UserPlan row (and so a row in the admin panel)
    only appears the first time someone actually LOGS IN and the page loads
    while authenticated, never at signup itself (Clerk and this backend are
    two separate systems with no sync otherwise - a real, reported gap:
    someone signed up and was visible in Clerk's own dashboard but invisible
    here until they logged in). Subscribes to Clerk's "user.created" event
    so the row exists the moment someone signs up, before their first login.

    Deliberately NOT gated by require_user/require_admin - Clerk calls this
    endpoint directly, with no Authorization: Bearer header at all. Security
    comes entirely from the Svix signature (settings.clerk_webhook_secret,
    from the Clerk Dashboard's Webhooks page), verified against the RAW
    request body - not the re-serialized JSON, which would produce a
    different byte sequence and always fail verification.
    """
    body = await request.body()
    if not settings.clerk_webhook_secret:
        # Not configured yet - same "silently inert" treatment as this
        # project's other optional vendor slots (e.g. idealhouse.py) rather
        # than 500ing on every delivery Clerk retries.
        raise HTTPException(400, "Clerk webhook not configured")

    try:
        Webhook(settings.clerk_webhook_secret).verify(
            body,
            {
                "svix-id": request.headers.get("svix-id", ""),
                "svix-timestamp": request.headers.get("svix-timestamp", ""),
                "svix-signature": request.headers.get("svix-signature", ""),
            },
        )
    except WebhookVerificationError:
        raise HTTPException(400, "invalid webhook signature")

    payload = json.loads(body)
    if payload.get("type") != "user.created":
        # Only user.created is handled - any other subscribed/future event
        # type is a harmless no-op ack, not an error (Clerk retries on
        # non-2xx, which would otherwise hammer this endpoint pointlessly).
        return {"status": "ignored"}

    data = payload.get("data", {})
    user_id = data.get("id")
    if not user_id:
        return {"status": "ignored"}

    email_addresses = data.get("email_addresses") or []
    primary_id = data.get("primary_email_address_id")
    primary_email = next(
        (e.get("email_address") for e in email_addresses if e.get("id") == primary_id),
        email_addresses[0].get("email_address") if email_addresses else "",
    )
    display_name = " ".join(filter(None, [data.get("first_name"), data.get("last_name")])) or (
        data.get("username") or ""
    )

    capture_identity(session, user_id, email=primary_email or "", display_name=display_name)
    return {"status": "ok"}


@app.post("/api/projects", response_model=ProjectCreateResponse)
async def create_project(
    background_tasks: BackgroundTasks,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    interior_style: str = Form(""),
    color_palette: str = Form(""),
    additional_instructions: str = Form(""),
    city: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    room_length: float | None = Form(None),
    room_width: float | None = Form(None),
    room_height: float | None = Form(None),
    dimension_unit: str = Form("ft"),
    preferred_model: str | None = Form(None),
    session: Session = Depends(get_session),
    user: AuthUser | None = Depends(get_current_user),
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
    # 2026-09: the frontend no longer asks for a city at all (see
    # app/pipeline/generate.py's run_pipeline docstring for why - a
    # location-biased search never actually helped). city is kept accepted
    # here only for backward-compat direct API callers; an empty/omitted
    # value is NOT special-cased any more - materials/pricing always runs
    # regardless of whether one is given.
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

    # Access gate - after every cheap/free validation above (so a request
    # that was going to 400 anyway never burns a generation), but before any
    # real storage/pipeline cost is incurred. Two paths (see
    # future-plans/subscription-and-access-roadmap.md):
    #   - No logged-in user: the pre-login trial (1 free Room generation per
    #     browser, cookie-tracked) - 401s once already used, matching the
    #     existing "log in to continue" frontend handling. Always allowed to
    #     choose a backend (Chunk 3's pre-login spec), unlike a logged-in
    #     Free-tier user.
    #   - Logged-in user: Chunk 1's plan quota, against whichever backend
    #     this user's plan+request actually resolves to (Chunk 3) - Free
    #     always resolves to the app's CONFIGURED default (settings.image_provider),
    #     ignoring any preferred_model sent; Pro/Studio may override it.
    #
    # requested_backend is the client's raw, unvalidated ask - resolve_preferred_backend()
    # (app/plans.py) is what actually decides whether it's honored; an
    # invalid/unrecognized value is silently treated as "no preference", same
    # "correct nonsense rather than error the whole request" convention this
    # endpoint already uses elsewhere.
    requested_backend = preferred_model if preferred_model in ("kaggle", "openai") else None
    plan_row = None
    if user is None:
        _consume_anonymous_trial(request, response, "room")
        backend_override = resolve_preferred_backend(None, requested_backend)
    else:
        plan_row = get_or_create_user_plan(session, user.id)
        capture_identity(session, user.id, email=email, display_name=display_name)
        backend_override = resolve_preferred_backend(plan_row.plan, requested_backend)
        quota_backend = backend_override or backend_bucket(settings.image_provider)
        try:
            plan_row = consume_quota(session, user.id, "room", quota_backend)
        except QuotaExceededError as exc:
            raise HTTPException(403, exc.user_message())

    storage = get_storage()
    provider = get_provider()

    project = Project(
        status="queued",
        interior_style=interior_style,
        color_palette=color_palette,
        additional_instructions=additional_instructions,
        user_id=user.id if user else None,
        room_dimensions_json=json.dumps(room_dimensions) if room_dimensions else None,
    )
    session.add(project)
    session.commit()
    session.refresh(project)

    # Namespaced by the Clerk user id, optionally with a human-readable label
    # appended (see _storage_namespace) so every user's uploads/renders group
    # under their own S3 prefix, and that prefix is actually identifiable by
    # name when browsing the bucket - see CLAUDE.md's "Authentication"
    # section. Anonymous (pre-login trial) uploads share one fixed namespace
    # instead - see ANONYMOUS_STORAGE_NAMESPACE's comment.
    storage_namespace = _storage_namespace(user.id, display_name) if user else ANONYMOUS_STORAGE_NAMESPACE
    if plan_row is not None:
        _write_account_json(storage, storage_namespace, plan_row)
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
        backend_override,
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
        created_at=_utc_isoformat(project.created_at),
        city=project.city,
        interior_style=project.interior_style,
        color_palette=project.color_palette,
        additional_instructions=project.additional_instructions,
        room_dimensions=room_dimensions,
        image_model=project.image_model,
    )


@app.get("/api/projects/{project_id}", response_model=ProjectStatusResponse)
def get_project(
    project_id: str, session: Session = Depends(get_session), user: AuthUser | None = Depends(get_current_user)
):
    project = session.get(Project, project_id)
    # 404 (not 403) for both "doesn't exist" and "not yours" - doesn't let a
    # caller distinguish "wrong id" from "someone else's real project", so
    # project ids aren't enumerable across accounts. See _owns_project()'s
    # docstring for the anonymous-project case.
    if project is None or not _owns_project(project.user_id, user):
        raise HTTPException(404, "project not found")

    return _project_to_response(project, get_storage())


@app.post("/api/projects/{project_id}/cancel", response_model=ProjectStatusResponse)
def cancel_project(
    project_id: str, session: Session = Depends(get_session), user: AuthUser | None = Depends(get_current_user)
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
    if project is None or not _owns_project(project.user_id, user):
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
    request: Request,
    response: Response,
    file: UploadFile | None = File(None),
    length: float | None = Form(None),
    width: float | None = Form(None),
    unit: str = Form("m"),
    prompt: str = Form(""),
    display_name: str = Form(""),
    email: str = Form(""),
    floor_count: int | None = Form(None),
    bedrooms: int | None = Form(None),
    bathrooms: int | None = Form(None),
    extras: str = Form(""),
    facing: str | None = Form(None),
    preferred_model: str | None = Form(None),
    session: Session = Depends(get_session),
    user: AuthUser | None = Depends(get_current_user),
):
    """Mirrors create_project()'s validate -> create-row -> commit ->
    upload-original -> commit -> background_tasks.add_task shape. length/width
    are optional (a plot photo alone is still a valid, if less useful,
    submission) - dimensions.json degrades to an empty dict rather than
    rejecting the request.

    The plot photo itself is ALSO optional (2026-09) - unlike Room Redesign,
    where the uploaded photo is the thing being edited (mandatory, always
    was), Build a House's floor plan/blueprint/DXF/Concept Layout are all
    computed purely from dimensions + room program and never touch the
    photo; only the best-effort analyze_plot() text call and the (currently
    disabled, see settings.house_render_enabled) exterior render actually
    use it. See CLAUDE.md's "Image-input investigation" entry for the full
    reasoning. When `file` is omitted, `plot_image_key` stays None and
    run_house_pipeline() skips both of those steps cleanly.

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

    facing (2026-09-04) is the optional North/South/East/West plot-facing
    selection - which compass direction the entrance/public zone faces (see
    app/pipeline/floor_layout.py's layout_floor() docstring for the full
    orientation convention). Genuinely optional: an unset/blank/unrecognized
    value is NOT rejected here - it's passed through as-is and resolved to
    "south" one layer down, in run_house_pipeline() itself (the real user
    request: "if the user doesn't choose anything keep the entrance from
    south"). Kept unvalidated at this layer on purpose, matching this
    endpoint's existing "silently correct obvious nonsense rather than error
    the whole request" treatment for floor_count/bedrooms/bathrooms above.
    """
    data: bytes | None = None
    if file is not None:
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
        "facing": facing or None,
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

    # Anonymous (pre-login) trial gate ONLY - a logged-in user's ONGOING
    # monthly quota is still NOT enforced here (unlike create_project above),
    # deliberately, not an oversight. The approved pricing table
    # (future-plans/subscription-and-access-roadmap.md) gives Free tier
    # "3 Kaggle house generations/mo, 0 OpenAI", but settings.house_image_provider
    # defaults to "openai" (no trained Kaggle house-render model exists in
    # production yet - see CLAUDE.md's "Build a House feature" section).
    # Gating a LOGGED-IN user's ongoing quota on the REAL configured backend
    # would 403 every Free-tier user's second-and-later Build a House
    # generation; hardcoding the "kaggle" bucket instead would silently let
    # Free users burn real, paid OpenAI calls at zero counted cost. Still
    # unresolved even with Chunk 3's routing now built - see the roadmap
    # doc's "House quota gap" note (2026-09-12); it needs a real Kaggle house
    # model or a pricing-table change, not just routing code.
    #
    # The model-CHOICE restriction, independent of quota tracking, IS
    # enforced here though: a Free-tier user can never explicitly force
    # "openai" for house (resolve_preferred_backend() below always returns
    # None for them) - this doesn't fully close the cost-exposure gap above
    # (their request still runs on OpenAI today regardless, since that's the
    # only configured backend), but it stops a Free user from making that
    # exposure WORSE by actively picking the paid option on purpose.
    requested_backend = preferred_model if preferred_model in ("kaggle", "openai") else None
    plan_row = None
    if user is None:
        _consume_anonymous_trial(request, response, "house")
        backend_override = resolve_preferred_backend(None, requested_backend)
    else:
        plan_row = get_or_create_user_plan(session, user.id)
        capture_identity(session, user.id, email=email, display_name=display_name)
        backend_override = resolve_preferred_backend(plan_row.plan, requested_backend)

    storage = get_storage()
    provider = get_provider()

    house_project = HouseProject(
        status="queued",
        dimensions_json=json.dumps(dimensions),
        prompt=prompt,
        user_id=user.id if user else None,
        house_inputs_json=json.dumps(house_inputs),
    )
    session.add(house_project)
    session.commit()
    session.refresh(house_project)

    # storage_namespace is needed below (metadata.json key, background task)
    # regardless of whether a photo was uploaded - only plot_image_key itself
    # is conditional on data being present.
    storage_namespace = _storage_namespace(user.id, display_name) if user else ANONYMOUS_STORAGE_NAMESPACE
    if plan_row is not None:
        # NOTE: house generations aren't currently counted by consume_quota()
        # at all (see the "House quota gap" comment above) - the
        # house_kaggle_used/house_openai_used numbers written here reflect
        # that same, already-documented gap, not a new omission.
        _write_account_json(storage, storage_namespace, plan_row)
    if data is not None:
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
        facing,
        backend_override,
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
    feasibility = json.loads(house_project.feasibility_json) if house_project.feasibility_json else None

    return HouseProjectStatusResponse(
        house_project_id=house_project.id,
        status=house_project.status,
        error=house_project.error,
        plot_description=house_project.plot_description,
        images=images,
        floor_plan_status=house_project.floor_plan_status,
        floor_plan_error=house_project.floor_plan_error,
        floor_plan_urls=floor_plan_urls,
        blueprint_status=house_project.blueprint_status,
        blueprint_urls=blueprint_urls,
        blueprint_dxf_urls=blueprint_dxf_urls,
        created_at=_utc_isoformat(house_project.created_at),
        prompt=house_project.prompt,
        dimensions=dimensions,
        house_inputs=house_inputs,
        render_model=house_project.render_model,
        feasibility=feasibility,
    )


@app.get("/api/house-projects/{house_project_id}", response_model=HouseProjectStatusResponse)
def get_house_project(
    house_project_id: str,
    session: Session = Depends(get_session),
    user: AuthUser | None = Depends(get_current_user),
):
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or not _owns_project(house_project.user_id, user):
        raise HTTPException(404, "house project not found")

    return _house_project_to_response(house_project, get_storage())


@app.post("/api/house-projects/{house_project_id}/cancel", response_model=HouseProjectStatusResponse)
def cancel_house_project(
    house_project_id: str,
    session: Session = Depends(get_session),
    user: AuthUser | None = Depends(get_current_user),
):
    """Mirrors cancel_project() above - see its docstring for the full
    best-effort cancel-and-discard contract."""
    house_project = session.get(HouseProject, house_project_id)
    if house_project is None or not _owns_project(house_project.user_id, user):
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
