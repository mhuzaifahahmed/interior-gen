from pathlib import Path

from sqlalchemy import text
from sqlmodel import SQLModel, Session, create_engine

from app.config import settings

_resolved_database_url = settings.resolved_database_url
_is_sqlite = _resolved_database_url.startswith("sqlite")

_db_path = _resolved_database_url.removeprefix("sqlite:///")
if _is_sqlite and _db_path and _db_path != ":memory:":
    Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

# check_same_thread=False is a SQLite-only connect arg (works around SQLite's
# single-thread default so FastAPI's threaded request handling doesn't error)
# - psycopg2 rejects it outright when DATABASE_URL points at Postgres instead.
engine = create_engine(
    _resolved_database_url,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
)

# Columns added after the initial table was created. create_all() only creates
# MISSING TABLES, never adds columns to an existing one - without this, an
# existing dev DB (e.g. from before the materials/pricing feature) would keep
# its old schema and every query would 500 with "no such column". Simpler than
# a real migration tool for a single-table SQLite dev DB; each entry is
# (column_name, DDL type + default) - additive only, never drops/renames.
# (table_name, [(column_name, DDL type + default), ...]) - additive only,
# never drops/renames. Was hardcoded to just "project" until the "Build a
# House" free algorithmic blueprint step needed new columns on "houseproject"
# too - existing dev DBs need this migration same as any other table, since
# create_all() only creates missing TABLES, never adds columns to an existing
# one.
_NEW_COLUMNS_BY_TABLE = {
    "project": [
        ("city", "TEXT"),
        ("materials_json", "TEXT"),
        ("materials_status", "TEXT NOT NULL DEFAULT 'idle'"),
        ("user_id", "TEXT"),
        ("interior_style", "TEXT"),
        ("color_palette", "TEXT"),
        ("additional_instructions", "TEXT"),
    ],
    "houseproject": [
        ("room_layout_json", "TEXT"),
        ("blueprint_keys_json", "TEXT"),
        ("blueprint_status", "TEXT NOT NULL DEFAULT 'idle'"),
        ("user_id", "TEXT"),
    ],
}


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    # The PRAGMA-based column migration below is SQLite-only syntax - a fresh
    # Postgres database already gets the full current schema straight from
    # create_all() above (nothing to migrate), and an existing Postgres DB
    # would need a real migration tool (e.g. Alembic), out of scope here.
    if _is_sqlite:
        _migrate_missing_columns()
    else:
        _drop_legacy_user_table()


def _drop_legacy_user_table() -> None:
    """One-time cleanup for an existing Postgres (Neon) database from before
    the Clerk migration: the old User table (and the FK constraints
    project.user_id/houseproject.user_id had pointing at it) are gone now
    that Clerk owns identity entirely and those columns just hold Clerk's own
    user id string, not a local FK - see app/auth.py. Without this, Postgres
    would keep enforcing the old FK constraint and reject every new
    project/house-project insert with a foreign key violation, since a Clerk
    user id will never exist as a row in the now-unmanaged "user" table.
    Best-effort and idempotent (IF EXISTS everywhere) - a no-op on a database
    that never had these, so it's safe to run on every startup.
    """
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE project DROP CONSTRAINT IF EXISTS project_user_id_fkey"))
            conn.execute(text("ALTER TABLE houseproject DROP CONSTRAINT IF EXISTS houseproject_user_id_fkey"))
            conn.execute(text('DROP TABLE IF EXISTS "user"'))
            conn.commit()
        except Exception:
            conn.rollback()


def _migrate_missing_columns() -> None:
    with engine.connect() as conn:
        for table_name, new_columns in _NEW_COLUMNS_BY_TABLE.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
            if not existing:
                continue  # table doesn't exist yet (fresh DB) - create_all() will give it full schema
            for name, ddl_type in new_columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl_type}"))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
