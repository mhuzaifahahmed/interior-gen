import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(SQLModel, table=True):
    """Real account, not a placeholder - see app/auth.py for the session/
    password-hashing logic. username is validated (app.auth.validate_username)
    to a safe charset ([a-z0-9_], 3-32 chars) BEFORE a row is ever created,
    since it's also used verbatim as an S3 key path segment
    (users/{username}/...) - see app/pipeline/generate.py / generate_house.py.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    full_name: Optional[str] = None
    role: Optional[str] = None
    # Real password for password-based signups. Google-only accounts (created
    # via /api/auth/google/callback, see app/main.py) get a random, never-
    # revealed bcrypt hash here instead of a nullable column - simpler than a
    # schema change (SQLite can't drop a NOT NULL constraint via ALTER TABLE
    # without a full table rebuild) and has the same effect: password login
    # naturally fails for these accounts since nobody knows the random value.
    password_hash: str
    # Google's stable per-account subject ID, set only for accounts that have
    # ever signed in with Google (find-or-create-by-email in
    # app/main.py::google_callback links this to a pre-existing password
    # account on first Google sign-in). Not enforced unique at the DB level on
    # existing dev databases (see db.py's additive-only migration), only via
    # application-level lookup before create - fine for this dev prototype.
    google_sub: Optional[str] = Field(default=None, index=True)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    status: str = Field(default="queued")  # queued | running | done | failed
    error: Optional[str] = None

    # Nullable so any pre-auth dev-DB rows (from before login was required)
    # still load - not backfilled, this is a dev prototype, not a real
    # migration target. Every NEW row always gets a real owner (main.py's
    # create_project requires a logged-in user).
    user_id: Optional[str] = Field(default=None, foreign_key="user.id")

    original_key: Optional[str] = None
    room_description: Optional[str] = None
    user_style_notes: Optional[str] = None

    economical_key: Optional[str] = None
    mid_key: Optional[str] = None
    premium_key: Optional[str] = None

    # Materials/pricing feature: city is optional (empty = user opted for
    # images-only, no Gemini price calls at all - see run_pipeline).
    city: Optional[str] = None
    materials_json: Optional[str] = None
    materials_status: str = Field(default="idle")  # idle | running | done | failed | skipped

    meta_json: Optional[str] = None


class HouseProject(SQLModel, table=True):
    """The 'Build a House' feature's own table - kept separate from Project
    rather than overloaded onto it, since the fields genuinely differ (plot
    dimensions/floor plan vs. room tiers/materials). See app/pipeline/
    generate_house.py for the pipeline that populates this.
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    status: str = Field(default="queued")  # queued | running | done | failed
    error: Optional[str] = None

    # Same nullable-for-pre-auth-rows reasoning as Project.user_id above.
    user_id: Optional[str] = Field(default=None, foreign_key="user.id")

    plot_image_key: Optional[str] = None
    # {"length": float, "width": float, "unit": str} as JSON text - same
    # JSON-as-text convention as Project.materials_json/meta_json.
    dimensions_json: Optional[str] = None
    prompt: Optional[str] = None

    plot_description: Optional[str] = None

    # Floor-plan generation via a real, PAID vendor is deferred (no vendor
    # wired in yet - see app/providers/idealhouse.py) - floor_plan_key stays
    # None until one is. NOT the same thing as room_layout_json/
    # blueprint_keys_json below - those power a separate, free, ALGORITHMIC
    # blueprint step that's live today (see app/pipeline/generate_house.py).
    floor_plan_key: Optional[str] = None
    floor_plan_status: str = Field(default="idle")  # idle | running | not_configured | done | failed

    render_key: Optional[str] = None

    # Free algorithmic blueprint step: Gemini's structured room list per floor
    # (app/providers/gemini.py's generate_room_layout), the computed room
    # rectangles, and one drawn PNG key per floor (app/pipeline/floor_layout.py
    # + blueprint_svg.py). blueprint_keys_json is a JSON list of storage keys,
    # floor-ordered (index 0 = ground floor - also the image fed to the paid
    # AI render step as its reference).
    room_layout_json: Optional[str] = None
    blueprint_keys_json: Optional[str] = None
    blueprint_status: str = Field(default="idle")  # idle | running | done | failed

    meta_json: Optional[str] = None
