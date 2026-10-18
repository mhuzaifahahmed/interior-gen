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
import time

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
    if (settings.house_layout_engine == "mask" and settings.house_planner_enabled
            and getattr(plan, "chain_path", None) == PATH_RECT_FALLBACK):
        # Phase 7: a floor the fallback chain gave to the rect engine is
        # re-laid by it here too (e.g. the AI Concept Layout conditioning
        # image), so every consumer of the persisted plan agrees.
        return floor_layout.layout_floor_with_warnings(rooms, dimensions, garage_cars, facing)
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


# ---- Phase 7.2: fallback chain (planner on only) ----
#
# Order, per BUILDING: (1) the mask result (incl. layout_building's own
# smaller-footprint search); (2) if any floor still has a hard violation,
# up to ALT_SHAPE_MAX_TRIES alternate footprint SHAPES (same area, different
# aspect: swapped / squarer / longer) are laid out, and one is kept only when
# the building's hard total is strictly lower and no floor is worse on any
# gate metric (same discipline as the smaller-footprint search); (3) each floor
# that STILL has a hard violation gets the rect engine as a last resort,
# used only when it has strictly fewer hard violations (incl. daylight);
# (4) otherwise the best mask result stays (mask_best_effort).
#
# Limits, stated plainly: the alternate shapes are a building-level change
# (one shared footprint), so floors are never mixed across shapes. The rect
# engine sizes its OWN footprint from the gross plot - a rect_fallback floor
# may not match the planner's yards/footprint or the other floors' stair
# position; it is a last resort and always visible via chain_path.
ALT_SHAPE_MAX_TRIES = 3
RECT_FALLBACK_ENABLED = True
# None = no limit. Otherwise alternate-shape tries stop once their total time
# would exceed this multiple of the first (baseline) mask layout's time.
EXTRA_TIME_FACTOR: float | None = None
_ALT_MIN_CHANGE = 0.02  # an alternate footprint must differ by >2% in a side
_ALT_ASPECT_LIMIT = 2.0


def _plan_aspect(plan, facing: str | None) -> float:
    """cross/depth of the planned footprint (depth = the facing direction)."""
    if (facing or "north").strip().lower() in ("east", "west"):
        depth, cross = plan.footprint_length, plan.footprint_width
    else:
        depth, cross = plan.footprint_width, plan.footprint_length
    return cross / depth if depth > 0 else 1.0


def alt_shape_aspects(base: float) -> list[float]:
    """Candidate aspects (cross/depth) for the retries: swapped, squarer,
    longer (more elongated), each clipped to the planner's own 1:2 bound,
    de-duplicated and at least 8% away from the base and each other."""
    longer = base * 1.4 if base >= 1.0 else base / 1.4
    out: list[float] = []
    for a in (1.0 / base, 1.0, longer):
        a = min(max(a, 1.0 / _ALT_ASPECT_LIMIT), _ALT_ASPECT_LIMIT)
        if all(abs(a - b) / b > 0.08 for b in [base, *out]):
            out.append(a)
    return out[:ALT_SHAPE_MAX_TRIES]


def _hard_totals(plan, results, dimensions, total, garage_cars) -> list[int]:
    return [floor_hard_total(r, dimensions, i + 1, total, garage_cars) for i, (_u, r, _w) in enumerate(results)]


def _chain_vector(plan, results, i, dimensions, garage_cars, facing, total) -> tuple:
    """The gate-metric vector the footprint-scale search uses, plus the
    scorecard's "parts per room <= 2" check (a room made of 3+ rectangles),
    which that vector does not cover."""
    from app.pipeline.layout_metrics import measure_floor

    vec = floor_layout_mask._scale_gate_vector(plan, results, i, dimensions, garage_cars, facing, total)
    parts = measure_floor(results[i][1], dimensions, total_floors=total, floor_number=i + 1,
                          garage_cars=garage_cars)["max_parts"]
    return vec + (int(parts > 2),)


