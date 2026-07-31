import json
import logging
from concurrent.futures import ThreadPoolExecutor

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.models import Project
from app.pipeline.prompts import PROMPT_VERSION, TIER_SPECS, build_prompt
from app.providers.base import Provider
from app.providers.gemini import fallback_materials
from app.storage.base import Storage

logger = logging.getLogger(__name__)

TIERS = ("economical", "mid", "premium")

# Generous timeout for a single tier's materials lookup. Bumped again (45->75
# ->100) after adding a retry for Gemini's transient 503s (up to 3 attempts,
# 2s apart - see gemini.py's MATERIALS_GEMINI_MAX_ATTEMPTS) plus a 30s (was
# 20s) SerpApi per-search timeout, both of which raise the real worst-case
# per-tier latency. generate_materials() already catches its own exceptions
# internally and returns a never-empty fallback on failure - this timeout only
# guards against a hung network call that never raises, so it should rarely
# if ever fire in practice.
MATERIALS_TIMEOUT_SECONDS = 100


def run_pipeline(
    project_id: str,
    provider: Provider,
    storage: Storage,
    user_style_notes: str | None = None,
    city: str | None = None,
    username: str | None = None,
) -> None:
    """Runs the full 3-tier generation for a project. Intended to run as a
    background task; opens its own DB session since the request-scoped one
    will already be closed by the time this executes.

    user_style_notes is the optional free-text style prompt the user typed in
    (static/index.html's style-prompt input) - passed through to every tier's
    build_prompt() call, see prompts.py for exactly how it's incorporated.

    username namespaces every generated-image storage key under
    users/{username}/output/... (see app/main.py's create_project) - required
    in practice (the endpoint always has a logged-in user), optional here only
    so this function's signature doesn't force every caller/test to pass it;
    None falls back to the pre-auth flat key layout.

    city is optional (empty/None means the user chose images-only - see
    static/app.js's empty-city confirm dialog). When present, a materials/pricing
    lookup for all 3 tiers is launched via a ThreadPoolExecutor at the start of
    this function and joined after the image loop - NOT via a second
    BackgroundTasks call, because Starlette runs BackgroundTasks sequentially,
    which would make materials run strictly after images instead of overlapping
    them. Each tier's lookup uses its own DEDICATED key from
    settings.gemini_materials_api_keys (a fixed tier->key mapping, not shared
    with gemini_api_key/the text calls) so all 3 run on separate rate-limit
    quotas with zero contention.
    """
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            logger.error("run_pipeline: project %s not found", project_id)
            return

        project.status = "running"
        project.city = city
        session.add(project)
        session.commit()

        try:
            original_bytes = storage.get(project.original_key)

            try:
                room_description = provider.describe_room(original_bytes)
            except Exception:
                logger.exception("describe_room failed for project %s; continuing without it", project_id)
                room_description = None

            project.room_description = room_description
            session.add(project)
            session.commit()

            try:
                tier_notes = provider.generate_tier_notes(original_bytes)
            except Exception:
                logger.exception("generate_tier_notes failed for project %s; continuing without it", project_id)
                tier_notes = {}

            executor = None
            materials_futures = None
            if city:
                project.materials_status = "running"
                session.add(project)
                session.commit()

                materials_keys = settings.gemini_materials_api_keys
                executor = ThreadPoolExecutor(max_workers=3)
                materials_futures = {
                    tier: executor.submit(
                        provider.generate_materials,
                        tier,
                        TIER_SPECS[tier],
                        room_description,
                        city,
                        materials_keys[tier],
                    )
                    for tier in TIERS
                }
            else:
                project.materials_status = "skipped"
                session.add(project)
                session.commit()

            for tier in TIERS:
                prompt = build_prompt(tier, room_description, tier_notes.get(tier), user_style_notes)
                image_bytes = provider.generate_image(original_bytes, prompt, tier=tier)
                key_prefix = f"users/{username}/output" if username else "local.output"
                key = f"{key_prefix}/{project_id}/{tier}.png"
                storage.put(key, image_bytes, content_type="image/png")
                setattr(project, f"{tier}_key", key)
                session.add(project)
                session.commit()

            if materials_futures is not None:
                materials: dict[str, dict] = {}
                for tier, future in materials_futures.items():
                    try:
                        materials[tier] = future.result(timeout=MATERIALS_TIMEOUT_SECONDS)
                    except Exception:
                        logger.exception(
                            "materials lookup timed out/failed for tier %s, project %s", tier, project_id
                        )
                        materials[tier] = fallback_materials(TIER_SPECS[tier], city)
                executor.shutdown(wait=False)

                project.materials_json = json.dumps(materials)
                project.materials_status = "done"
                session.add(project)
                session.commit()

            project.status = "done"
            project.meta_json = json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "tier_specs": list(TIER_SPECS.keys()),
                    "tier_notes": tier_notes,
                    "user_style_notes": user_style_notes,
                }
            )
            session.add(project)
            session.commit()

        except Exception as exc:
            logger.exception("pipeline failed for project %s", project_id)
            project.status = "failed"
            project.error = str(exc)
            session.add(project)
            session.commit()
