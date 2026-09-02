from typing import Optional

from pydantic import BaseModel


class ProjectCreateResponse(BaseModel):
    project_id: str


class ProjectStatusResponse(BaseModel):
    project_id: str
    status: str
    error: Optional[str] = None
    room_description: Optional[str] = None
    images: dict[str, Optional[str]]
    materials: Optional[dict] = None
    materials_status: str = "idle"
    # ISO 8601 - added for the history dropdown (GET /api/projects), harmless
    # extra field on the existing GET /api/projects/{id} response too.
    created_at: Optional[str] = None
    city: Optional[str] = None
    interior_style: Optional[str] = None
    color_palette: Optional[str] = None
    additional_instructions: Optional[str] = None
    # {"length", "width", "height", "unit", "area_sqft", "wall_area_sqft"} -
    # None when the user didn't supply measurements (materials pricing then
    # falls back to provider.estimate_room_area()'s Gemini vision guess).
    room_dimensions: Optional[dict] = None
    # "our model", "OpenAI", or "our model + OpenAI" - which provider(s)
    # actually produced the generated images. None until at least one tier
    # completes. See app/providers/hybrid.py's get_image_model_label().
    image_model: Optional[str] = None


class HouseProjectCreateResponse(BaseModel):
    house_project_id: str


class HouseProjectStatusResponse(BaseModel):
    house_project_id: str
    status: str
    error: Optional[str] = None
    plot_description: Optional[str] = None
    images: dict[str, Optional[str]]
    floor_plan_status: str = "idle"
    # Set only when floor_plan_status == "unavailable" - a human-readable
    # explanation (see app/providers/session_errors.py) of why the Concept
    # Layout vendor call failed in a way that looks like the Kaggle notebook
    # session being offline, distinct from "not_configured" (no vendor URL
    # set at all - an expected, silent state with no error to show).
    floor_plan_error: Optional[str] = None
    # Real vendor output (app/providers/kaggle_autocad.py, see CLAUDE.md) -
    # one URL per floor, floor-ordered, same convention as blueprint_urls.
    # images["floor_plan"] is kept populated with the FIRST floor's URL only,
    # for backward compatibility with old consumers - this list is the real,
    # current source of truth.
    floor_plan_urls: list[str] = []
    # Free algorithmic blueprint step (app/pipeline/floor_layout.py +
    # blueprint_svg.py) - unrelated to floor_plan_status above, which is the
    # real vendor slot above (kaggle_autocad.py).
    blueprint_status: str = "idle"
    blueprint_urls: list[str] = []
    # Real AutoCAD-format (.dxf) download URLs for each floor - same
    # floor-ordering as blueprint_urls, may be shorter (or empty) than it if
    # DXF export failed for some/all floors while the PNG still succeeded.
    blueprint_dxf_urls: list[str] = []
    # Same history-dropdown addition as ProjectStatusResponse above.
    created_at: Optional[str] = None
    prompt: Optional[str] = None
    # {"length", "width", "unit"} - None when neither length nor width was given.
    dimensions: Optional[dict] = None
    # {"floor_count", "bedrooms", "bathrooms", "extras"} - the structured
    # "Plot Parameters" selections that composed `prompt` above. None for
    # projects created before this feature, or via a direct API call that
    # only supplied the legacy free-text `prompt` field.
    house_inputs: Optional[dict] = None
    # "our model" or "OpenAI" - which provider produced the render. None
    # until the render completes. See
    # app/providers/hybrid.py's get_house_render_model_label().
    render_model: Optional[str] = None
    # Feasibility hard-gate result (app/pipeline/feasibility.py) -
    # {"verdict": "feasible"|"tight"|"not_feasible", "required_area",
    # "available_area", "unit", "explanation"}. None before the blueprint
    # stage runs, or if it failed before a feasibility check could happen.
    feasibility: Optional[dict] = None
