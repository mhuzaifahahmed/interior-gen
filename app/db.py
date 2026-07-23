from pathlib import Path

from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

_db_path = settings.database_url.removeprefix("sqlite:///")
if _db_path and _db_path != ":memory:":
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    SQLModel.metadata.create_all(engine)


def get_session():
    with Session(engine) as session:
        yield session
