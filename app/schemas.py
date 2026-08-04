from typing import Optional

from pydantic import BaseModel


class SignupRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: Optional[str] = None
    role: Optional[str] = None


class LoginRequest(BaseModel):
    identifier: str  # username or email
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    full_name: Optional[str] = None
    role: Optional[str] = None


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
    user_style_notes: Optional[str] = None


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
