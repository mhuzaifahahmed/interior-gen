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

HOUSE_PROMPT_VERSION = "v2"  # v2: adds the free algorithmic blueprint stage + ground-floor render reference


def run_house_pipeline(
    house_project_id: str,
    provider: Provider,
    storage: Storage,
    dimensions: dict,
    prompt: str | None = None,
    username: str | None = None,
) -> None:
    """Runs the "Build a House" pipeline for one HouseProject. Mirrors
    app/pipeline/generate.py's run_pipeline shape: its own DB session (runs as
    a background task after the request-scoped session closes), per-stage
    commits so polling clients see partial progress, best-effort degradation
    for the analysis/floor-plan steps, and a top-level catch-all that marks
    the whole project failed.

    dimensions: {"length": float, "width": float, "unit": str}. prompt is the
    user's optional free-text style/requirements input (e.g. "2 floors, 3
    bedrooms, modern style"). username namespaces every generated-image key
    under users/{username}/output/... - see run_pipeline's docstring in
    generate.py for why it's optional here despite the endpoint always
    supplying it.
    """
    key_prefix = f"users/{username}/output" if username else "local.output"
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
                room_layout = provider.generate_room_layout(dimensions, prompt or "", plot_description)
                for floor in room_layout["floors"]:
                    rects = layout_floor(floor["rooms"], dimensions)
                    png_bytes = render_floor_blueprint(floor["floor_number"], rects, dimensions)
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

            # The render is NOT best-effort - it's the core paid deliverable of
            # this feature, same treatment as the room-redesign image loop. A
            # failure here propagates to the outer except and fails the project.
            # Its reference image is the GROUND FLOOR's blueprint (index 0,
            # per the confirmed design) when one was drawn; otherwise it falls
            # back to the raw plot photo exactly like the original behavior.
            using_blueprint_image = bool(blueprint_keys)
            render_input_bytes = storage.get(blueprint_keys[0]) if using_blueprint_image else plot_bytes
            house_prompt = build_house_prompt(
                dimensions, prompt, plot_description, room_layout, using_blueprint_image
            )
            render_bytes = provider.generate_house_render(render_input_bytes, house_prompt)
            render_key = f"{key_prefix}/{house_project_id}/render.png"
            storage.put(render_key, render_bytes, content_type="image/png")
            house_project.render_key = render_key
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
