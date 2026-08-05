import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.models import Project
from app.pipeline.prompts import PROMPT_VERSION, TIER_SPECS, build_prompt
from app.pipeline.timing import PipelineTimer
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
    timer = PipelineTimer(project_id)

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
            with timer.stage("storage.get(original)"):
                original_bytes = storage.get(project.original_key)

            try:
                with timer.stage("describe_room"):
                    room_description = provider.describe_room(original_bytes)
            except Exception:
                logger.exception("describe_room failed for project %s; continuing without it", project_id)
                room_description = None

            project.room_description = room_description
            session.add(project)
            session.commit()

            try:
                with timer.stage("generate_tier_notes"):
                    tier_notes = provider.generate_tier_notes(original_bytes)
            except Exception:
                logger.exception("generate_tier_notes failed for project %s; continuing without it", project_id)
                tier_notes = {}

            executor = None
            materials_futures = None
            if city:
                # Best-effort - see estimate_room_area()'s docstring for why
                # this exists (without it, a per-sqft price found for e.g.
                # flooring never actually got multiplied into a real total).
                # Only called when materials pricing will actually run, since
                # it's wasted work otherwise.
                try:
                    with timer.stage("estimate_room_area"):
                        room_area_sqft = provider.estimate_room_area(original_bytes)
                except Exception:
                    logger.exception(
                        "estimate_room_area failed for project %s; continuing without it", project_id
                    )
                    room_area_sqft = None

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
                        room_area_sqft,
                    )
                    for tier in TIERS
                }
            else:
                project.materials_status = "skipped"
                session.add(project)
                session.commit()

            # The 3 tiers' OpenAI image-edit calls are independent (no shared
            # state, no data dependency between them) but were previously run
            # in a plain sequential loop - each one taking ~10-30s meant total
            # wait time was roughly 3x a single tier's. Running them
            # concurrently (same ThreadPoolExecutor pattern already used for
            # materials below) bounds the wait to the SLOWEST tier instead of
            # the sum of all three - the single biggest lever on perceived
            # generation speed.
            #
            # Storage uploads are further overlapped with STILL-RUNNING image
            # generation: as_completed() (not a fixed-order loop) means the
            # moment any one tier's image lands, its S3 upload starts
            # immediately on its own worker thread while the other tiers are
            # still generating - rather than waiting on tiers in a fixed
            # order even if a later tier actually finished first. Each
            # upload also gets its own worker, so if two tiers finish close
            # together their uploads overlap each other too.
            #
            # Only network calls (generate_image, storage.put) run on worker
            # threads; every `session`/`project` ORM mutation stays on the
            # main thread - SQLModel sessions aren't safe to share across
            # threads, same discipline the materials futures already follow.
            tier_prompts = {
                tier: build_prompt(tier, room_description, tier_notes.get(tier), user_style_notes)
                for tier in TIERS
            }
            with timer.stage("generate_images+storage_upload(all tiers)"):
                with (
                    ThreadPoolExecutor(max_workers=3) as image_executor,
                    ThreadPoolExecutor(max_workers=3) as upload_executor,
                ):

                    def _generate(tier: str) -> bytes:
                        with timer.stage(f"generate_image:{tier}"):
                            return provider.generate_image(original_bytes, tier_prompts[tier], tier)

                    def _upload(tier: str, image_bytes: bytes) -> str:
                        key_prefix = f"users/{username}/output" if username else "local.output"
                        key = f"{key_prefix}/{project_id}/{tier}.png"
                        with timer.stage(f"storage.put:{tier}"):
                            storage.put(key, image_bytes, content_type="image/png")
                        return key

                    image_futures = {image_executor.submit(_generate, tier): tier for tier in TIERS}
                    upload_futures = {}
                    for image_future in as_completed(image_futures):
                        tier = image_futures[image_future]
                        image_bytes = image_future.result()
                        upload_future = upload_executor.submit(_upload, tier, image_bytes)
                        upload_futures[upload_future] = tier

                    for upload_future in as_completed(upload_futures):
                        tier = upload_futures[upload_future]
                        key = upload_future.result()
                        setattr(project, f"{tier}_key", key)
                        session.add(project)
                        session.commit()

            if materials_futures is not None:
                materials: dict[str, dict] = {}
                with timer.stage("materials.join(all tiers)"):
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

        finally:
            timer.log_summary()
