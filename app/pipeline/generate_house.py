import json
import logging
import threading

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
from app.providers.session_errors import KaggleSessionUnavailableError
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
# v9 (2026-08-29): the staircase becomes a REAL reserved room (not a symbol
# drawn inside whichever room happened to be biggest) on every floor of a
# multi-floor building - see the injection block above and
# app/pipeline/floor_layout.py's new circulation zone /
# app/pipeline/blueprint_svg.py's dynamic straight-vs-L-shaped stair
# rendering. Addresses spec items #16 (real staircase footprint + dynamic
# shape) and, partially, #17 (multi-floor coordination - consistent
# zone-relative placement + consistent sizing across floors, NOT
# pixel-exact interior alignment, which would need a fundamentally
# different, non-rectangular-region layout algorithm - see
# floor_layout.py's docstring for why that's a stated limitation, not an
# oversight). See future-plans/house-layout-spec-checklist.md for the full
# item-by-item status.
# v10 (2026-08-31): the AI "Concept Layout" call (generate_floor_plan, the
# friend-hosted Kaggle notebook) and the exterior render call
# (generate_house_render) briefly ran CONCURRENTLY via a ThreadPoolExecutor -
# SUPERSEDED by v11 below, which decouples them entirely instead of merely
# overlapping them. Kept here for history.
# v11 (2026-09-01): generate_floor_plan() is now FULLY DECOUPLED from the
# house project's "done" status, not just run concurrently with the render.
# Real motivation: a live-measured Kaggle Concept Layout call took ~2 minutes
# PER FLOOR (SDXL+ControlNet diffusion on a T4, sequential across floors on
# the notebook's own side) - concurrency with the render call (v10) only
# ever avoided ADDING the render's time on top; it could never shrink the
# ~2min/floor number itself, and with HOUSE_RENDER_ENABLED=false (no render
# running at all right now) there was nothing left to overlap with, so v10's
# win was invisible in practice. Since generate_floor_plan is explicitly
# documented as a SUPPLEMENTARY visual (see CLAUDE.md's "Concept Layout"
# labeling requirement - never a replacement for the deterministic
# blueprint/DXF, which stay the authoritative, accurate deliverable), there
# is no reason the whole project's completion should wait on it at all.
# Now: once the blueprint/DXF/feasibility stage finishes, floor_plan_status
# is set to "running" and generate_floor_plan() is handed to a fire-and-
# forget daemon thread (_run_floor_plan_stage below) that is NEVER joined -
# the main pipeline immediately continues to the (still synchronous,
# still NOT best-effort) render call and then to house_project.status =
# "done", without waiting on the Kaggle call at all. The detached thread
# opens its OWN fresh Session(engine) and re-fetches the row by id when it
# eventually finishes (same "never share a session across threads" rule
# app/pipeline/generate.py's materials ThreadPoolExecutor already
# established) and commits floor_plan_status="done"/"not_configured" +
# the image keys whenever the Kaggle call actually completes - the frontend
# already polls floor_plan_status independently of the overall project
# status (see static/app.js's renderHouseResults()), so this needed zero
# frontend changes. generate_house_render keeps its exact prior contract
# (still synchronous, still not best-effort, still fails the whole project
# on error) - only generate_floor_plan changed shape. Real, accepted
# tradeoff: cancelling a house project can no longer stop the Concept Layout
# call once it has started (already true after v10, now permanent rather
# than a narrow race window) - a cancelled project's detached thread may
# still write a floor_plan image to a project the user no longer sees
# (list_house_projects excludes cancelled rows), same "harmless orphan"
# treatment already accepted elsewhere in this file for S3 objects.
HOUSE_PROMPT_VERSION = "v12"  # v12 (2026-09-04): true front-to-back zoning, real room min/max
# proportions, kitchen-dining adjacency reordering, and garage-buffered-by-Entry-room door
# suppression - see floor_layout.py/blueprint_svg.py module docstrings for the full detail.
# Entry-room auto-injection (mirroring the existing garage injection just above it in this
# file) changes what room_layout can contain, which feeds into build_house_prompt()'s
# room_layout summary.


