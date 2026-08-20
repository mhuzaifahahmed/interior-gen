import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.models import Project
from app.pipeline.prompts import PROMPT_VERSION, build_prompt, build_tier_spec
from app.pipeline.timing import PipelineTimer
from app.providers.base import Provider
from app.providers.gemini import fallback_materials
from app.storage.base import Storage

logger = logging.getLogger(__name__)

TIERS = ("economical", "mid", "premium")

# Sniffs the real image format from magic bytes rather than assuming PNG -
# OpenAI (gpt-image-1) returns PNG, but KaggleImageProvider's notebook was
# switched to JPEG output (smaller payload over the Cloudflare tunnel), and
# IMAGE_PROVIDER can point at either one. Storing the wrong extension/
# content-type wouldn't break rendering (browsers use the real bytes, not
# the extension) but would mislabel S3 objects and downloaded filenames.
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _detect_image_format(image_bytes: bytes) -> tuple[str, str]:
    """Returns (file_extension, content_type). Defaults to PNG for anything
    unrecognized - matches this project's prior hardcoded-PNG behavior."""
    if image_bytes.startswith(_JPEG_MAGIC):
        return "jpg", "image/jpeg"
    return "png", "image/png"

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
    interior_style: str,
    color_palette: str,
    additional_instructions: str | None = None,
    city: str | None = None,
    username: str | None = None,
    user_room_area_sqft: float | None = None,
    user_wall_area_sqft: float | None = None,
) -> None:
    """Runs the full 3-tier generation for a project. Intended to run as a
    background task; opens its own DB session since the request-scoped one
    will already be closed by the time this executes.

    interior_style/color_palette are the two REQUIRED user selections (see
    static/index.html's "Style Parameters" panel and app/pipeline/prompts.py's
    STYLE_OPTIONS/COLOR_PALETTES) - passed through to every tier's build_prompt()
    call and to build_tier_spec() for materials pricing. additional_instructions
    is the optional free-text refinement (static/index.html's "Additional
    Instructions" textarea) - see prompts.py for exactly how it's incorporated.

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

    user_room_area_sqft/user_wall_area_sqft come from an optional user-supplied
    Length x Width (x Height) measurement (see app/main.py's
    _compute_room_dimensions()) - when user_room_area_sqft is given, it's used
    as the AUTHORITATIVE materials-pricing floor area instead of
    provider.estimate_room_area()'s Gemini vision guess (the guess becomes the
    fallback, only called when the user didn't supply a measurement).
    user_wall_area_sqft is independently optional on top of that (only present
    if the user also gave a height) and feeds Paint/wall-finish's own quantity
    rule in generate_materials() - see gemini.py's docstring there.
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

            tier_specs = {tier: build_tier_spec(tier, interior_style, color_palette) for tier in TIERS}

            executor = None
            materials_futures = None
            if city:
                # A real user-supplied measurement (see app/main.py's
                # _compute_room_dimensions()) is AUTHORITATIVE and skips the
                # Gemini vision guess entirely - estimate_room_area() only
                # runs as a fallback when the user didn't supply one. Best-
                # effort either way - see estimate_room_area()'s docstring
                # for why this exists (without it, a per-sqft price found for
                # e.g. flooring never actually got multiplied into a real
                # total).
                if user_room_area_sqft:
                    room_area_sqft = user_room_area_sqft
                else:
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
                        tier_specs[tier],
                        room_description,
                        city,
                        materials_keys[tier],
                        room_area_sqft,
                        user_wall_area_sqft,
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
                tier: build_prompt(
                    tier,
                    interior_style,
                    color_palette,
                    room_description,
                    tier_notes.get(tier),
                    additional_instructions,
                )
                for tier in TIERS
            }
            with timer.stage("generate_images+storage_upload(all tiers)"):

                def _upload(tier: str, image_bytes: bytes) -> str:
                    key_prefix = f"users/{username}/output" if username else "local.output"
                    ext, content_type = _detect_image_format(image_bytes)
                    key = f"{key_prefix}/{project_id}/{tier}.{ext}"
                    with timer.stage(f"storage.put:{tier}"):
                        storage.put(key, image_bytes, content_type=content_type)
                    return key

                # getattr with a default rather than a direct call - some
                # test fakes are plain duck-typed classes that don't inherit
                # from Provider (and thus lack the base class's default
                # implementation), so a missing attribute must mean "no batch
                # support" rather than an AttributeError.
                if getattr(provider, "supports_batch", lambda: False)():
                    # True GPU-batched generation (currently only Kaggle) -
                    # one call covering all 3 tiers, instead of N separate
                    # HTTP requests. See Provider.supports_batch()'s docstring
                    # for why OpenAI doesn't take this branch (it already
                    # parallelizes at the per-tier request level below, and
                    # gains nothing from a sequential-fallback batch loop).
                    with timer.stage("generate_images_batch(all tiers)"):
                        tier_images = provider.generate_images_batch(original_bytes, tier_prompts)

                    with ThreadPoolExecutor(max_workers=3) as upload_executor:
                        upload_futures = {
                            upload_executor.submit(_upload, tier, image_bytes): tier
                            for tier, image_bytes in tier_images.items()
                        }
                        for upload_future in as_completed(upload_futures):
                            tier = upload_futures[upload_future]
                            key = upload_future.result()
                            setattr(project, f"{tier}_key", key)
                            session.add(project)
                            session.commit()
                else:
                    with (
                        ThreadPoolExecutor(max_workers=3) as image_executor,
                        ThreadPoolExecutor(max_workers=3) as upload_executor,
                    ):

                        def _generate(tier: str) -> bytes:
                            with timer.stage(f"generate_image:{tier}"):
                                return provider.generate_image(original_bytes, tier_prompts[tier], tier)

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
                            materials[tier] = fallback_materials(tier_specs[tier], city)
                executor.shutdown(wait=False)

                project.materials_json = json.dumps(materials)
                project.materials_status = "done"
                session.add(project)
                session.commit()

            project.status = "done"
            project.meta_json = json.dumps(
                {
                    "prompt_version": PROMPT_VERSION,
                    "tier_specs": list(TIERS),
                    "tier_notes": tier_notes,
                    "interior_style": interior_style,
                    "color_palette": color_palette,
                    "additional_instructions": additional_instructions,
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
