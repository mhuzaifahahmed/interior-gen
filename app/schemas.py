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


class HouseProjectCreateResponse(BaseModel):
    house_project_id: str


class HouseProjectStatusResponse(BaseModel):
    house_project_id: str
    status: str
    error: Optional[str] = None
    plot_description: Optional[str] = None
    images: dict[str, Optional[str]]
    floor_plan_status: str = "idle"
    # Free algorithmic blueprint step (app/pipeline/floor_layout.py +
    # blueprint_svg.py) - unrelated to floor_plan_status above, which stays
    # reserved for a future real, paid floor-plan vendor (still inert).
    blueprint_status: str = "idle"
    blueprint_urls: list[str] = []
    # Same history-dropdown addition as ProjectStatusResponse above.
    created_at: Optional[str] = None
    prompt: Optional[str] = None
