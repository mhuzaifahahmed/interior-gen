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


class HouseProjectCreateResponse(BaseModel):
    house_project_id: str


class HouseProjectStatusResponse(BaseModel):
    house_project_id: str
    status: str
    error: Optional[str] = None
    plot_description: Optional[str] = None
    images: dict[str, Optional[str]]
    floor_plan_status: str = "idle"