def _run_floor_plan_stage(
    house_project_id: str,
    provider: Provider,
    storage: Storage,
    plot_description: str | None,
    dimensions: dict,
    prompt: str | None,
    room_layout: dict | None,
    key_prefix: str,
    facing: str | None = None,
) -> None:
    """Runs generate_floor_plan() (the Kaggle "Concept Layout" call) on its
    own daemon thread, fully decoupled from run_house_pipeline()'s own
    session/lifetime - see HOUSE_PROMPT_VERSION's v11 comment for why. Opens
    its own Session(engine) since the caller's session may already be closed
    by the time this finishes (a real, live-measured ~2min/floor call).

    facing (2026-09-04) MUST be the same resolved orientation the real
    blueprint step used for this house project - see
    _conditioning_images_by_floor()'s docstring in kaggle_autocad.py for why
    a mismatch here would make the AI Concept Layout card visibly disagree
    with the deterministic blueprint."""
    floor_plan_error: str | None = None
    try:
        floor_plan_images = provider.generate_floor_plan(
            plot_description, dimensions, prompt or "", room_layout, facing
        )
    except KaggleSessionUnavailableError as exc:
        # A real, actionable problem (the notebook session is offline) - see
        # app/providers/session_errors.py. Logged at warning (not exception -
        # there's no traceback worth keeping, the cause is already known) and
        # surfaced to the user via floor_plan_status="unavailable" +
        # floor_plan_error, instead of silently degrading the same way an
        # unconfigured vendor does.
        logger.warning("generate_floor_plan unavailable for house project %s: %s", house_project_id, exc)
        floor_plan_images = None
        floor_plan_error = str(exc)
    except Exception:
        logger.exception(
            "generate_floor_plan failed for house project %s; continuing without it", house_project_id
        )
        floor_plan_images = None

    try:
        with Session(engine) as session:
            house_project = session.get(HouseProject, house_project_id)
            if house_project is None:
                return
            if floor_plan_images:
                floor_plan_keys = []
                for i, image_bytes in enumerate(floor_plan_images, start=1):
                    key = f"{key_prefix}/{house_project_id}/floor_plan_floor{i}.png"
                    storage.put(key, image_bytes, content_type="image/png")
                    floor_plan_keys.append(key)
                house_project.floor_plan_key = floor_plan_keys[0]  # legacy field, first floor only
                house_project.floor_plan_keys_json = json.dumps(floor_plan_keys)
                house_project.floor_plan_status = "done"
            elif floor_plan_error:
                house_project.floor_plan_status = "unavailable"
                house_project.floor_plan_error = floor_plan_error
            else:
                house_project.floor_plan_status = "not_configured"
            session.add(house_project)
            session.commit()
    except Exception:
        # This thread is detached and never joined (see docstring) - without
        # this, any failure here (e.g. a SQLite "database is locked" error
        # under real write contention, a real bug hit while testing this
        # feature - see app/db.py's `timeout=30` comment for the fix on the
        # connection side) would kill the thread silently, leaving
        # floor_plan_status stuck at "running" forever with zero diagnostic
        # trail anywhere. At minimum, this must be logged so it's actually
        # discoverable - there's no session left to update the row from here
        # if the write itself is what failed.
        logger.exception(
            "failed to persist generate_floor_plan result for house project %s", house_project_id
        )


