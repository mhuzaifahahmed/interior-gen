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
# timeout=30 (real bug hit 2026-09-02, not just a theoretical concern): SQLite's
# default busy_timeout is effectively 0 - any writer that finds the file
# locked by another connection fails IMMEDIATELY with "database is locked"
# instead of waiting. This was always a latent risk for anything writing from
# more than one thread, but became a REAL one once
# generate_house.py's _run_floor_plan_stage (v11) started opening its own
# Session(engine) on a detached background thread that can genuinely overlap
# a project's own main-thread writes (and, in tests, other tests' still-
# running detached threads sharing the same real dev DB file - see CLAUDE.md's
# "Resilient loading" testing note). Without a busy timeout, a lock collision
# there silently kills the thread mid-write (no error handling wraps the DB
# section, only the provider call) - the row is left stuck at
# floor_plan_status="running" forever, with nothing surfaced anywhere. 30s
# gives SQLite real room to retry/wait instead of failing on the first
# collision - psycopg2 has its own, separate connection-pooling story and
# doesn't take this kwarg, hence still gated on _is_sqlite.
engine = create_engine(
    _resolved_database_url,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
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
        ("room_dimensions_json", "TEXT"),
        ("image_model", "TEXT"),
    ],
    "houseproject": [
        ("room_layout_json", "TEXT"),
        ("blueprint_keys_json", "TEXT"),
        ("blueprint_dxf_keys_json", "TEXT"),
        ("floor_plan_keys_json", "TEXT"),
        ("blueprint_status", "TEXT NOT NULL DEFAULT 'idle'"),
        ("user_id", "TEXT"),
        ("house_inputs_json", "TEXT"),
        ("render_model", "TEXT"),
        ("feasibility_json", "TEXT"),
        ("floor_plan_error", "TEXT"),
        # render_layout_key (the 3D isometric render) was added here, was
        # briefly live (committed to git), then removed once that feature was
        # dropped - deliberately NOT listed here anymore. A dev DB that ran
        # that commit keeps the orphan column (this migration is
        # additive-only, it can't drop columns), but nothing reads it -
        # harmless. A later cad_plan_keys_json column (an AI-drawn "CAD
        # plan" feature) was added and removed within the same uncommitted
        # working session, so it never reached any real database at all.
    ],
    "userplan": [
        ("lifetime_generations", "INTEGER NOT NULL DEFAULT 0"),
        ("email", "TEXT NOT NULL DEFAULT ''"),
        ("display_name", "TEXT NOT NULL DEFAULT ''"),
    ],
}


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    # Real bug hit in production (2026-09): the comment that used to be here
    # claimed "a fresh Postgres database already gets the full current schema
    # straight from create_all(), nothing to migrate" - true only the FIRST
    # time the Postgres DB was ever created. Every column added to a
    # SQLModel class AFTER that (like UserPlan.email/display_name/
    # lifetime_generations) never reaches the real, already-existing Postgres
    # table, because create_all() only creates missing TABLES, never adds
    # columns to one that already exists - identical to the SQLite gap this
    # migration was originally written for. Real symptom: GET /api/plan
    # started 500ing in production the moment capture_identity() tried to
    # write plan_row.email on a table with no "email" column - the ADMIN
    # panel + nav quota bar shipped, but silently broke on the live Postgres
    # DB since this only ran for SQLite. Fixed by making
    # _migrate_missing_columns() work against BOTH engines (see its own
    # docstring) - it now runs unconditionally, same DDL strings for either.
    _migrate_missing_columns()
    if not _is_sqlite:
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


def _existing_columns(conn, table_name: str) -> set[str]:
    """Real column names currently on `table_name`, engine-appropriate:
    SQLite has no information_schema, hence the PRAGMA branch; Postgres (and
    every other real SQL engine) supports information_schema.columns
    directly. Returns an empty set if the table doesn't exist yet (a fresh
    DB - create_all() already gave it the full current schema, nothing to
    migrate)."""
    if _is_sqlite:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table_name})"))}
    return {
        row[0]
        for row in conn.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name = :table_name"),
            {"table_name": table_name},
        )
    }


def _migrate_missing_columns() -> None:
    """Runs against EITHER engine - see init_db()'s comment for the real
    production bug this generalization fixes. The ALTER TABLE ... ADD COLUMN
    DDL strings in _NEW_COLUMNS_BY_TABLE are plain ANSI-ish SQL (TEXT/INTEGER,
    NOT NULL DEFAULT ...) that Postgres accepts identically to SQLite - only
    the "what columns already exist" introspection differs, see
    _existing_columns()."""
    with engine.connect() as conn:
        for table_name, new_columns in _NEW_COLUMNS_BY_TABLE.items():
            existing = _existing_columns(conn, table_name)
            if not existing:
                continue  # table doesn't exist yet (fresh DB) - create_all() will give it full schema
            for name, ddl_type in new_columns:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl_type}"))
        conn.commit()


def get_session():
    with Session(engine) as session:
        yield session
