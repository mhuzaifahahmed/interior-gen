import json
import logging

from sqlmodel import Session

from app.db import engine
from app.models import HouseProject
from app.pipeline.blueprint_svg import render_floor_blueprint
from app.pipeline.floor_layout import layout_floor
from app.pipeline.house_prompts import build_house_prompt
from app.providers.base import Provider
from app.storage.base import Storage

logger = logging.getLogger(__name__)


def _is_cancelled(session: Session, house_project: HouseProject) -> bool:
    """Mirrors app/pipeline/generate.py's _is_cancelled() - see its docstring
    for why session.refresh() (not a plain attribute read) is required."""
    session.refresh(house_project)
    return house_project.status == "cancelled"

# v3: enriched photoreal prompt vocabulary, floor-count hard constraint, a
# researched negative-prompt block.
# v4: removed the second, blueprint-sourced 3D isometric render; briefly
# added a per-floor AI-drawn "CAD plan" (gpt-image-1 redrawing the
# deterministic blueprint) - REVERTED in v5 below after a real generation
# showed the image model hallucinating malformed dimension/area text (e.g.
# "18.4 x 522.59 ft") when asked to render technical content. Never
# committed to git, so cleanly removed rather than left dormant.
# v5: the floor-plan image is 100% deterministic again - no AI model ever
# touches geometry, dimensions, or labels. blueprint_svg.py gained furniture
# symbols and a staircase (for multi-floor buildings) to close the
# remaining "looks basic" gap WITHOUT reintroducing hallucination risk -
# see that module's docstring. This directly follows the "structured
# geometry is the source of truth; never let an image model invent
# dimensions" principle from a professional floor-plan generation spec the
# user supplied, taken to its most literal conclusion: skip the image model
# for the floor plan entirely, not just for the numbers.
# v6: the free-text-only prompt input was replaced with structured dropdowns
# (Floors, Bedrooms, Bathrooms, Garage, Kitchen-each-floor toggles) plus a
# small free-text "extras" box - see app/main.py's create_house_project. The
# server now composes a natural-language requirements string from those
# selections (still stored as `prompt`, for backward compatibility with
# every downstream consumer of it), and passes the real, explicit floor
# count straight into generate_room_layout() instead of relying on regex-
# guessing it back out of that composed text.
HOUSE_PROMPT_VERSION = "v6"