def run_house_pipeline(
    house_project_id: str,
    provider: Provider,
    storage: Storage,
    dimensions: dict,
    prompt: str | None = None,
    username: str | None = None,
    floor_count: int | None = None,
    facing_input: str | None = None,
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

    facing_input: the raw, optional North/South/East/West selection from the
    upload form (see app/main.py's create_house_project) - resolved to a
    real "south"-by-default facing string just below (2026-09-04, real user
    request). Named "_input" rather than "facing" to make clear this is the
    UNVALIDATED raw value; the resolved, always-valid `facing` local variable
    is what actually gets passed to layout_floor().
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
            # The plot photo is optional (2026-09 - see CLAUDE.md's "Image-
            # input investigation" entry) - a project can be created with
            # dimensions/room-program only. plot_bytes stays None in that
            # case, which cleanly skips both analyze_plot (best-effort
            # already, same as any other analyze_plot failure) and the
            # exterior render step below (there's no photo to edit).
            plot_bytes = storage.get(house_project.plot_image_key) if house_project.plot_image_key else None

            plot_description = None
            if plot_bytes is not None:
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
            # Plot facing (2026-09-04, real user request: an optional
            # North/South/East/West input, defaulting to South when the user
            # doesn't choose one - "keep the entrance from south"). This is
            # the PRODUCT default, distinct from layout_floor()'s own neutral
            # library default of "north" (see that function's docstring) -
            # direct/test callers that never pass `facing` keep behaving
            # exactly as before this feature existed; only this real pipeline
            # resolves the "south by default" rule. Resolved OUTSIDE the try
            # block below (a real bug hit and fixed here: it was originally
            # computed INSIDE that try, right after the Gemini room-layout
            # call - when that call raised, `facing` was never assigned, but
            # code further down the function - after the except - still
            # referenced it unconditionally, causing an UnboundLocalError
            # that masked the original, real failure).
            facing = (facing_input or "south").strip().lower()
            if facing not in ("north", "south", "east", "west"):
                facing = "south"
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
                # docstring for the reasoning).
                garage_cars = parse_garage_cars(requirements_text) if mentions_garage(requirements_text) else None

                # Strip any garage Gemini invented on its own initiative
                # (2026-09-04, real user report: "I didn't tell it to
                # generate a garage but it did"). ROOM_LAYOUT_PROMPT_TEMPLATE
                # explicitly leaves adding a garage up to Gemini's own
                # judgement ("if it fits the requirements") - that's a
                # probabilistic decision, not a real signal the user asked
                # for one. When the user's own text has no "garage" mention
                # at all, garage_cars is None here - remove any room Gemini
                # added anyway so it never reaches layout_floor(), which
                # would otherwise still give ANY room named "Garage" the
                # real, full-box-height carve-out treatment in
                # _slice_reserving_garage() regardless of whether it was
                # actually requested - exactly what caused the reported
                # over-large, cramping garage. Same "deterministic parsing
                # overrides a probabilistic LLM decision" pattern already
                # used to GUARANTEE a garage that WAS requested (the
                # injection block below) - this is its mirror image for the
                # unrequested case.
                if not garage_cars:
                    for floor in room_layout["floors"]:
                        floor["rooms"] = [
                            r
                            for r in floor.get("rooms") or []
                            if classify_room_category(str(r.get("name") or "")) != "garage"
                        ]

                unit = dimensions.get("unit") or "ft"
                front_yard_depth = parse_front_yard_depth(requirements_text, unit)

                yard_infeasible_explanation = None
                if front_yard_depth:
                    building_dimensions = dict(dimensions)
                    # The front yard sits in front of whichever edge is
                    # actually "front" for this facing - N/S facings trim
                    # depth off the width axis (the yard is a strip along the
                    # y-extent), E/W trim off the length axis (the yard is a
                    # strip along the x-extent). See layout_floor()'s
                    # docstring for the same north/south/east/west convention.
                    trim_dimension = "width" if facing in ("north", "south") else "length"
                    available_depth = float(dimensions.get(trim_dimension) or 0) - front_yard_depth
                    if available_depth <= 0:
                        yard_infeasible_explanation = (
                            f"The requested front yard ({front_yard_depth:.0f} sq {unit} deep) alone "
                            f"leaves no room for the building on a plot only "
                            f"{float(dimensions.get(trim_dimension) or 0):.0f} sq {unit} deep."
                        )
                    else:
                        building_dimensions[trim_dimension] = available_depth

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

                    # Garage buffered from living space (2026-09-04, real user
                    # critique: "do not force garage circulation through the
                    # main living room") - guarantee a real Entry/foyer room
                    # exists to route through, same "real data, don't leave it
                    # to chance" pattern as the garage injection just above.
                    # blueprint_svg.py's _should_suppress_garage_direct_door()
                    # relies on this room actually existing to suppress a
                    # direct Garage<->Living/Kitchen/Dining door.
                    has_entry = any(
                        classify_room_category(str(r.get("name") or "")) == "foyer" for r in ground_floor_rooms
                    )
                    if not has_entry:
                        avg_weight = (
                            sum(float(r.get("area") or 1) for r in ground_floor_rooms) / len(ground_floor_rooms)
                            if ground_floor_rooms
                            else 1.0
                        )
                        ground_floor_rooms.append({"name": "Entry", "area": avg_weight})

                # Every floor of a multi-floor building - guarantee a REAL
                # reserved staircase room exists (2026-08-29), not just a
                # decorative symbol drawn inside whichever room happened to
                # be biggest (see blueprint_svg.py's history). Participates
                # in the same minimum-area guarantee and feasibility check as
                # any other room via room_specs.py's "staircase" category.
                if total_floors > 1:
                    for floor in room_layout["floors"]:
                        floor_rooms = floor.setdefault("rooms", [])
                        has_staircase = any(
                            classify_room_category(str(r.get("name") or "")) == "staircase" for r in floor_rooms
                        )
                        if not has_staircase:
                            avg_weight = (
                                sum(float(r.get("area") or 1) for r in floor_rooms) / len(floor_rooms)
                                if floor_rooms
                                else 1.0
                            )
                            floor_rooms.append({"name": "Staircase", "area": avg_weight})

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
                        rects = layout_floor(floor["rooms"], building_dimensions, garage_cars, facing)
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

            if _is_cancelled(session, house_project):
                logger.info(
                    "house project %s was cancelled before the floor-plan/render stage started - stopping",
                    house_project_id,
                )
                return

            # v11: the AI "Concept Layout" call is fully decoupled onto a
            # fire-and-forget daemon thread (_run_floor_plan_stage) - see
            # HOUSE_PROMPT_VERSION's comment for why. It is NEVER joined
            # here; the pipeline moves straight on to the render step below
            # without waiting for it.
            is_infeasible = feasibility_result and feasibility_result["verdict"] == "not_feasible"
            if is_infeasible:
                # Same hard gate as the blueprint stage above - an infeasible
                # program never gets an AI visualization either (it would be
                # equally misleading), so this call is skipped entirely
                # rather than run with a set of rectangles that don't
                # actually fit.
                house_project.floor_plan_status = "infeasible"
            else:
                house_project.floor_plan_status = "running"
                threading.Thread(
                    target=_run_floor_plan_stage,
                    args=(
                        house_project_id,
                        provider,
                        storage,
                        plot_description,
                        building_dimensions,
                        prompt,
                        room_layout,
                        key_prefix,
                        facing,
                    ),
                    daemon=True,
                ).start()
            session.add(house_project)
            session.commit()

            # Exterior render is NOT best-effort when a plot photo IS
            # available - it's the core paid deliverable of this feature,
            # same treatment as the room-redesign image loop, and an
            # exception here still propagates out to the outer except and
            # fails the whole project. But the render is an image-EDIT call
            # (see app/providers/openai.py/kaggle.py's generate_house_render)
            # - it has no photo to edit when the plot photo was skipped
            # (optional as of 2026-09), so it's cleanly skipped in that case
            # instead of erroring, exactly like the house_render_enabled
            # dev-only escape hatch below already does for a different
            # reason. settings.house_render_enabled (see app/config.py) -
            # when False, this call (the ONLY OpenAI/Kaggle call left in the
            # main pipeline path) is skipped too, so the project still
            # completes as "done" with whatever else succeeded.
            render_model = None
            if not settings.house_render_enabled:
                logger.info(
                    "house_render_enabled is False - skipping the render step for house project %s",
                    house_project_id,
                )
            elif plot_bytes is None:
                logger.info(
                    "no plot photo was uploaded for house project %s - skipping the exterior "
                    "render step (it has no photo to edit)",
                    house_project_id,
                )
            else:
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
