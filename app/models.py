import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    created_at: datetime = Field(default_factory=_now)
    status: str = Field(default="queued")  # queued | running | done | failed
    error: Optional[str] = None

    # The Clerk user id (e.g. "user_2abc...") of whoever created this project -
    # NOT a local FK anymore (there is no local User table since the Clerk
    # migration; Clerk owns identity entirely - see app/auth.py). Nullable so
    # any pre-Clerk dev-DB rows still load - not backfilled, this is a dev
    # prototype. Every NEW row always gets a real owner (main.py's
    # create_project requires a logged-in user).
    user_id: Optional[str] = None

    original_key: Optional[str] = None
    room_description: Optional[str] = None

    # Interior Style + Color Palette: the two REQUIRED user selections (see
    # app/pipeline/prompts.py's STYLE_OPTIONS/COLOR_PALETTES) that replaced the
    # old free-text style-prompt input. additional_instructions is the optional
    # free-text refinement that remains (static/index.html's "Additional
    # Instructions" textarea) - kept as its own column (not reusing the old
    # user_style_notes column) since its meaning changed from "the whole style"
    # to "an optional refinement on top of an explicit style"; the old column is
    # left in place unused rather than dropped (SQLite can't drop columns via the
    # additive-only ALTER TABLE migration this project uses - see app/db.py).
    interior_style: Optional[str] = None
    color_palette: Optional[str] = None
    additional_instructions: Optional[str] = None
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

    # Same "Clerk user id, not a local FK" reasoning as Project.user_id above.
    user_id: Optional[str] = None

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
    # A second, blueprint-sourced 3D isometric render (render_layout_key,
    # briefly committed to git) and later a per-floor AI-drawn "CAD plan"
    # (cad_plan_keys_json, never committed) both existed here and were both
    # removed - the CAD plan after a real generation showed the image model
    # hallucinating malformed dimension text. See generate_house.py's
    # HOUSE_PROMPT_VERSION docstring for the full history.

    # Free algorithmic blueprint step: Gemini's structured room list per floor
    # (app/providers/gemini.py's generate_room_layout), the computed room
    # rectangles, and one drawn PNG key per floor (app/pipeline/floor_layout.py
    # + blueprint_svg.py). blueprint_keys_json is a JSON list of storage keys,
    # floor-ordered (index 0 = ground floor). This is THE floor-plan image -
    # 100% deterministic (no AI model ever touches geometry/dimensions/text),
    # now including furniture and staircase symbols (v5).
    room_layout_json: Optional[str] = None
    blueprint_keys_json: Optional[str] = None
    blueprint_status: str = Field(default="idle")  # idle | running | done | failed

    meta_json: Optional[str] = None
