import json
import logging

from sqlmodel import Session

from app.config import settings
from app.db import engine
from app.models import HouseProject
from app.pipeline.blueprint_dxf import render_floor_blueprint_dxf
from app.pipeline.blueprint_svg import render_floor_blueprint
from app.pipeline.feasibility import check_feasibility
from app.pipeline.floor_layout import layout_floor
from app.pipeline.house_prompts import build_house_prompt
from app.pipeline.house_requirements import mentions_garage, parse_front_yard_depth, parse_garage_cars
from app.pipeline.room_specs import classify_room_category
from app.providers.base import Provider
from app.storage.base import Storage

logger = logging.getLogger(__name__)

_VERDICT_RANK = {"feasible": 0, "tight": 1, "not_feasible": 2}


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
# (Floors, Bedrooms, Bathrooms) plus a small free-text "extras" box (a
# Garage/Kitchen-each-floor checkbox pair was tried and dropped - see
# CLAUDE.md) - see app/main.py's create_house_project. The
# server now composes a natural-language requirements string from those
# selections (still stored as `prompt`, for backward compatibility with
# every downstream consumer of it), and passes the real, explicit floor
# count straight into generate_room_layout() instead of relying on regex-
# guessing it back out of that composed text.
# v7 (2026-08-27): reordered the pipeline so the deterministic room-layout
# stage (generate_room_layout + layout_floor + blueprint PNG/DXF) now runs
# BEFORE the AI "Concept Layout" floor-plan stage (generate_floor_plan),
# not after - see future-plans/concept-layout-controlnet-conditioning.md,
# Phase 1. That plan feeds this repo's real room rectangles into the
# friend-hosted Kaggle model's ControlNet conditioning input so it traces
# our actual geometry instead of a hardcoded empty rectangle, which requires
# the layout to already exist by the time generate_floor_plan() is called.
# The two cancellation checkpoints moved with their stages: one right after
# analyze_plot (before any of layout/blueprint/floor-plan work starts), one
# right before the paid render (unchanged position).
# v8 (2026-08-27): added a real feasibility HARD GATE (app/pipeline/
# feasibility.py, finally building future-plans/feasibility-checker.md) -
# an infeasible room program is never silently laid out/rendered anymore;
# blueprint_status/floor_plan_status become "infeasible" and
# HouseProject.feasibility_json carries a plain-language explanation
# instead. Also: garage car-count and front-yard depth are now parsed
# deterministically out of the free-text requirements string (app/pipeline/
# house_requirements.py, explicit user decision 2026-08-27 - NOT new
# structured frontend fields, since a dedicated Garage/Kitchen checkbox pair
# was already tried and dropped once) - a front yard reduces the actual
# building footprint passed to layout_floor()/the blueprint renderers
# BEFORE any room is placed, and a requested-but-missing garage room is
# injected into the ground floor's room list so it's guaranteed to exist and
# be sized for the requested car count. See app/pipeline/room_specs.py for
# the real-world minimum room sizes this all depends on.
HOUSE_PROMPT_VERSION = "v8"


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

            if _is_cancelled(session, house_project):
                logger.info(
                    "house project %s was cancelled before the layout/blueprint/floor-plan stage "
                    "started - stopping",
                    house_project_id,
                )
                return

            # Free algorithmic blueprint step - runs BEFORE the AI "Concept
            # Layout" floor-plan stage below (v7 reorder, see
            # HOUSE_PROMPT_VERSION comment) because that stage now feeds this
            # step's real room rectangles into the AI model's ControlNet
            # conditioning input. Gemini returns a structured room list per
            # floor, app/pipeline/floor_layout.py deterministically slices the
            # plot into room rectangles, app/pipeline/blueprint_svg.py draws
            # each floor as a PNG. Best-effort/local try-except, same as
            # analyze_plot above - a bug in this newer code must not take down
            # the render step below.
            house_project.blueprint_status = "running"
            session.add(house_project)
            session.commit()

            room_layout: dict | None = None
            blueprint_keys: list[str] = []
            blueprint_dxf_keys: list[str] = []
            feasibility_result: dict | None = None
            garage_cars: int | None = None
            building_dimensions = dimensions
            requirements_text = prompt or ""
            try:
                room_layout = provider.generate_room_layout(
                    dimensions, prompt or "", plot_description, floor_count
                )
                total_floors = len(room_layout["floors"])

                # Garage/front-yard requirements are parsed deterministically
                # from the free-text requirements string, NOT trusted to
                # Gemini's own judgement, and NOT new structured frontend
                # fields (a dedicated Garage/Kitchen checkbox pair was already
                # tried and dropped once - see house_requirements.py's module
                # docstring for the reasoning and the documented front/road-
                # orientation limitation).
                garage_cars = parse_garage_cars(requirements_text) if mentions_garage(requirements_text) else None
                unit = dimensions.get("unit") or "ft"
                front_yard_depth = parse_front_yard_depth(requirements_text, unit)

                yard_infeasible_explanation = None
                if front_yard_depth:
                    building_dimensions = dict(dimensions)
                    available_width = float(dimensions.get("width") or 0) - front_yard_depth
                    if available_width <= 0:
                        yard_infeasible_explanation = (
                            f"The requested front yard ({front_yard_depth:.0f} sq {unit} deep) alone "
                            f"leaves no room for the building on a plot only "
                            f"{float(dimensions.get('width') or 0):.0f} sq {unit} deep."
                        )
                    else:
                        building_dimensions["width"] = available_width

                # Ground floor only - guarantee a garage room exists if one
                # was requested but Gemini's own room list didn't include one.
                if garage_cars and total_floors:
                    ground_floor_rooms = room_layout["floors"][0].setdefault("rooms", [])
                    has_garage = any(
                        classify_room_category(str(r.get("name") or "")) == "garage" for r in ground_floor_rooms
                    )
                    if not has_garage:
                        avg_weight = (
                            sum(float(r.get("area") or 1) for r in ground_floor_rooms) / len(ground_floor_rooms)
                            if ground_floor_rooms
                            else 1.0
                        )
                        ground_floor_rooms.append({"name": "Garage", "area": avg_weight})

                # Feasibility check - HARD GATE (explicit user decision,
                # 2026-08-27): an infeasible floor is never silently laid out
                # or rendered - see app/pipeline/feasibility.py.
                if yard_infeasible_explanation:
                    feasibility_result = {
                        "verdict": "not_feasible",
                        "required_area": None,
                        "available_area": None,
                        "unit": unit,
                        "explanation": yard_infeasible_explanation,
                    }
                else:
                    for floor in room_layout["floors"]:
                        floor_result = check_feasibility(
                            floor.get("rooms") or [], building_dimensions, total_floors, garage_cars
                        )
                        if (
                            feasibility_result is None
                            or _VERDICT_RANK[floor_result["verdict"]] > _VERDICT_RANK[feasibility_result["verdict"]]
                        ):
                            feasibility_result = floor_result

                if feasibility_result and feasibility_result["verdict"] == "not_feasible":
                    logger.info(
                        "house project %s is not feasible: %s",
                        house_project_id,
                        feasibility_result["explanation"],
                    )
                    house_project.blueprint_status = "infeasible"
                else:
                    for floor in room_layout["floors"]:
                        rects = layout_floor(floor["rooms"], building_dimensions, garage_cars)
                        png_bytes = render_floor_blueprint(
                            floor["floor_number"], rects, building_dimensions, total_floors
                        )
                        key = f"{key_prefix}/{house_project_id}/blueprint_floor{floor['floor_number']}.png"
                        storage.put(key, png_bytes, content_type="image/png")
                        blueprint_keys.append(key)
                        # Real AutoCAD-format export of the SAME rectangles - no
                        # AI model involved, see app/pipeline/blueprint_dxf.py's
                        # module docstring. Deliberately its own try/except so a
                        # DXF-serialization bug can never take down the PNG
                        # blueprint (which the render step below depends on).
                        try:
                            dxf_bytes = render_floor_blueprint_dxf(floor["floor_number"], rects, building_dimensions)
                            dxf_key = f"{key_prefix}/{house_project_id}/blueprint_floor{floor['floor_number']}.dxf"
                            storage.put(dxf_key, dxf_bytes, content_type="application/dxf")
                            blueprint_dxf_keys.append(dxf_key)
                        except Exception:
                            logger.exception(
                                "DXF export failed for house project %s floor %s; PNG blueprint is unaffected",
                                house_project_id,
                                floor["floor_number"],
                            )
                    house_project.blueprint_status = "done"
            except Exception:
                logger.exception(
                    "blueprint generation failed for house project %s; falling back to plot photo for the render",
                    house_project_id,
                )
                room_layout = None
                blueprint_keys = []
                blueprint_dxf_keys = []
                feasibility_result = None
                house_project.blueprint_status = "failed"

            house_project.room_layout_json = json.dumps(room_layout) if room_layout else None
            house_project.blueprint_keys_json = json.dumps(blueprint_keys) if blueprint_keys else None
            house_project.blueprint_dxf_keys_json = json.dumps(blueprint_dxf_keys) if blueprint_dxf_keys else None
            house_project.feasibility_json = json.dumps(feasibility_result) if feasibility_result else None
            session.add(house_project)
            session.commit()

            # AI "Concept Layout" floor-plan generation - best-effort/deferred,
            # see app/providers/idealhouse.py. None (the expected path when no
            # vendor is configured) means "not_configured", not a failure - the
            # whole house-project must still succeed without one. room_layout
            # (just computed above, possibly None if the blueprint step
            # failed) is passed through so a vendor that can use it
            # (kaggle_autocad.py) traces our real geometry via ControlNet
            # conditioning instead of inventing its own - see
            # future-plans/concept-layout-controlnet-conditioning.md.
            #
            # Same hard gate as the blueprint stage above - an infeasible
            # program never gets an AI visualization either (it would be
            # equally misleading), so this stage is skipped entirely rather
            # than run with a set-of-rectangles that don't actually fit.
            if feasibility_result and feasibility_result["verdict"] == "not_feasible":
                house_project.floor_plan_status = "infeasible"
                session.add(house_project)
                session.commit()
            else:
                house_project.floor_plan_status = "running"
                session.add(house_project)
                session.commit()

                try:
                    floor_plan_images = provider.generate_floor_plan(
                        plot_description, building_dimensions, prompt or "", room_layout
                    )
                except Exception:
                    logger.exception(
                        "generate_floor_plan failed for house project %s; continuing without it", house_project_id
                    )
                    floor_plan_images = None

                if floor_plan_images:
                    floor_plan_keys = []
                    for i, image_bytes in enumerate(floor_plan_images, start=1):
                        key = f"{key_prefix}/{house_project_id}/floor_plan_floor{i}.png"
                        storage.put(key, image_bytes, content_type="image/png")
                        floor_plan_keys.append(key)
                    house_project.floor_plan_key = floor_plan_keys[0]  # legacy single-key field, first floor only
                    house_project.floor_plan_keys_json = json.dumps(floor_plan_keys)
                    house_project.floor_plan_status = "done"
                else:
                    house_project.floor_plan_status = "not_configured"
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
            #
            # settings.house_render_enabled is a dev-only escape hatch (see its
            # comment in app/config.py) - when False, this whole step (and the
            # ONLY OpenAI/Kaggle call in the house pipeline) is skipped, so the
            # project still completes as "done" with whatever else succeeded.
            render_model = None
            if settings.house_render_enabled:
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
            else:
                logger.info(
                    "house_render_enabled is False - skipping the render step for house project %s",
                    house_project_id,
                )
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
                    "feasibility_verdict": feasibility_result["verdict"] if feasibility_result else None,
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
