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