def run_house_pipeline(
    house_project_id: str,
    provider: Provider,
    storage: Storage,
    dimensions: dict,
    prompt: str | None = None,
    username: str | None = None,
    floor_count: int | None = None,
) -> None:
    """Runs the "Build a House" pipeline for one HouseProject. Mirrors
    app/pipeline/generate.py's run_pipeline shape: its own DB session (runs as
    a background task after the request-scoped session closes), per-stage
    commits so polling clients see partial progress, best-effort degradation
    for the analysis/floor-plan steps, and a top-level catch-all that marks
    the whole project failed.

    dimensions: {"length": float, "width": float, "unit": str}. prompt is a
    composed requirements string (structured selections + free-text extras -
    see app/main.py's create_house_project) or, for backward compatibility,
    raw free text. floor_count, when given, is the REAL explicit value from
    the structured "Floors" dropdown - passed straight to
    provider.generate_room_layout() so it skips regex-guessing the floor
    count from prompt text entirely. username namespaces every generated-image
    key under users/{username}/buildAHouse/output/... - see run_pipeline's
    docstring in generate.py for why it's optional here despite the endpoint
    always supplying it.
    """
    key_prefix = f"users/{username}/buildAHouse/output" if username else "local.output"
    with Session(engine) as session:
        house_project = session.get(HouseProject, house_project_id)
        if house_project is None:
            logger.error("run_house_pipeline: house project %s not found", house_project_id)
            return

        house_project.status = "running"
        session.add(house_project)
        session.commit()

        try:
            plot_bytes = storage.get(house_project.plot_image_key)

            try:
                plot_description = provider.analyze_plot(plot_bytes, dimensions)
            except Exception:
                logger.exception(
                    "analyze_plot failed for house project %s; continuing without it", house_project_id
                )
                plot_description = None

            house_project.plot_description = plot_description
            session.add(house_project)
            session.commit()

            # Floor-plan generation is best-effort/deferred - see
            # app/providers/idealhouse.py. None (the expected path today) means
            # "not_configured", not a failure - the whole house-project must
            # still succeed without one.
            house_project.floor_plan_status = "running"
            session.add(house_project)
            session.commit()

            try:
                floor_plan_bytes = provider.generate_floor_plan(plot_description, dimensions, prompt or "")
            except Exception:
                logger.exception(
                    "generate_floor_plan failed for house project %s; continuing without it", house_project_id
                )
                floor_plan_bytes = None

            if floor_plan_bytes:
                key = f"{key_prefix}/{house_project_id}/floor_plan.png"
                storage.put(key, floor_plan_bytes, content_type="image/png")
                house_project.floor_plan_key = key
                house_project.floor_plan_status = "done"
            else:
                house_project.floor_plan_status = "not_configured"
            session.add(house_project)
            session.commit()

            if _is_cancelled(session, house_project):
                logger.info(
                    "house project %s was cancelled before the blueprint step started - stopping",
                    house_project_id,
                )
                return

            # Free algorithmic blueprint step - unrelated to the floor_plan_*
            # stage above (that one's the still-inert, deferred REAL PAID
            # vendor slot). This one is live: Gemini returns a structured room
            # list per floor, app/pipeline/floor_layout.py deterministically
            # slices the plot into room rectangles, app/pipeline/
            # blueprint_svg.py draws each floor as a PNG. Best-effort/local
            # try-except, same as analyze_plot/generate_floor_plan above - a
            # bug in this newer code must not take down the render step below.
            house_project.blueprint_status = "running"
            session.add(house_project)
            session.commit()

            room_layout: dict | None = None
            blueprint_keys: list[str] = []
            try:
                room_layout = provider.generate_room_layout(
                    dimensions, prompt or "", plot_description, floor_count
                )
                total_floors = len(room_layout["floors"])
                for floor in room_layout["floors"]:
                    rects = layout_floor(floor["rooms"], dimensions)
                    png_bytes = render_floor_blueprint(floor["floor_number"], rects, dimensions, total_floors)
                    key = f"{key_prefix}/{house_project_id}/blueprint_floor{floor['floor_number']}.png"
                    storage.put(key, png_bytes, content_type="image/png")
                    blueprint_keys.append(key)
                house_project.blueprint_status = "done"
            except Exception:
                logger.exception(
                    "blueprint generation failed for house project %s; falling back to plot photo for the render",
                    house_project_id,
                )
                room_layout = None
                blueprint_keys = []
                house_project.blueprint_status = "failed"

            house_project.room_layout_json = json.dumps(room_layout) if room_layout else None
            house_project.blueprint_keys_json = json.dumps(blueprint_keys) if blueprint_keys else None
            session.add(house_project)
            session.commit()

            if _is_cancelled(session, house_project):
                logger.info(
                    "house project %s was cancelled before the render started - stopping", house_project_id
                )
                return

            # Exterior render is NOT best-effort - it's the core paid
            # deliverable of this feature, same treatment as the room-redesign
            # image loop. A failure here propagates to the outer except and
            # fails the project. Edited from the real plot photo (a photoreal
            # "house on this actual land" picture). v4 removed the second,
            # blueprint-sourced 3D isometric render entirely - this is now the
            # only generate_house_render() call in the pipeline.
            primary_prompt = build_house_prompt(dimensions, prompt, plot_description, room_layout)
            render_bytes = provider.generate_house_render(plot_bytes, primary_prompt)
            render_key = f"{key_prefix}/{house_project_id}/render.png"
            storage.put(render_key, render_bytes, content_type="image/png")
            house_project.render_key = render_key
            # Which provider produced the render - "our model"/"OpenAI", or
            # None if the provider doesn't track this (e.g. a test
            # FakeProvider). See app/providers/hybrid.py's
            # get_house_render_model_label() docstring.
            render_model = getattr(provider, "get_house_render_model_label", lambda: None)()
            house_project.render_model = render_model
            session.add(house_project)
            session.commit()

            house_project.status = "done"
            house_project.meta_json = json.dumps(
                {
                    "house_prompt_version": HOUSE_PROMPT_VERSION,
                    "dimensions": dimensions,
                    "prompt": prompt,
                    "floor_plan_generated": house_project.floor_plan_status == "done",
                    "blueprint_generated": house_project.blueprint_status == "done",
                    "floor_count": len(blueprint_keys) or (len(room_layout["floors"]) if room_layout else 0),
                    "render_model": render_model,
                }
            )
            session.add(house_project)
            session.commit()

        except Exception as exc:
            logger.exception("house pipeline failed for house project %s", house_project_id)
            house_project.status = "failed"
            house_project.error = str(exc)
            session.add(house_project)
            session.commit()