def _try_alt_shapes(plan, results, floors_rooms, dimensions, facing, garage_cars, total,
                    min_front_setback, max_replans, base_totals, base_seconds):
    """(plan, results, n) of the best accepted alternate shape, or None."""
    started = time.perf_counter()
    base_vecs = [_chain_vector(plan, results, i, dimensions, garage_cars, facing, total)
                 for i in range(len(results))]
    best = None
    best_total = sum(base_totals)
    for n, aspect in enumerate(alt_shape_aspects(_plan_aspect(plan, facing)), start=1):
        if EXTRA_TIME_FACTOR is not None and (time.perf_counter() - started) + base_seconds > EXTRA_TIME_FACTOR * base_seconds:
            logger.info("layout chain: alt-shape budget reached after %d tries", n - 1)
            break
        p2, r2 = floor_layout_mask._layout_building_at(
            floors_rooms, dimensions, facing, garage_cars, total, min_front_setback, max_replans,
            plan.footprint_scale, aspect,
        )
        if (abs(p2.footprint_length - plan.footprint_length) <= _ALT_MIN_CHANGE * plan.footprint_length
                and abs(p2.footprint_width - plan.footprint_width) <= _ALT_MIN_CHANGE * plan.footprint_width):
            continue  # the envelope pinned the shape: nothing new to try
        totals = _hard_totals(p2, r2, dimensions, total, garage_cars)
        if len(totals) != len(base_totals) or any(a > b for a, b in zip(totals, base_totals)):
            continue
        vecs = [_chain_vector(p2, r2, i, dimensions, garage_cars, facing, total)
                for i in range(len(r2))]
        if any(any(x > y for x, y in zip(v, b)) for v, b in zip(vecs, base_vecs)):
            continue
        if sum(totals) < best_total:
            best, best_total = (p2, r2, n), sum(totals)
            if best_total == 0:
                break
    return best


def _rect_floor(used, dimensions, garage_cars, facing):
    return floor_layout.layout_floor_with_warnings(used, dimensions, garage_cars, facing)


_RECT_FOOTPRINT_TOLERANCE = 0.10


def _rect_matches_plan(plan, rects: list[dict]) -> bool:
    """True when the rect engine's own footprint is within 10% of the
    planner's on both sides - the planner's yards/coverage checks then still
    describe the drawn floor. (Measured: without this guard, rect floors
    regressed yard balance, footprint scaling and the oversized check.)"""
    from app.pipeline.layout_metrics import _bbox

    box = _bbox(rects)
    got_l, got_w = box["x1"] - box["x0"], box["y1"] - box["y0"]
    tol = _RECT_FOOTPRINT_TOLERANCE
    return (abs(got_l - plan.footprint_length) <= tol * plan.footprint_length
            and abs(got_w - plan.footprint_width) <= tol * plan.footprint_width)


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
    this is a pure pass-through. With it ON, each floor gets
    `plan.floors[i].chain_path` (PATH_* above) after the fallback chain
    described above, logged at INFO."""
    t0 = time.perf_counter()
    plan, results = floor_layout_mask.layout_building(
        floors_rooms, dimensions, facing, garage_cars, total_floors, min_front_setback, max_replans
    )
    if not settings.house_planner_enabled:
        return plan, results
    base_seconds = time.perf_counter() - t0
    total = total_floors or len(floors_rooms)
    results = list(results)
    totals = _hard_totals(plan, results, dimensions, total, garage_cars)
    chain = [PATH_MASK if t == 0 else PATH_BEST_EFFORT for t in totals]

    if any(totals) and ALT_SHAPE_MAX_TRIES > 0:
        alt = _try_alt_shapes(plan, results, floors_rooms, dimensions, facing, garage_cars, total,
                              min_front_setback, max_replans, totals, base_seconds)
        if alt is not None:
            plan, results, n = alt
            results = list(results)
            totals = _hard_totals(plan, results, dimensions, total, garage_cars)
            chain = [f"{PATH_ALT_SHAPE}:{n}"] * len(results)

    if RECT_FALLBACK_ENABLED:
        for i, (used, rects, _w) in enumerate(results):
            if totals[i] == 0:
                continue
            r_rects, r_warnings = _rect_floor(used, dimensions, garage_cars, facing)
            r_total = floor_hard_total(r_rects, dimensions, i + 1, total, garage_cars)
            if r_total >= totals[i] or not _rect_matches_plan(plan, r_rects):
                continue
            # Same all-metrics discipline as the footprint-scale search: the
            # rect floor must be no worse than the mask floor on any gate
            # metric (oversized, undersized, aspect, circulation...).
            cur_vec = _chain_vector(plan, results, i, dimensions, garage_cars, facing, total)
            trial = list(results)
            trial[i] = (used, r_rects, r_warnings)
            new_vec = _chain_vector(plan, trial, i, dimensions, garage_cars, facing, total)
            if any(x > y for x, y in zip(new_vec, cur_vec)):
                continue
            results[i] = (used, r_rects, r_warnings)
            totals[i] = r_total
            chain[i] = PATH_RECT_FALLBACK

    for i, path in enumerate(chain):
        plan.floors[i].chain_path = path
        logger.info("layout_path floor %s: %s (hard=%s)", i + 1, path, totals[i])
    return plan, results
