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

    original_key: Optional[str] = None
    room_description: Optional[str] = None

    economical_key: Optional[str] = None
    mid_key: Optional[str] = None
    premium_key: Optional[str] = None

    meta_json: Optional[str] = None
