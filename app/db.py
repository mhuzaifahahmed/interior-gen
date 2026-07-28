from pathlib import Path

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

_db_path = settings.database_url.removeprefix("sqlite:///")
if _db_path and _db_path != ":memory:":
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})

# Columns added after the initial table was created. create_all() only creates
# MISSING TABLES, never adds columns to an existing one - without this, an
# existing dev DB (e.g. from before the materials/pricing feature) would keep
# its old schema and every query would 500 with "no such column". Simpler than
# a real migration tool for a single-table SQLite dev DB; each entry is
# (column_name, DDL type + default) - additive only, never drops/renames.
_NEW_COLUMNS = [
    ("city", "TEXT"),
    ("materials_json", "TEXT"),
    ("materials_status", "TEXT NOT NULL DEFAULT 'idle'"),
]


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    _migrate_missing_columns()


def _migrate_missing_columns() -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.execute(text("PRAGMA table_info(project)"))}
        for name, ddl_type in _NEW_COLUMNS:
            if name not in existing:
                conn.execute(text(f"ALTER TABLE project ADD COLUMN {name} {ddl_type}"))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
