"""Single dispatcher between the two deterministic room-layout algorithms -
app/pipeline/floor_layout.py's original rectangle slice-and-dice engine
("rect") and app/pipeline/floor_layout_mask.py's grid-mask region-growing
engine ("mask", produces real L/U-shaped rooms, a real circulation spine,
and window-wall egress for every bedroom/living room BY CONSTRUCTION - see
that module's own docstring for the full rationale and the structural bugs
in the rect engine's banded private-zone packing that motivated it).

This is a pure feature-flag seam (app/config.py's `house_layout_engine`,
"rect" or "mask") - every caller that used to import layout_floor_with_
warnings()/layout_floor() directly from floor_layout.py now imports the
SAME two function names from HERE instead, with an IDENTICAL signature and
return shape either way, so routing is the only thing this module does.
The three real call sites (app/pipeline/generate_house.py's render step,
app/pipeline/feasibility.py's geometry-verified capacity probe, and
app/providers/kaggle_autocad.py's AI Concept Layout conditioning image) all
route through here - this guarantees all three ALWAYS agree on which
engine produced a given floor's geometry; feasibility.py's probe in
particular must use the exact engine that will actually render, or its
"does this fit" answer could silently disagree with the real output.

Default is "rect" - this is a rollout flag, not a replacement. The live
site keeps producing today's proven rectangular output until the mask
engine is visually verified across the full test matrix and this default
is deliberately flipped in its own commit (see the plan file's milestone
roadmap). Reverting after a flip is a one-line config change, not a code
rollback."""

import logging

from app.config import settings
from app.pipeline import floor_layout, floor_layout_mask

logger = logging.getLogger(__name__)

# Phase 7: per-floor record of how the final geometry was produced (planner
# path only; see layout_building() below).
PATH_MASK = "mask"
PATH_ALT_SHAPE = "mask_alt_shape"  # recorded as "mask_alt_shape:<n>"
PATH_RECT_FALLBACK = "rect_fallback"
PATH_BEST_EFFORT = "mask_best_effort"


def layout_floor_with_warnings(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None,
    plan=None,
) -> tuple[list[dict], list]:
    """Routes to the configured engine's own layout_floor_with_warnings() -
    see floor_layout.layout_floor_with_warnings()'s docstring for the full
    parameter/return contract, identical on both engines. `plan` (a
    house_planner.FloorPlan, mask planner Phase 2) is passed to the MASK
    engine only - the rect engine never sees it - and the mask engine itself
    ignores it unless settings.house_planner_enabled is on."""
    if settings.house_layout_engine == "mask":
        return floor_layout_mask.layout_floor_with_warnings(rooms, dimensions, garage_cars, facing, plan)
    return floor_layout.layout_floor_with_warnings(rooms, dimensions, garage_cars, facing)


def layout_floor(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None,
    plan=None,
) -> list[dict]:
    """Routes to the configured engine's own layout_floor() - plain rects/
    parts list, no warnings (see layout_floor_with_warnings() above)."""
    rects, _warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars, facing, plan)
    return rects


def floor_hard_total(rects: list[dict], dimensions: dict, floor_number: int, total_floors: int,
                     garage_cars: int | None) -> int:
    """Hard violations of one floor's final rects, including the hard
    daylight failures: layout_quality's hard kinds (measure_floor.hard_count)
    plus measure_floor.daylight_fail_hard. Engine-agnostic (works on rects in
    plot coordinates), so mask and rect results are compared on one yardstick."""
    from app.pipeline.layout_metrics import measure_floor

    m = measure_floor(rects, dimensions, total_floors=total_floors, floor_number=floor_number,
                      garage_cars=garage_cars)
    return int(m["hard_count"]) + len(m["daylight_fail_hard"])


def layout_building(
    floors_rooms: list[list[dict]],
    dimensions: dict,
    facing: str | None,
    garage_cars: int | None,
    total_floors: int | None = None,
    min_front_setback: float | None = None,
    max_replans: int = 2,
):
    """Whole-building layout for the planner path (what generate_house.py
    calls). Same contract as floor_layout_mask.layout_building(): returns
    (plan, [(rooms_used, rects, warnings), ...]). With the planner flag OFF
    this is a pure pass-through. With it ON, each floor additionally gets
    `plan.floors[i].chain_path` (see PATH_* above), logged at INFO."""
    plan, results = floor_layout_mask.layout_building(
        floors_rooms, dimensions, facing, garage_cars, total_floors, min_front_setback, max_replans
    )
    if not settings.house_planner_enabled:
        return plan, results
    total = total_floors or len(floors_rooms)
    for index, (_used, rects, _warnings) in enumerate(results):
        hard = floor_hard_total(rects, dimensions, index + 1, total, garage_cars)
        path = PATH_MASK if hard == 0 else PATH_BEST_EFFORT
        plan.floors[index].chain_path = path
        logger.info("layout_path floor %s: %s (hard=%s)", index + 1, path, hard)
    return plan, results
