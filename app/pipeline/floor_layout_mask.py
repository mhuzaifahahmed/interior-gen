"""Grid-mask region-growing layout engine - the "boolean-mask" foundation
for real L/U-shaped rooms, a real circulation spine, and window-wall
egress for every bedroom/living room GUARANTEED BY CONSTRUCTION, not by a
soft scoring penalty.

Why this exists: app/pipeline/floor_layout.py's original rectangle slice-
and-dice engine can only ever give a room ONE axis-aligned rectangle. On a
dense floor, the private zone's banded packing (see that module's own
"NO-BOWLING-ALLEY ROOMS" section) puts some bedrooms in a "Band 2" with NO
exterior wall at all - layout_quality.py's soft EGRESS_PENALTY can only
ever decide WHICH room gets buried, since on a crowded floor some
rectangle-shaped room was always going to end up landlocked no matter how
the scorer is tuned. This is a structural ceiling of the representation,
not a tunable bug - the actual fix is letting a room be more than one
rectangle, so a deep room can grow a thin ARM out to an exterior wall.

Algorithm (fully deterministic - identical input always produces identical
output, same doctrine as floor_layout.py):

  1. GRID. The net footprint box is discretized into a uniform grid of
     roughly-square cells (~40 along the longer side, clamped - see
     _TARGET_CELLS_LONG_SIDE).
  2. SPINE. A circulation corridor is reserved FIRST, before any room
     claims a cell - a full-depth, entrance-to-back vertical spine, plus
     (on a large enough room program) a perpendicular cross-link, so no
     interior room is ever more than one quadrant away from TWO different
     corridor segments. This is a deliberately simple, deterministic
     approximation of "open pathways" - a cross-spine, NOT a literal
     pathfound ring loop (see _reserve_spine()'s own docstring for why that
     's a stated, honest simplification, not an oversight).
  3. SEED. One anchor cell per room. This is what actually GUARANTEES
     egress: a bedroom/living room's seed cell is placed ON the floor's
     own exterior boundary, and because growth (step 4) is always a
     connected flood-fill outward from the seed, that boundary cell can
     never stop being part of the room's final shape no matter how far or
     which direction it grows from there - egress is a property of WHERE
     growth starts, not something scored after the fact.
  4. GROW. Deterministic round-robin region growing (BFS): each round,
     every room that hasn't yet reached its own real PREFERRED area
     (room_specs.preferred_area_for_room(), not Gemini's relative weight -
     see the module-level note on this simplification) claims one more
     frontier cell, preferring whichever keeps its shape most compact.
     Growth never crosses a spine cell or another room's already-claimed
     cell - a room reaching past an obstacle has to wrap AROUND it, which
     is where L/U shapes come from.
  5. FILL. Any leftover unclaimed interior cell (growth can stall before
     the grid is fully claimed) is absorbed by whichever adjacent room is
     furthest under its own target - the footprint always tiles with NO
     holes (a hole would render as an unexplained solid black poché block).
  6. DECOMPOSE. Each room's claimed cell-set is converted into a minimal,
     deterministic set of axis-aligned rectangles ("parts") via a greedy
     maximal-rectangle run-merge - a plain rectangular room always yields
     exactly 1 part (byte-compatible with every pre-existing single-rect
     consumer); an L yields 2; a U yields 3.
  7. FACING + ANCHOR + NET-OFFSET. The exact same compass-facing coordinate
     transform floor_layout.layout_floor_with_warnings() applies (front-
     at-low-y core convention, transposed for east/west, mirrored for
     south/east, anchored within any real yard) - applied here to every
     PART as well as each room's own bounding box, via _anchor_offsets()
     (reused directly from floor_layout.py - pure coordinate math with no
     engine-specific logic).
  8. BEST-OF-N. A small, deterministic set of candidates (varying the
     spine's width and whether the cross-link is built) is scored by the
     SAME layout_quality.score_floor_layout() the rect engine uses (now
     parts-aware) and the highest-scoring candidate wins - same
     "best-of-SCORED-CANDIDATES, not a guarantee" doctrine as the rect
     engine (see layout_quality.py's own module docstring).

Reuses, not reimplements: app/pipeline/room_specs.py (min/preferred/max
area, classify_room_category, wall thickness) for every sizing decision;
floor_layout.py's zone ordering (_sort_key/_zone_key) and suite pairing
(_arrange_suites, including its wet-wall-leftover grouping) for room
ORDER, which this module's seeding then turns into spatial adjacency; and
floor_layout.py's own _anchor_offsets()/_footprint_target_area() for the
facing transform and footprint-sizing formula. This module is a new
PLACEMENT algorithm on the same semantics, not a parallel classification
system.

Known, stated simplifications for this first pass (not silently glossed
over - tracked for a later milestone, not hidden as if solved):
  - The circulation "loop" is a cross-spine, not a literal pathfound ring
    (see _reserve_spine()).
  - Footprint sizing here uses a fixed overhead multiplier on top of the
    rect engine's own target-area formula, not that engine's geometry-
    verification probe (which calls the RECT engine's own internals
    directly, so it can't be reused as-is here) - a looser footprint (more
    yard than strictly necessary) is the accepted cost of keeping this
    module self-contained, relying on its own min/max-area guarantees for
    correctness instead of a pre-verification pass.
  - Growth targets each room's own real PREFERRED area directly
    (room_specs.py), not Gemini's relative "area" weight scaled above a
    guaranteed minimum (the rect engine's own mechanism) - simpler, and
    reasonable since the footprint is already sized around the sum of real
    preferred areas.
  - Garage/staircase don't get an explicit width x depth shape carve-out
    the way the rect engine's _slice_reserving_garage() gives them - they
    grow like any other room, bounded by real area (min==max, since both
    are pinned categories) but not an explicit aspect ratio. Flagged
    honestly, not claimed as solved.
"""

import heapq
import math
from collections import deque

from app.pipeline.floor_layout import (
    _VALID_FACINGS,
    _anchor_offsets,
    _arrange_suites,
    _footprint_target_area,
    _sort_key,
    _zone_key,
)
from app.config import settings
from app.pipeline.layout_quality import (
    NOTCH_PENALTY,
    WRAPPED_PENALTY,
    Violation,
    score_floor_layout,
    secondary_part_ok,
)
from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    garage_dimensions,
    max_area_for_room,
    min_area_for_room,
    min_width_for_room,
    preferred_area_for_room,
    target_area_for_room,
    to_plot_unit,
)

# ---- Grid resolution ----
# A uniform cell size targeting roughly this many cells along the
# footprint's longer side - fine enough for real L/U detail, coarse enough
# to stay fast (a ~40x40 grid is ~1600 cells; region growing over that is
# milliseconds) and for decomposed rectangles to not fragment into dozens
# of slivers.
_TARGET_CELLS_LONG_SIDE = 40
_MIN_CELLS_PER_AXIS = 6
_MAX_CELLS_PER_AXIS = 60

_EGRESS_CATEGORIES = ("bedroom", "living")

# ---- Phase D2: wet cluster (2026-10-12) ----
# Service rooms that architecturally belong BESIDE the kitchen (a "wet wall"
# cluster sharing plumbing/ventilation). When a kitchen exists on the SAME
# floor, these seed directly behind that floor's kitchen (the first private
# row at the kitchen's column), so they share the public-band boundary wall
# with it instead of landing at the far back with the bedrooms. This is a
# mask-engine-LOCAL seeding bias only - it does NOT re-zone these rooms into
# floor_layout._PUBLIC_ZONE_KEYWORDS (explicitly forbidden, §1.5 decision 4,
# since that touches the frozen rect engine). They stay private-zone rooms
# (grow behind the band); only their SEED position is biased to the kitchen.
# Applied per-floor automatically (the kitchen lookup is over THIS floor's
# rooms), so a kitchen+laundry requested on an upper floor clusters there too.
_WET_CLUSTER_CATEGORIES = ("laundry", "pantry", "dirty_kitchen")

# Circulation spine width - the same real-world clamps floor_layout.py's
# own hallway uses, duplicated (not imported) since this module computes
# width in CELLS, not real units, from the start.
_HALLWAY_MIN_WIDTH_M = 1.2
_HALLWAY_MAX_WIDTH_M = 3.0
_HALLWAY_WIDTH_FRACTION = 0.14

_MIN_ROOMS_FOR_SPINE = 3
_MIN_ROOMS_FOR_CROSS_LINK = 5

# ---- Phase D0: front public band (2026-10-12) ----
# Public-zone rooms (living/dining/kitchen/entry/garage) are confined to a
# shallow FRONT BAND spanning the full footprint width, and the circulation
# spine starts BELOW that band - so public rooms grow wide-and-shallow (near
# square) instead of diving the full depth of a narrow spine-split half-
# column and wrapping around the back bedrooms in bowling-alley L/U shapes
# (the Phase A "public rooms are slivers in every case" finding - root-caused
# via owner-grid tracing to unbounded public-room DEPTH growth, not seed
# position: see _grow_rooms()'s own band gate). The band depth is sized so
# every public room's own preferred area fits across the full width, clamped
# to at most _MAX_PUBLIC_BAND_FRACTION of the floor so the private zone + the
# spine always keep real depth behind it.
_MAX_PUBLIC_BAND_FRACTION = 0.5

# Best-of-N candidate knobs (2 x 2 = 4) - smaller than the rect engine's 10
# since each mask-engine candidate is a full grid-growth pass, not just
# rect arithmetic. 0 listed first so a tie favors the plainest spine.
_CANDIDATE_SPINE_WIDTH_DELTAS = (0, 1)
_CANDIDATE_CROSS_LINK = (True, False)

# Phase 3 (planner path only): widened best-of-N - (spine width delta,
# cross-link, front-band depth delta, reversed egress perimeter order). The
# first four are the original 2x2 set. Only the best _PLANNER_RECTIFY_TOP_K
# (ranked before rectification) are rectified and fully scored.
_PLANNER_CANDIDATES = (
    (0, True, 0, False), (0, False, 0, False), (1, True, 0, False), (1, False, 0, False),
    (0, True, 1, False), (0, False, 1, False), (0, True, -1, False), (0, False, -1, False),
    (0, True, 0, True), (0, False, 0, True),
)
_PLANNER_RECTIFY_TOP_K = 3
_PHASE2_VARIANT_COUNT = 4
# Phase 3: False = planner bonus rooms are kept/dropped exactly as Phase 2
# decided (on the unrectified reference layout). True = decide on the
# rectified result instead (keeps more bonus rooms; see the Phase 3 report -
# it can trade one floor's gate metric for a bigger house, so it is off).
_BONUS_DECISIONS_ON_RECTIFIED = False  # the first 4 _PLANNER_CANDIDATES are Phase 2's set

_GATE_SEPARATE_KINDS = ("oversized", "hierarchy_inverted", "no_daylight", "small_area")


def _worst_over_ratio(rooms: list[dict], room_max: dict) -> float:
    """Largest area / max over habitable rooms (stair/garage/hallway excluded)
    - an oversized room may not get MORE oversized than in the reference."""
    worst = 0.0
    for r in rooms:
        name = str(r.get("name") or "")
        if classify_room_category(name) in ("hallway", "staircase", "garage"):
            continue
        mx = room_max.get(name)
        if not mx:
            continue
        area = sum(p["w"] * p["h"] for p in (r.get("parts") or [r]))
        worst = max(worst, area / mx)
    return worst


def _gate_vector(violations: list) -> tuple[int, ...]:
    """Counts of the hard violations the mask quality gate ratchets
    separately (oversized, inverted hierarchy, daylight, undersized) plus all
    other hard kinds together; `wrapped` (shape) is deliberately excluded."""
    hard = [v for v in violations if v.hard and v.kind != "wrapped"]
    other = sum(1 for v in hard if v.kind not in _GATE_SEPARATE_KINDS)
    # The gate ignores stair/garage size (pinned), so count those apart.
    pinned_over = sum(
        1 for v in hard
        if v.kind == "oversized" and classify_room_category(v.room) in ("staircase", "garage")
    )
    counts = tuple(sum(1 for v in hard if v.kind == k) for k in _GATE_SEPARATE_KINDS)
    return (other, counts[0] - pinned_over, pinned_over) + counts[1:]

# Extra safety margin (on top of floor_layout._footprint_target_area()'s
# own preferred-area-sum + overhead) - see module docstring's "Known
# simplifications" for why this engine uses a fixed multiplier instead of
# the rect engine's geometry-verification probe.
FOOTPRINT_EXTRA_MARGIN = 1.3

# ATTEMPTED 2026-10, REVERTED THE SAME DAY - kept at 1.0 (a true no-op) as
# an honest record, not deleted outright. The investigation that led here
# (a sparse room program produced a tiny house regardless of plot size, and
# feasibility.py's capacity table reported 0 bedrooms/0 bathrooms fit on
# ANY plot size) correctly found _mask_footprint_dimensions()'s own
# max_absorbable cap (below) had ZERO slack - but giving it even a modest
# 1.3x slack reintroduced the EXACT "a room balloons to several times its
# own real maximum area" bug max_absorbable was originally built to prevent
# (confirmed live: Master Bedroom reached 1,009.8 sq ft against its own
# 323.9 sq ft absolute cap, a 3x overshoot, on a regression test that used
# to pass cleanly). _fill_leftovers()'s own excess-redistribution fallback
# is "deliberately UNBOUNDED by max_cells" (see that function's own
# docstring - capping it was tried twice before and reverted both times for
# making sparse programs worse) - so ANY extra footprint slack beyond the
# bare max_absorbable sum has nowhere bounded to go once every room's own
# max_cells is reached. The real fix for "capacity table reports too few
# rooms fit" turned out to live elsewhere (see
# layout_quality.py's largest-part-aware narrow check and
# _public_band_rows()'s min-width floor, both kept) - NOT in loosening this
# specific cap. Left at 1.0 so a future attempt doesn't have to
# rediscover this the hard way.
FOOTPRINT_ABSORBABLE_SLACK = 1.0

# ---- Phase C0: auto site-planning (2026-10) ----
# "This is going to be used for HOUSES" (explicit user directive) - a large
# residential plot must always leave real yard around the house, not sprawl
# edge-to-edge just because the room program happens to be big enough to
# absorb that much area. Values DECIDED by the user (see the plan file's
# §1.5 "RESOLVED DECISIONS" - do not re-tune without the Phase F rendered
# matrix making them look visibly wrong):
#   - AUTO_YARD_PLOT_THRESHOLD_M: below this (on the plot's SHORTER net
#     side), no auto-yard behavior triggers at all - a compact/urban lot
#     keeps filling edge-to-edge exactly as before this feature existed.
#   - MAX_PLOT_COVERAGE: once over threshold, the footprint is additionally
#     capped at this fraction of the net plot area, so there is always real
#     yard left over regardless of how large the room program's own target
#     area would otherwise have been.
#   - MIN_FRONT_SETBACK_M: a real push-back of the footprint's own facing
#     edge, so a driveway + a parked car has somewhere to actually fit
#     between the house and the road, instead of all the coverage-cap
#     slack landing entirely at the rear (see _front_setback_offset()).
# Both are strict no-ops on a small/right-sized plot: the coverage cap only
# ever TIGHTENS an already-larger target_area (min() with the existing
# value), and the setback is bounded by the real slack the coverage cap (or
# a naturally small room program) actually freed up - never larger than
# that, never negative.
AUTO_YARD_PLOT_THRESHOLD_M = 12.0
MAX_PLOT_COVERAGE = 0.60
MIN_FRONT_SETBACK_M = 5.5


def layout_floor(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None,
    plan=None,
) -> list[dict]:
    """Same contract as floor_layout.layout_floor() - plain rects/parts
    list, no warnings."""
    rects, _warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars, facing, plan)
    return rects


def layout_floor_with_warnings(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None,
    plan=None,
) -> tuple[list[dict], list]:
    """Same contract as floor_layout.layout_floor_with_warnings() - see
    that function's docstring for the parameter/return shape, identical on
    both engines. Returns rooms in [{"name","x","y","w","h","parts"?,
    "suite"?}, ...] shape - "parts" is present (a list of >=2 {"x","y","w",
    "h"} dicts) only for an L/U-shaped room; a plain-rectangle room has no
    "parts" key at all, so every pre-existing single-rect consumer that
    doesn't yet know about `floor_layout.room_parts()` still works
    unchanged on this engine's plain-rectangle rooms."""
    if not rooms:
        return [], []

    facing = (facing or "north").strip().lower()
    if facing not in _VALID_FACINGS:
        facing = "north"

    # Phase 2 planner path (flag-gated). `plan` is a house_planner.FloorPlan;
    # with the flag OFF it is ignored entirely and everything below runs
    # exactly as before (byte-identical).
    if settings.house_planner_enabled:
        return _layout_with_plan(rooms, dimensions, garage_cars, facing, plan)

    real_length = float(dimensions.get("length") or 1)
    real_width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"

    exterior = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    net_length = max(real_length - 2 * exterior, 1.0)
    net_width = max(real_width - 2 * exterior, 1.0)

    footprint_length, footprint_width = _mask_footprint_dimensions(
        rooms, net_length, net_width, unit, garage_cars
    )

    if facing in ("east", "west"):
        core_dimensions = {"length": footprint_width, "width": footprint_length, "unit": unit}
    else:
        core_dimensions = {"length": footprint_length, "width": footprint_width, "unit": unit}

    ordered_rooms = _order_rooms(rooms)
    targets = _room_targets(ordered_rooms, unit, garage_cars)

    best_rooms: list[dict] | None = None
    best_score: float | None = None
    best_warnings: list = []
    for width_delta in _CANDIDATE_SPINE_WIDTH_DELTAS:
        for cross_link in _CANDIDATE_CROSS_LINK:
            candidate = _grow_floor(targets, core_dimensions, width_delta, cross_link, garage_cars)
            scored = score_floor_layout(candidate, core_dimensions, garage_cars)
            if best_score is None or scored.score > best_score:
                best_score = scored.score
                best_rooms = candidate
                best_warnings = scored.violations

    rooms_out = best_rooms or []
    rooms_out = _apply_facing_transform(
        rooms_out, facing, footprint_length, footprint_width, net_length, net_width,
        exterior, real_length, real_width, unit,
    )
    return rooms_out, best_warnings


# ---- Phase 2: planner path (Settings.house_planner_enabled) ----


def _layout_with_plan(
    rooms: list[dict], dimensions: dict, garage_cars: int | None, facing: str, plan
) -> tuple[list[dict], list]:
    """Planner path: footprint + yards + per-room targets come from a
    house_planner.FloorPlan; this engine only shapes rooms inside it. When
    `plan` is None (feasibility probe, kaggle conditioning image) a single-
    floor plan is built here. Rooms the plan ADDED (bonus rooms, Terrace) are
    appended if the caller did not already include them."""
    from app.pipeline.house_planner import plan_building

    unit = dimensions.get("unit") or "ft"
    if plan is None:
        already_planned = any(r.get("planner_added") for r in rooms)
        if not already_planned:
            _plan, results = layout_building([rooms], dimensions, facing, garage_cars, 1)
            _rooms, rects, warnings = results[0]
            return rects, warnings
        plan = plan_building([rooms], dimensions, facing, garage_cars, 1, add_bonus=False).floors[0]
    names = {str(r.get("name") or "") for r in rooms}
    rooms = list(rooms) + [dict(a) for a in plan.added_rooms if a["name"] not in names]

    fp_length, fp_width = plan.footprint_length, plan.footprint_width
    if facing in ("east", "west"):
        core_dimensions = {"length": fp_width, "width": fp_length, "unit": unit}
    else:
        core_dimensions = {"length": fp_length, "width": fp_width, "unit": unit}

    def run(room_list: list[dict]) -> tuple[list[dict], list]:
        targets = _room_targets(_order_rooms(room_list), unit, garage_cars)
        for t in targets:
            mx = plan.room_max.get(t["name"]) or t["max_area"]
            t["max_area"] = max(mx, t["min_area"])
            tgt = plan.room_targets.get(t["name"])
            if tgt is None:
                tgt = target_area_for_room(t["name"], unit, garage_cars)
            t["target_area"] = min(max(tgt, t["min_area"]), t["max_area"])
        room_targets = {t["name"]: t["target_area"] for t in targets}
        room_max = {t["name"]: t["max_area"] for t in targets}
        daylight_fp = {"x0": 0.0, "y0": 0.0, "x1": core_dimensions["length"], "y1": core_dimensions["width"]}

        def score(candidate):
            return score_floor_layout(
                candidate, core_dimensions, garage_cars, daylight_footprint=daylight_fp,
                planner_checks=True, room_targets=room_targets, room_max=room_max,
            )

        # Phase 3: widened best-of-N, two stages. Stage 1 grows every variant
        # WITHOUT rectification (cheap) and ranks them ignoring shape
        # violations (rectification changes shapes); stage 2 rectifies and
        # fully scores only the best _PLANNER_RECTIFY_TOP_K.
        ranked = []
        seen_layouts: set = set()
        reference = None  # the Phase-2 pick: best original variant, unrectified
        for order, variant in enumerate(_PLANNER_CANDIDATES):
            width_delta, cross_link, band_delta, flip = variant
            stats1: dict = {}
            candidate = _grow_floor(
                targets, core_dimensions, width_delta, cross_link, garage_cars, planner=True, stats=stats1,
                band_delta=band_delta, flip_egress=flip, rectify=False,
            )
            signature = tuple(
                (r["name"], round(r["x"], 6), round(r["y"], 6), round(r["w"], 6), round(r["h"], 6), len(r.get("parts") or ()))
                for r in candidate
            )
            if signature in seen_layouts:
                continue  # several variants can grow the identical layout
            seen_layouts.add(signature)
            s1 = score(candidate)
            shape_free = s1.score + sum(
                (WRAPPED_PENALTY if v.kind == "wrapped" else NOTCH_PENALTY)
                for v in s1.violations if v.kind in ("wrapped", "notch")
            )
            ranked.append((-shape_free, order, variant))
            if order < _PHASE2_VARIANT_COUNT and (reference is None or shape_free > reference[0]):
                reference = (shape_free, variant, candidate, s1, stats1)
        ranked.sort()
        stage2 = [v for _neg, _order, v in ranked[:_PLANNER_RECTIFY_TOP_K]]
        if reference is not None and reference[1] not in stage2:
            stage2.append(reference[1])
        options = []
        for variant in stage2:
            width_delta, cross_link, band_delta, flip = variant
            stats: dict = {}
            candidate = _grow_floor(
                targets, core_dimensions, width_delta, cross_link, garage_cars, planner=True, stats=stats,
                band_delta=band_delta, flip_egress=flip,
            )
            options.append((candidate, score(candidate), stats))
        if reference is not None:
            # The Phase-2 pick with only its stranded pieces reconnected.
            width_delta, cross_link, band_delta, flip = reference[1]
            stats_r: dict = {}
            reconnected = _grow_floor(
                targets, core_dimensions, width_delta, cross_link, garage_cars, planner=True, stats=stats_r,
                band_delta=band_delta, flip_egress=flip, rectify="reconnect",
            )
            options.append((reconnected, score(reconnected), stats_r))
            options.append((reference[2], reference[3], reference[4]))
            # A rectified candidate may only win if it is no worse than the
            # Phase-2 pick on every metric the quality gate ratchets - so
            # Phase 3 can never trade a gate metric for a better shape.
            # Tiers: rectified candidates first; then the reconnected
            # reference; the raw reference only when nothing else qualifies.
            ref_vec = _gate_vector(reference[3].violations)
            ref_worst = _worst_over_ratio(reference[2], room_max)
            ok = [
                all(x <= y for x, y in zip(_gate_vector(o[1].violations), ref_vec))
                and _worst_over_ratio(o[0], room_max) <= ref_worst + 1e-9
                for o in options
            ]
            n_rect = len(options) - 2
            eligible = [o for o, good in zip(options[:n_rect], ok[:n_rect]) if good]
            if not eligible:
                eligible = [options[-2]] if ok[-2] else [options[-1]]
        else:
            eligible = options
        best_rooms: list[dict] | None = None
        best_score: float | None = None
        best_warnings: list = []
        best_stats: dict = {}
        for candidate, scored, stats in eligible:
            if best_score is None or scored.score > best_score:
                best_score = scored.score
                best_rooms = candidate
                best_warnings = list(scored.violations)
                best_stats = stats
        if best_stats.get("overflow_cells") or best_stats.get("hallway_unaligned_cells"):
            best_warnings.append(
                Violation(
                    "__floor__", "fill_overflow", False,
                    f"bounded fill: {best_stats.get('overflow_cells', 0)} cells had no neighbour with headroom "
                    f"({', '.join(best_stats.get('overflow_rooms', []))}); "
                    f"{best_stats.get('over_max_after_rebalance', 0)} cells still over a max after rebalancing; "
                    f"{best_stats.get('hallway_unaligned_cells', 0)} cells absorbed into a non-aligned hallway pocket",
                )
            )
        # Bonus keep/drop decisions are made on the Phase-2 reference layout's
        # warnings (see _BONUS_DECISIONS_ON_RECTIFIED), so the building's room
        # set - and with it every floor's plan - is exactly Phase 2's.
        decide = best_warnings
        if reference is not None and not _BONUS_DECISIONS_ON_RECTIFIED:
            decide = [v for v in reference[3].violations if v.kind not in ("wrapped", "notch")]
        return best_rooms or [], best_warnings, decide

    def hard_count(warnings: list) -> int:
        # Shape (`wrapped`, Phase 3) is repaired by rectification, not by
        # dropping rooms - dropping a bonus room to fix a shape would only
        # shrink the house.
        return sum(1 for v in warnings if v.hard and v.kind != "wrapped")

    rects, warnings, decide = run(rooms)
    # Bonus rooms are added only where they actually fit: lowest priority
    # first, a planner-added SPARE-AREA room is dropped when it carries a hard
    # violation itself, or the floor lays out with fewer hard violations
    # without it - but never when dropping it would push its area into an
    # oversized room instead. (The Terrace - the
    # upper-floor remainder - is never dropped.)
    from app.pipeline.house_planner import ADDED_REASON_SPARE

    bonus = [r["name"] for r in rooms if r.get("planner_added") and r.get("added_reason") == ADDED_REASON_SPARE]
    def oversized_count(warnings: list) -> int:
        return sum(1 for v in warnings if v.kind == "oversized")

    def involves(warnings: list, name: str) -> bool:
        return any(v.hard and v.kind != "wrapped" and name in v.room.split("<->") for v in warnings)

    for name in reversed(bonus):
        current = hard_count(decide)
        if current == 0:
            break
        trial_rooms = [r for r in rooms if str(r.get("name") or "") != name]
        t_rects, t_warnings, t_decide = run(trial_rooms)
        # Dropped when the bonus room itself did not fit (it carries a hard
        # violation) and removing it does not make the floor worse, or when
        # the floor is strictly better without it.
        t_hard = hard_count(t_decide)
        no_new_oversize = oversized_count(t_decide) <= oversized_count(decide)
        if no_new_oversize and (t_hard < current or (involves(decide, name) and t_hard <= current)):
            rooms, rects, warnings, decide = trial_rooms, t_rects, t_warnings, t_decide
            warnings.append(Violation(name, "planner_room_dropped", False, "bonus room did not fit cleanly"))

    rooms_out = _apply_plan_transform(rects, facing, fp_length, fp_width, plan.yards)
    return rooms_out, warnings


def layout_building(
    floors_rooms: list[list[dict]],
    dimensions: dict,
    facing: str | None,
    garage_cars: int | None,
    total_floors: int | None = None,
    min_front_setback: float | None = None,
    max_replans: int = 2,
):
    """Planner path for a whole building: plan ONE BuildingPlan, lay out every
    floor inside it, and - when the engine dropped a bonus room it could not
    fit cleanly - re-plan WITHOUT that room (so the footprint shrinks rather
    than the spare area overflowing another room) and lay out again, at most
    `max_replans` times. Returns (plan, [(rooms_used, rects, warnings), ...])
    in floor order; rooms_used includes the planner-added rooms that were
    actually drawn (each carries planner_added / added_reason)."""
    from app.pipeline.house_planner import plan_building

    total_floors = total_floors or len(floors_rooms)
    exclude: dict[int, set] = {}
    plan = None
    results: list = []
    for _attempt in range(max_replans + 1):
        plan = plan_building(
            floors_rooms, dimensions, facing, garage_cars, total_floors,
            min_front_setback=min_front_setback, exclude_bonus=exclude or None,
        )
        results = []
        dropped_any = False
        for index, rooms in enumerate(floors_rooms):
            fplan = plan.floors[index]
            names = {str(r.get("name") or "") for r in rooms}
            used = list(rooms) + [dict(a) for a in fplan.added_rooms if a["name"] not in names]
            rects, warnings = layout_floor_with_warnings(used, dimensions, garage_cars, facing, plan=fplan)
            drawn = {str(r.get("name") or "") for r in rects}
            dropped = {a["name"] for a in fplan.added_rooms if a["name"] not in drawn}
            if dropped:
                exclude.setdefault(index + 1, set()).update(dropped)
                dropped_any = True
            used = [r for r in used if not r.get("planner_added") or r["name"] in drawn]
            results.append((used, rects, warnings))
        if not dropped_any:
            break
    for index, (used, _rects, _w) in enumerate(results):
        kept = {str(r.get("name") or "") for r in used}
        plan.floors[index].added_rooms = [a for a in plan.floors[index].added_rooms if a["name"] in kept]
    return plan, results


def _apply_plan_transform(rooms: list[dict], facing: str, fp_length: float, fp_width: float, yards: dict) -> list[dict]:
    """Core (front at low-y) -> plot coordinates for the planner path:
    transpose for east/west, mirror inside the footprint for south/east,
    then translate by the plan's own left/top yard (gross distance from the
    plot edge - it already includes the exterior wall band)."""
    if facing in ("east", "west"):
        rooms = [_map_room(r, lambda p: {**p, "x": p["y"], "y": p["x"], "w": p["h"], "h": p["w"]}) for r in rooms]
    if facing == "south":
        rooms = [_map_room(r, lambda p: {**p, "y": fp_width - p["y"] - p["h"]}) for r in rooms]
    elif facing == "east":
        rooms = [_map_room(r, lambda p: {**p, "x": fp_length - p["x"] - p["w"]}) for r in rooms]
    dx, dy = float(yards.get("left", 0.0)), float(yards.get("top", 0.0))
    return [_map_room(r, lambda p: {**p, "x": p["x"] + dx, "y": p["y"] + dy}) for r in rooms]


def _fit_cells(idx: list[int], min_c: list[int], tgt_c: list[int], max_c: list[int], available: int) -> None:
    """Rescales tgt_c[i] for i in idx (in place) so they sum to `available`
    cells, staying within [min_c, max_c]: raised toward max (proportional to
    headroom) when there is spare room, lowered toward min (proportional to
    slack above min) when there is not. Deterministic integer rounding."""
    if not idx or available <= 0:
        return
    current = sum(tgt_c[i] for i in idx)
    if current == available:
        return
    if current < available:
        room = {i: max(max_c[i] - tgt_c[i], 0) for i in idx}
        sign = 1
    else:
        room = {i: max(tgt_c[i] - min_c[i], 0) for i in idx}
        sign = -1
    total_room = sum(room.values())
    if total_room <= 0:
        return
    need = min(abs(available - current), total_room)
    shares = {i: need * room[i] / total_room for i in idx}
    given = {i: min(int(shares[i]), room[i]) for i in idx}
    left = need - sum(given.values())
    for i in sorted(idx, key=lambda j: (-(shares[j] - int(shares[j])), j)):
        if left <= 0:
            break
        if given[i] < room[i]:
            given[i] += 1
            left -= 1
    for i in idx:
        tgt_c[i] += sign * given[i]


def _mask_footprint_dimensions(
    rooms: list[dict], net_length: float, net_width: float, unit: str, garage_cars: int | None
) -> tuple[float, float]:
    """Simpler than floor_layout._footprint_dimensions() - reuses its
    target-area formula (preferred-area sum + wall/circulation overhead)
    but applies one fixed safety margin instead of a geometry-verification
    probe (that probe calls the RECT engine's own _layout_floor_core
    directly, so it can't be reused here - see module docstring). A strict
    no-op whenever the plot is already right-sized/smaller, exactly like
    the rect engine's version.

    Real bug fixed during development (found by rendering a genuinely
    minimal 4-room program and seeing a bedroom balloon to several times
    its own real maximum area): FOOTPRINT_EXTRA_MARGIN alone can still
    size a footprint LARGER than every room's own real maximum area could
    ever absorb, for a SPARSE room program specifically - a sparse program
    also has no circulation spine at all (below _MIN_ROOMS_FOR_SPINE), so
    there's nowhere else for that extra margin to go except overflowing
    straight past some room's real cap in _fill_leftovers(). Clamped here
    by the sum of every room's own real max_area_for_room() too - the
    footprint can never be sized bigger than what the room program could
    ever legitimately use, regardless of how generous the margin is."""
    plot_area = net_length * net_width
    if plot_area <= 0:
        return net_length, net_width
    target_area = _footprint_target_area(rooms, unit, garage_cars) * FOOTPRINT_EXTRA_MARGIN
    max_absorbable = sum(
        max_area_for_room(str(r.get("name") or ""), unit, garage_cars) for r in rooms
    )
    if max_absorbable > 0:
        target_area = min(target_area, max_absorbable * FOOTPRINT_ABSORBABLE_SLACK)

    # Phase C0 auto site-planning: a genuinely large plot always keeps real
    # yard, even when the room program is big enough to otherwise want more
    # than that - see the module-level constants' own docstring. `min()`
    # only ever TIGHTENS target_area (never raises it), so this is a strict
    # no-op whenever the room program's own natural target was already
    # smaller than the coverage cap, and a strict no-op whenever the plot's
    # shorter side doesn't clear the threshold at all (a compact/urban lot).
    threshold = to_plot_unit(AUTO_YARD_PLOT_THRESHOLD_M, unit)
    if min(net_length, net_width) > threshold:
        target_area = min(target_area, plot_area * MAX_PLOT_COVERAGE)

    if target_area <= 0 or target_area >= plot_area:
        return net_length, net_width
    aspect = net_length / net_width if net_width else 1.0
    width = (target_area / aspect) ** 0.5 if aspect else 0.0
    length = target_area / width if width else 0.0
    if length <= 0 or width <= 0:
        return net_length, net_width
    return min(length, net_length), min(width, net_width)


def _order_rooms(rooms: list[dict]) -> list[dict]:
    """Returns rooms re-ordered by the SAME zone/suite logic the rect
    engine uses - floor_layout._sort_key() for public/circulation/private
    zoning + front-of-house ordering within the public zone, then
    floor_layout._arrange_suites() for bedroom+bathroom suite pairing and
    wet-wall-leftover grouping within the circulation+private tail. Returns
    [{"name","suite"}, ...] - this module's seeding step (_seed_rooms())
    turns this ORDER into real spatial adjacency."""
    names = [str(r.get("name") or "") for r in rooms]
    order = sorted(range(len(names)), key=lambda i: _sort_key(names[i]))
    names = [names[i] for i in order]

    private_start = next((i for i, n in enumerate(names) if _zone_key(n) != 0), len(names))
    head_names = names[:private_start]
    tail_names = names[private_start:]
    if tail_names:
        tail_names, _tail_weights, tail_suites = _arrange_suites(tail_names, [1.0] * len(tail_names))
    else:
        tail_suites = []
    head_suites: list[int | None] = [None] * len(head_names)

    return [
        {"name": n, "suite": s}
        for n, s in zip(head_names + tail_names, head_suites + tail_suites)
    ]


def _room_targets(ordered_rooms: list[dict], unit: str, garage_cars: int | None) -> list[dict]:
    """Attaches real category + min/preferred/max AREA (plot-unit squared)
    per room, using the exact same room_specs.py functions the rect engine
    relies on."""
    targets = []
    for r in ordered_rooms:
        name = r["name"]
        targets.append(
            {
                "name": name,
                "suite": r.get("suite"),
                "category": classify_room_category(name),
                "min_area": min_area_for_room(name, unit, garage_cars),
                "preferred_area": preferred_area_for_room(name, unit, garage_cars),
                "max_area": max_area_for_room(name, unit, garage_cars),
            }
        )
    return targets


def _grow_floor(
    targets: list[dict], dimensions: dict, spine_width_delta: int, cross_link: bool,
    garage_cars: int | None = None, planner: bool = False, stats: dict | None = None,
    band_delta: int = 0, flip_egress: bool = False, rectify: bool | str = True,
) -> list[dict]:
    # Phase 3: rectify=False also keeps the legacy row-major decomposition, so
    # a planner candidate grown without rectification is exactly what Phase 2
    # produced (the reference the quality gate's baseline was built from).
    """One candidate: grid + spine + seed + grow + fill + decompose.
    Returns room dicts in CORE coordinates (front always at low-y - see
    layout_floor_with_warnings()'s facing transform), tiling
    [0,length]x[0,width] exactly with no holes. The circulation spine
    becomes its own "Hallway" room (same name the rect engine's own
    corridor uses) so the tiling stays complete and blueprint_svg.py's
    existing hallway-adjacency door logic keeps working unchanged."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"

    cell_size = max(max(length, width, 1.0) / _TARGET_CELLS_LONG_SIDE, 0.01)
    cols = _clamp(round(length / cell_size), _MIN_CELLS_PER_AXIS, _MAX_CELLS_PER_AXIS)
    rows = _clamp(round(width / cell_size), _MIN_CELLS_PER_AXIS, _MAX_CELLS_PER_AXIS)
    cell_w = length / cols
    cell_h = width / rows
    cell_area = cell_w * cell_h

    public_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 0]
    circulation_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 1]
    private_idx = [i for i, t in enumerate(targets) if _zone_key(t["name"]) == 2]

    has_spine = len(private_idx) >= _MIN_ROOMS_FOR_SPINE
    area_key = "target_area" if planner else "preferred_area"
    band = _public_band_rows(targets, public_idx, cols, rows, cell_area, cell_h, unit, area_key)
    zones = [_zone_key(t["name"]) for t in targets]

    target_cells = [max(1, round(t[area_key] / cell_area)) for t in targets]
    min_cells = [max(1, round(t["min_area"] / cell_area)) for t in targets]
    max_cells = [max(min_cells[i], round(targets[i]["max_area"] / cell_area)) for i in range(len(targets))]
    if planner and band > 0:
        band = _planner_band_cap(band, public_idx, max_cells, targets, cols, cell_h, unit)
        if band_delta:
            # Phase 3 candidate variant: a slightly shallower/deeper front band.
            band = _clamp(band + band_delta, 1, max(1, int(rows * _MAX_PUBLIC_BAND_FRACTION)))

    spine_cells: set[tuple[int, int]] = set()
    if has_spine and not planner:
        spine_cells = _reserve_spine(
            cols, rows, cell_w, cell_h, unit, spine_width_delta, cross_link, len(targets), band
        )
    elif has_spine:
        # Planner path: a real 1.2 m corridor, widened (up to the 3 m cap)
        # only as far as needed so the back zone never has more cells than
        # its rooms' combined max - excess area becomes circulation, never an
        # oversized room.
        public_set = set(public_idx) if band > 0 else set()
        back_max = sum(max_cells[i] for i in range(len(targets)) if i not in public_set)
        base = _planner_hallway_width_cells(0, cell_w, unit, spine_width_delta)
        cap = max(base, round(to_plot_unit(_HALLWAY_MAX_WIDTH_M, unit) / max(cell_w, 1e-6)) + spine_width_delta)
        for w in range(base, cap + 1):
            spine_cells = _reserve_spine(
                cols, rows, cell_w, cell_h, unit, spine_width_delta, cross_link, len(targets), band,
                planner=True, width_override=w,
            )
            if (rows - band) * cols - len(spine_cells) <= back_max:
                break

    owner = [[-1 for _ in range(cols)] for _ in range(rows)]
    for (c, r) in spine_cells:
        owner[r][c] = -2

    if planner:
        # Phase 2: the plan's targets are relative intent; rescale them so each
        # zone's targets sum to the cells that zone actually has (front band /
        # back zone minus the spine), within [min, max] - so growth fills the
        # footprint and the bounded fill only has rounding residue left.
        back = [i for i in range(len(targets)) if i not in public_idx]
        if band > 0:
            _fit_cells(public_idx, min_cells, target_cells, max_cells, band * cols)
            _fit_cells(back, min_cells, target_cells, max_cells, (rows - band) * cols - len(spine_cells))
        else:
            _fit_cells(list(range(len(targets))), min_cells, target_cells, max_cells, rows * cols - len(spine_cells))

    # Phase D4 (2026-10-12): the garage needs a real WIDTH along the facing
    # edge (room for `garage_cars` cars side by side), not just the right
    # total area - a 2-car garage sharing the front row evenly with 4 other
    # public rooms came out ~7ft wide (one car) x deep. Reserve its real
    # garage_dimensions() width in cells so _seed_rooms seeds it at the
    # front-left corner with the other public rooms pushed past that frontage,
    # and pre-claim its block below before general growth. depth is bounded by
    # the public band so it can never dive into the private zone.
    garage_i = next((i for i, t in enumerate(targets) if t["category"] == "garage"), None)
    garage_w_cells = 0
    garage_d_cells = 0
    if garage_i is not None and cell_w > 0 and cell_h > 0:
        gw, gd = garage_dimensions(garage_cars or 1, unit)
        garage_w_cells = _clamp(max(1, round(gw / cell_w)), 1, cols)
        depth_cap = band if band > 0 else rows
        garage_d_cells = _clamp(max(1, round(gd / cell_h)), 1, depth_cap)

    seeds = _seed_rooms(
        targets, public_idx, circulation_idx, private_idx, cols, rows, spine_cells, min_cells, band,
        garage_i, garage_w_cells, flip_egress,
    )

    cells_by_room: list[set[tuple[int, int]]] = [set() for _ in targets]
    for i, seed in enumerate(seeds):
        if seed is not None:
            cells_by_room[i].add(seed)
            owner[seed[1]][seed[0]] = i

    # Pre-claim the garage's real width x depth rectangle at its front-corner
    # seed (Phase D4) so it keeps a car-fitting shape instead of being
    # out-competed for frontage by the general round-robin growth.
    if garage_i is not None and seeds[garage_i] is not None and garage_w_cells > 0:
        _preclaim_garage(owner, cells_by_room, garage_i, seeds[garage_i], garage_w_cells, garage_d_cells, cols, rows)

    _grow_rooms(owner, cells_by_room, seeds, cols, rows, target_cells, min_cells, max_cells, zones, band)
    if planner:
        _fill_leftovers_bounded(
            owner, cells_by_room, target_cells, max_cells, cols, rows, spine_cells, zones, band,
            stats if stats is not None else {},
        )
        _rebalance_over_max(
            owner, cells_by_room, target_cells, max_cells, cols, rows, zones, band,
            {garage_i} if garage_i is not None else set(), stats if stats is not None else {},
            [
                0.0 if t["category"] in ("garage", "staircase", "hallway") else min_width_for_room(t["name"], unit)
                for t in targets
            ],
            cell_w, cell_h,
        )
    else:
        _fill_leftovers(owner, cells_by_room, target_cells, max_cells, cols, rows, spine_cells, zones, band)
    _ensure_minimum_presence(cells_by_room, min_cells, owner)
    if planner and rectify == "reconnect":
        n_t = len(targets)
        if zones is not None and band > 0:
            lo_r = [0 if zones[i] == 0 else band for i in range(n_t)]
            hi_r = [band - 1 if zones[i] == 0 else rows - 1 for i in range(n_t)]
        else:
            lo_r, hi_r = [0] * n_t, [rows - 1] * n_t
        _reconnect_rooms(
            owner, cells_by_room, targets, target_cells, max_cells, lo_r, hi_r,
            {i for i in range(n_t) if targets[i]["category"] in ("garage", "staircase", "hallway")},
            _ShapeCtx(cols, rows, cell_w, cell_h, unit, targets), stats,
        )
    elif planner and rectify:
        _rectify_rooms(
            owner, cells_by_room, targets, target_cells, min_cells, max_cells, zones, band,
            _ShapeCtx(cols, rows, cell_w, cell_h, unit, targets), stats,
        )
    if planner and stats is not None:
        stats["overflow_rooms"] = [targets[int(i)]["name"] for i in stats.get("overflow_rooms", [])]
        stats["over_max_rooms"] = [
            targets[i]["name"] for i in range(len(targets)) if len(cells_by_room[i]) > max_cells[i]
        ]

    rooms_out = []
    for t, cells in zip(targets, cells_by_room):
        room = _cells_to_room(t["name"], cells, cell_w, cell_h, bool(planner and rectify))
        if room is None:
            continue
        if t.get("suite") is not None:
            room["suite"] = t["suite"]
        rooms_out.append(room)

    # Recompute the spine's own final cell-set from `owner` rather than
    # reusing the ORIGINAL `spine_cells` local - _fill_leftovers() can grow
    # it (see that function's own docstring: a leftover pocket bordered only
    # by spine cells and the grid edge, with no real room neighbor at all,
    # is absorbed into the spine/Hallway rather than left permanently
    # unclaimed - this is what actually closes Bug A, the "black hole").
    final_spine_cells = {(c, r) for r in range(rows) for c in range(cols) if owner[r][c] == -2}
    if final_spine_cells:
        hallway = _cells_to_room("Hallway", final_spine_cells, cell_w, cell_h, bool(planner and rectify))
        if hallway is not None:
            rooms_out.append(hallway)

    return rooms_out


def _cells_to_room(
    name: str, cells: set[tuple[int, int]], cell_w: float, cell_h: float, planner: bool = False
) -> dict | None:
    if not cells:
        return None
    # Planner path (Phase 3): fewest-rectangle decomposition; flag-off keeps
    # the legacy row-major greedy (byte-identical).
    part_cells = _decompose_planner(cells) if planner else _decompose(cells)
    parts = [
        {"x": c * cell_w, "y": r * cell_h, "w": pw * cell_w, "h": ph * cell_h}
        for (c, r, pw, ph) in part_cells
    ]
    min_x = min(p["x"] for p in parts)
    min_y = min(p["y"] for p in parts)
    max_x = max(p["x"] + p["w"] for p in parts)
    max_y = max(p["y"] + p["h"] for p in parts)
    room = {"name": name, "x": min_x, "y": min_y, "w": max_x - min_x, "h": max_y - min_y}
    if len(parts) > 1:
        room["parts"] = parts
    return room


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _hallway_width_cells(long_cells: int, cell_span: float, unit: str, width_delta: int) -> int:
    frac_cells = max(1, round(long_cells * _HALLWAY_WIDTH_FRACTION))
    min_cells_w = max(1, round(to_plot_unit(_HALLWAY_MIN_WIDTH_M, unit) / max(cell_span, 1e-6)))
    max_cells_w = max(min_cells_w, round(to_plot_unit(_HALLWAY_MAX_WIDTH_M, unit) / max(cell_span, 1e-6)))
    return max(1, _clamp(frac_cells, min_cells_w, max_cells_w) + width_delta)


def _planner_band_cap(
    band: int, public_idx: list[int], max_cells: list[int], targets: list[dict], cols: int, cell_h: float, unit: str
) -> int:
    """Planner path: never make the front band hold more cells than every
    public room's combined max (that surplus could only be dumped into a
    public room past its max) - except where the largest public room's real
    min width needs the depth."""
    cap_rows = sum(max_cells[i] for i in public_idx) // max(cols, 1)
    max_min_width = max((min_width_for_room(targets[i]["name"], unit) for i in public_idx), default=0.0)
    min_rows = max(1, int(-(-max_min_width // cell_h))) if cell_h > 0 and max_min_width > 0 else 1
    return max(1, min(band, max(cap_rows, min_rows)))


def _planner_hallway_width_cells(_long_cells: int, cell_span: float, unit: str, width_delta: int) -> int:
    return max(1, round(to_plot_unit(_HALLWAY_MIN_WIDTH_M, unit) / max(cell_span, 1e-6)) + width_delta)


def _reserve_spine(
    cols: int, rows: int, cell_w: float, cell_h: float, unit: str, width_delta: int, cross_link: bool,
    room_count: int, band_start: int = 0, planner: bool = False, width_override: int | None = None,
) -> set[tuple[int, int]]:
    """Reserves circulation cells BEFORE any room is seeded - a vertical
    spine running from the back of the public band (`band_start`, the first
    row behind the front public zone - see _public_band_rows()) through to
    the back of the floor, centered in x, plus (on a large enough room
    program) a perpendicular cross-link partway through the depth. The spine
    deliberately does NOT extend into the front public band (rows <
    band_start): that band is kept open, full-width, for the public rooms to
    grow wide-and-shallow (Phase D0). This is a deliberately simple,
    deterministic "open pathways" approximation - a cross shape, not a
    literal pathfound ring loop - but it structurally guarantees no
    interior room is more than one quadrant away from TWO different
    corridor segments, which is what actually prevents a pure dead-end
    single-loaded corridor (the rect engine's own structural limitation).
    Never reserves more than 60% of the grid - if the cross-link would push
    past that, it's dropped for this candidate (the vertical spine alone is
    kept) so there's always real room left to grow rooms into."""
    band_start = _clamp(band_start, 0, max(0, rows - 1))
    # Planner path (Phase 2): a real corridor width (1.2 m clear) instead of
    # 14% of the short side - the 14% rule turned ~25% of an upper floor into
    # hallway, which the planner's area allocation never budgeted for.
    width_fn = _planner_hallway_width_cells if planner else _hallway_width_cells
    hw_cells = width_override if width_override is not None else width_fn(min(cols, rows), cell_w, unit, width_delta)
    hw = min(hw_cells, max(1, cols - 2))
    cx0 = max(0, (cols - hw) // 2)
    spine = {(c, r) for r in range(band_start, rows) for c in range(cx0, min(cols, cx0 + hw))}

    if cross_link and room_count >= _MIN_ROOMS_FOR_CROSS_LINK:
        hh_cells = width_override if width_override is not None else width_fn(min(cols, rows), cell_h, unit, width_delta)
        hh = min(hh_cells, max(1, rows - 2))
        ry0 = _clamp(round(rows * 0.62) - hh // 2, band_start, max(band_start, rows - hh))
        candidate = spine | {(c, r) for r in range(ry0, min(rows, ry0 + hh)) for c in range(cols)}
        if len(candidate) <= cols * rows * 0.6:
            spine = candidate

    return spine


def _public_band_rows(
    targets: list[dict], public_idx: list[int], cols: int, rows: int, cell_area: float,
    cell_h: float = 1.0, unit: str = "ft", area_key: str = "preferred_area",
) -> int:
    """Depth (in rows) of the front public band - enough rows for every
    public room's own PREFERRED area to fit across the full footprint width
    (`cols`), so public rooms grow wide-and-shallow rather than deep
    slivers, clamped to at most _MAX_PUBLIC_BAND_FRACTION of the floor so
    the private zone + spine always keep real depth behind it. Returns 0
    when there are no public rooms (no band, whole floor is private/
    circulation - the growth row-bounds then degrade to the full [0, rows)
    range, i.e. a strict no-op).

    Real bug fixed 2026-10 (found via feasibility.py's capacity-table
    probe, which uses a SMALL, sparse room program on a square test plot -
    the exact shape that triggered this): the band depth used to be sized
    from AREA alone (sum of preferred areas / cols), with no floor tied to
    any public room's own real MIN WIDTH. On a sparse public program (e.g.
    just Living/Kitchen/Dining/Entry, no suite/garage padding out the area
    sum), that area-only math could size the band THINNER than Living
    Room's own real min_width_for_room() (the largest of any public
    category) - and because _grow_rooms()'s band gate then CONFINES every
    public room's growth to rows [0, band), no amount of extra plot size
    could ever let that room clear its own min-width bar: the band itself
    structurally prevented it, regardless of how much margin the capacity
    probe's search tried. This is what made
    feasibility.room_count_requirements_table() report 0 bedrooms/0
    bathrooms fit on every plot size, including a genuinely huge one - the
    band was never the bottleneck a bigger plot could fix. Fixed by
    flooring the band depth (in rows) at the largest public room's own real
    min_width, converted via `cell_h` - the band is now always AT LEAST
    deep enough to let the room that needs the most depth have a real
    chance to clear its own bar (still only a chance, not a guarantee -
    horizontal space/competition from other rooms can still produce a
    narrower shape - but the band itself is no longer the hard ceiling)."""
    if not public_idx or cols <= 0 or cell_area <= 0:
        return 0
    total = sum(max(1, round(targets[i][area_key] / cell_area)) for i in public_idx)
    band = max(1, -(-total // cols))  # ceil(total / cols)
    if cell_h > 0:
        max_min_width = max(
            (min_width_for_room(targets[i]["name"], unit) for i in public_idx), default=0.0
        )
        # int(-(-a // b)) is the standard ceiling-division trick; cast to
        # int explicitly since max_min_width/cell_h are floats and `band`
        # must stay a real int (it's used as a range()/grid-index bound
        # elsewhere) - band = max(int, float) would silently return a float.
        min_width_rows = max(1, int(-(-max_min_width // cell_h))) if max_min_width > 0 else 1
        band = max(band, min_width_rows)
    return _clamp(band, 1, max(1, int(rows * _MAX_PUBLIC_BAND_FRACTION)))


def _seed_rooms(
    targets: list[dict],
    public_idx: list[int],
    circulation_idx: list[int],
    private_idx: list[int],
    cols: int,
    rows: int,
    spine_cells: set[tuple[int, int]],
    min_cells: list[int],
    band: int = 0,
    garage_i: int | None = None,
    garage_w_cells: int = 0,
    flip_egress: bool = False,
) -> list[tuple[int, int] | None]:
    """One anchor cell per room, matching floor_layout.py's own front-to-
    back convention (front = low-y). Bedroom/living rooms seed ON the
    floor's own exterior boundary - see module docstring for why that's
    what guarantees egress by construction, not a scored preference.

    Phase D0 (2026-10-12): public rooms seed in the front band (rows
    [0, band)) and private egress rooms seed on the exterior boundary only
    BEHIND the band (rows [band, rows)), so the front-public/back-private
    zoning growth enforces (see _grow_rooms()) is seeded consistently -
    `band` is 0 (no-op, whole floor) when there are no public rooms.

    Real bug fixed during development (found by actually running this on a
    6-room suite program and inspecting the output, not just reasoning
    about it): an earlier version placed every private room's seed via
    nearest-free-cell-to-the-previous-seed, which packed a run of 3+
    suites into one tight corner cluster - the MIDDLE rooms in that
    cluster ended up with every single neighbor already claimed by another
    room's own seed before growth even started, so they never grew past
    their 1-cell seed at all. Fixed by seeding every EGRESS room (bedroom/
    living) SPREAD OUT along the back edge first (the same index/count
    proportional spacing public rooms already use along the front edge) -
    real spacing between egress seeds is what prevents them from boxing
    each other in - and only THEN seeding each non-egress room (a suite's
    own bathroom, or a standalone wet/dry leftover) adjacent to its real
    anchor (its own suite's bedroom seed, or the last-placed private room
    for an unpaired leftover).

    Real bug fixed 2026-10 (Phase A2, "Bug B" - a room that collapses to a
    ~1-cell-wide sliver, confirmed reproducible under east/west facing and
    on a dense upper floor): the proportional boundary-path index above
    picks a position purely by COUNT/SPACING, with no idea whether that
    specific spot has enough real free territory behind it to ever reach
    the room's own minimum area. On the reproduced case, the ideal spot
    landed in a pocket wedged between an already-seeded neighbor (a
    suite's own bathroom, claimed first) and the circulation cross-link
    spine (see _reserve_spine()) on the opposite side - a pocket with ZERO
    reachable free cells beyond the seed itself, so growth's whole-strip
    extension (see _grow_rooms()) never had anywhere to go and even the
    arm-fallback BFS found nothing left to claim: the room was stuck at its
    single seed cell (or whatever thin leftover _fill_leftovers() later
    glued on), nowhere near its real minimum. Fixed by making egress
    seeding REACHABILITY-aware: `_best_reachable_boundary_seed()` still
    tries the ideal proportional position FIRST (so every case that
    already worked renders byte-identical to before this fix - the common
    case always resolves on its first probe), but when that position's
    locally reachable free-cell count falls short of the room's own
    min_cells, it searches outward along the SAME boundary path (the
    deterministic offset sequence 0, +1, -1, +2, -2, ...) for a nearby spot
    with real room to grow into, falling back to whichever probed position
    had the LARGEST reachable pocket if none fully clears the bar."""
    occupied: set[tuple[int, int]] = set(spine_cells)
    seeds: list[tuple[int, int] | None] = [None] * len(targets)

    # Phase D2: when a wet-service room (laundry/pantry/dirty_kitchen) will be
    # clustered behind the kitchen, the kitchen is seeded at the BACK row of
    # the public band (row band-1) instead of the front row - so it grows
    # forward and OWNS the band-boundary cells, letting the laundry seeded
    # directly behind it (row band) actually share a wall with the KITCHEN
    # rather than with whichever larger public room (e.g. Dining) would
    # otherwise out-compete the small kitchen for that boundary row (confirmed
    # via owner-grid tracing on the deep-band 60x80 ground-floor case). When
    # no wet room exists, the kitchen seeds at the front like every other
    # public room (byte-identical to pre-D2 behavior).
    # Public rooms: spread along the front row, left to right in their
    # already front-of-house-ordered sequence (floor_layout._sort_key()).
    n_public = len(public_idx)
    kitchen_idx = next((i for i in public_idx if targets[i]["category"] == "kitchen"), None)
    has_wet_cluster = any(targets[i]["category"] in _WET_CLUSTER_CATEGORIES for i in private_idx)
    # Only bother back-seeding the kitchen when ANOTHER public room could
    # out-compete it for the band-boundary row (n_public >= 2). On a single-
    # public-room floor (e.g. a lone upper-floor Kitchen) the kitchen already
    # owns the whole band, so front-seeding keeps it shallower/less sliver-
    # like while the laundry behind it (row band) is still adjacent.
    kitchen_back_seed = (
        has_wet_cluster and kitchen_idx is not None and band > 0 and n_public >= 2
    )
    # Phase D4: when a garage exists, it anchors at the front-left corner and
    # reserves `garage_w_cells` of frontage; the OTHER public rooms spread
    # across the remaining columns [garage_w_cells, cols) so they don't eat
    # into the garage's car-width. (_grow_floor then pre-claims the garage's
    # full width x depth block.) With no garage this is the plain even spread.
    has_garage_reserve = garage_i is not None and garage_i in public_idx and garage_w_cells > 0
    spread_start = garage_w_cells if has_garage_reserve else 0
    other_public = [i for i in public_idx if not (has_garage_reserve and i == garage_i)]
    n_other = len(other_public)
    span = max(1, cols - spread_start)
    if has_garage_reserve:
        cell = _first_free_near(0, 0, cols, rows, occupied)
        if cell is not None:
            seeds[garage_i] = cell
            occupied.add(cell)
    for k, i in enumerate(other_public):
        col = _clamp(spread_start + round(((k + 0.5) / n_other) * (span - 1)), 0, cols - 1) if n_other else 0
        seed_row = band - 1 if (kitchen_back_seed and i == kitchen_idx) else 0
        cell = _first_free_near(col, seed_row, cols, rows, occupied)
        if cell is not None:
            seeds[i] = cell
            occupied.add(cell)

    # The kitchen's own seed on this floor (if any), used to cluster wet-
    # service rooms (laundry/pantry/dirty_kitchen) directly behind it.
    kitchen_seed = seeds[kitchen_idx] if kitchen_idx is not None else None

    # Private egress rooms (bedroom/living) FIRST, spread proportionally
    # along a real exterior PERIMETER PATH (left wall, then the back wall,
    # then the right wall) - not just the back row alone. Real bug found by
    # rendering a 6-bedroom program and seeing two bedrooms come out
    # genuinely too narrow: with only the single back edge to spread 6
    # rooms across, each room's available "slot" before hitting its
    # neighbor was narrower than its own real minimum width, no matter how
    # much area it was given - exactly the real architectural reason a
    # 6-bedroom house wraps bedrooms around more than one exterior wall
    # instead of lining them all up on one. Spreading across 3 walls
    # instead of 1 gives each room roughly 3x the real wall frontage to
    # grow from.
    private_egress = [i for i in private_idx if targets[i]["category"] in _EGRESS_CATEGORIES]
    if flip_egress:
        # Phase 3 candidate variant (planner path only): walk the egress rooms
        # around the perimeter in the opposite order.
        private_egress = private_egress[::-1]
    private_other = [i for i in private_idx if i not in private_egress]
    boundary_path = _exterior_boundary_path(cols, rows, band)

    # Phase D3 (2026-10-12): the master bedroom takes the boundary-path spread
    # SLOT farthest from the entrance (a quiet back-wall position), instead of
    # whatever slot its suite-order index happened to give it. Previously the
    # master (first in suite order) got spread-slot 0 = boundary-path index 0
    # = the left wall just behind the front band, i.e. one of the CLOSEST spots
    # to the entrance (problem #5 "master stranded near the entrance"). This
    # deliberately does NOT force the master onto an exact corner cell (an
    # earlier attempt that did collapsed the master/other bedrooms to slivers
    # on the dense 150x150 case - forcing an exact low-reachability corner
    # fights the proven proportional spacing). Instead it PERMUTES the
    # already-computed, already-proven spread slots so the master simply gets
    # the farthest one - same set of seed positions, just reassigned - and its
    # ensuite follows via the existing suite-anchor logic below.
    n_egress = len(private_egress)
    spread_positions = [
        _clamp(round(((k + 0.5) / n_egress) * (len(boundary_path) - 1)), 0, len(boundary_path) - 1)
        if n_egress else 0
        for k in range(n_egress)
    ]
    master_i = next(
        (i for i in private_egress
         if "master" in targets[i]["name"].lower() and targets[i]["category"] == "bedroom"),
        None,
    )
    ordered_egress = list(private_egress)
    if master_i is not None and n_egress > 1:
        entrance = _entrance_cell(public_idx, targets, seeds, cols)
        far_slot = max(
            range(n_egress),
            key=lambda k: _dist2(boundary_path[spread_positions[k]], entrance),
        )
        others = [i for i in private_egress if i != master_i]
        ordered_egress = others[:far_slot] + [master_i] + others[far_slot:]

    for k, i in enumerate(ordered_egress):
        idx = spread_positions[k]
        cell = _best_reachable_boundary_seed(boundary_path, idx, cols, rows, occupied, min_cells[i])
        if cell is None:
            cell = _seed_on_exterior(cols, rows, occupied, band)
        if cell is not None:
            seeds[i] = cell
            occupied.add(cell)

    # Every other private room (a suite's own bathroom, or a standalone
    # wet/dry leftover) seeds adjacent to its real anchor: its own suite's
    # bedroom seed when it has one, else the most-recently-placed private
    # room (keeps wet-wall-leftover grouping - see floor_layout.
    # _arrange_suites() - expressed as spatial adjacency).
    suite_seed: dict[int, tuple[int, int]] = {}
    for i in private_egress:
        suite = targets[i].get("suite")
        if suite is not None and seeds[i] is not None:
            suite_seed[suite] = seeds[i]

    last_cell = seeds[private_egress[-1]] if private_egress else None
    for i in private_other:
        suite = targets[i].get("suite")
        # Phase D2: a wet-service room seeds directly behind this floor's
        # kitchen (the first private row, rows[band], at the kitchen's own
        # column) so it shares the public-band boundary wall with the kitchen.
        # Falls through to the normal suite/last-cell anchor if there's no
        # kitchen on this floor (or no public band at all).
        if (
            targets[i]["category"] in _WET_CLUSTER_CATEGORIES
            and kitchen_seed is not None
            and band > 0
        ):
            cell = _first_free_near(kitchen_seed[0], band, cols, rows, occupied)
        else:
            anchor = suite_seed.get(suite) if suite is not None else last_cell
            if anchor is not None:
                cell = _first_free_near(anchor[0], anchor[1], cols, rows, occupied)
            else:
                cell = _seed_on_exterior(cols, rows, occupied, band)
        if cell is None:
            cell = _any_free_cell(cols, rows, occupied)
        if cell is not None:
            seeds[i] = cell
            occupied.add(cell)
            last_cell = cell

    # Circulation (staircase) seeded LAST, into whichever back corner is
    # still free - out of the way of the entrance and every already-seeded
    # private room above.
    for i in circulation_idx:
        cell = _first_free_near(cols - 1, rows - 1, cols, rows, occupied)
        if cell is not None:
            seeds[i] = cell
            occupied.add(cell)

    return seeds


def _first_free_near(col: int, row: int, cols: int, rows: int, occupied: set[tuple[int, int]]) -> tuple[int, int] | None:
    """BFS outward from (col,row) (clamped into the grid) for the nearest
    free cell - deterministic (fixed N/S/W/E neighbor order + a FIFO queue,
    so ties always resolve to whichever cell was reached first in that
    fixed order)."""
    col = _clamp(col, 0, cols - 1)
    row = _clamp(row, 0, rows - 1)
    if (col, row) not in occupied:
        return (col, row)
    seen = {(col, row)}
    queue = deque([(col, row)])
    while queue:
        c, r = queue.popleft()
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows and (nc, nr) not in seen:
                seen.add((nc, nr))
                if (nc, nr) not in occupied:
                    return (nc, nr)
                queue.append((nc, nr))
    return None


def _reachable_free_count(
    start: tuple[int, int], cols: int, rows: int, occupied: set[tuple[int, int]], cap: int
) -> int:
    """How many free (non-occupied) cells are reachable from `start` via a
    4-connected flood fill, counted up to `cap` (early-exits the moment the
    cap is hit - callers only ever need "is this at least N", not the exact
    total, so this stays cheap even on a large open region). `start` itself
    counts as reachable only if it's actually free; a seed candidate that's
    already occupied has 0 reachable free cells by this measure."""
    if cap <= 0:
        return 0
    if start in occupied:
        return 0
    seen = {start}
    queue = deque([start])
    count = 0
    while queue and count < cap:
        c, r = queue.popleft()
        count += 1
        if count >= cap:
            break
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows and (nc, nr) not in seen and (nc, nr) not in occupied:
                seen.add((nc, nr))
                queue.append((nc, nr))
    return count


def _best_reachable_boundary_seed(
    boundary_path: list[tuple[int, int]],
    ideal_idx: int,
    cols: int,
    rows: int,
    occupied: set[tuple[int, int]],
    needed: int,
) -> tuple[int, int] | None:
    """Picks a seed cell for an egress room from `boundary_path`, preferring
    the ideal proportional-spacing position but verifying it actually has
    `needed` reachable free cells behind it (see _seed_rooms()'s own
    docstring, "Bug B" fix, for the real trap this avoids - a boundary
    position wedged between an already-seeded neighbor and the circulation
    spine with zero room to grow into). Probes outward from `ideal_idx` in
    the fixed, deterministic order 0, +1, -1, +2, -2, ... (so the common
    case - the ideal spot already has plenty of room - resolves on the
    very first probe, byte-identical to the pre-fix behavior) and returns
    the first position whose reachable-free-cell count meets `needed`;
    falls back to whichever probed position had the LARGEST reachable
    pocket if none fully clears the bar (a genuinely tight room program
    still gets the best available spot, not nothing)."""
    path_len = len(boundary_path)
    if path_len == 0:
        return None
    best_cell: tuple[int, int] | None = None
    best_count = -1
    max_offset = max(ideal_idx, path_len - 1 - ideal_idx)
    for offset in range(0, max_offset + 1):
        for idx in ((ideal_idx + offset, ideal_idx - offset) if offset else (ideal_idx,)):
            if idx < 0 or idx >= path_len:
                continue
            col, row = boundary_path[idx]
            cell = _first_free_near(col, row, cols, rows, occupied)
            if cell is None:
                continue
            count = _reachable_free_count(cell, cols, rows, occupied, needed)
            if count >= needed:
                return cell
            if count > best_count:
                best_count = count
                best_cell = cell
    return best_cell


def _entrance_cell(
    public_idx: list[int],
    targets: list[dict],
    seeds: list[tuple[int, int] | None],
    cols: int,
) -> tuple[int, int]:
    """Phase D3: the grid cell representing where the entrance is, used to
    pick the boundary-path spread slot FARTHEST from it for the master suite.
    The Entry's own seed, else the Garage's, else the front-center cell (row
    0 = the front/low-y edge, this engine's core convention)."""
    anchor_i = next(
        (i for i in public_idx if targets[i]["category"] in ("foyer", "garage")),
        None,
    )
    if anchor_i is not None and seeds[anchor_i] is not None:
        return seeds[anchor_i]
    return (cols // 2, 0)


def _dist2(a: tuple[int, int], b: tuple[int, int]) -> int:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _exterior_boundary_path(cols: int, rows: int, band: int = 0) -> list[tuple[int, int]]:
    """A continuous path of grid-boundary cells - down the left wall, across
    the back wall, back up the right wall - starting at row `band` on the
    side walls (so private egress seeding stays BEHIND the front public band
    and never collides with public-room seeding on the entrance edge; `band`
    is 0, the original row-1 start, when there are no public rooms). Used to
    spread private egress rooms across REAL wall frontage on more than one
    exterior wall (see _seed_rooms()'s own docstring for the real bug this
    fixes)."""
    start = max(1, band)
    path: list[tuple[int, int]] = []
    for r in range(start, rows):
        path.append((0, r))
    for c in range(cols):
        path.append((c, rows - 1))
    for r in range(rows - 2, start - 1, -1):
        path.append((cols - 1, r))
    return path or [(0, max(0, rows - 1))]


def _seed_on_exterior(cols: int, rows: int, occupied: set[tuple[int, int]], band: int = 0) -> tuple[int, int] | None:
    """A free cell on the grid's own outer boundary BEHIND the front public
    band (rows >= band) - preferring the back edge (high-y, farthest from
    the entrance), then the side walls below the band, then (defensively)
    the band's own back row - a fixed, deterministic scan order."""
    for col in range(cols):
        cell = (col, rows - 1)
        if cell not in occupied:
            return cell
    for row in range(band, rows):
        for col in (0, cols - 1):
            cell = (col, row)
            if cell not in occupied:
                return cell
    for col in range(cols):
        cell = (col, band)
        if cell not in occupied:
            return cell
    return None


def _any_free_cell(cols: int, rows: int, occupied: set[tuple[int, int]]) -> tuple[int, int] | None:
    for r in range(rows):
        for c in range(cols):
            if (c, r) not in occupied:
                return (c, r)
    return None


_EXTEND_DIRECTIONS = ("up", "down", "left", "right")


def _grow_rooms(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    seeds: list[tuple[int, int] | None],
    cols: int,
    rows: int,
    target_cells: list[int],
    min_cells: list[int],
    max_cells: list[int],
    zones: list[int] | None = None,
    band: int = 0,
) -> None:
    """Rectangle-preserving growth, NOT cell-by-cell: round-robin across
    rooms, but each room's turn claims a WHOLE free row/column strip
    against one side of its CURRENT rectangle, never a single contested
    cell. This is what keeps the common case a clean, single rectangle
    (exactly what a plain-rectangle room always was) while still letting
    growth wrap around an obstacle when truly necessary (see the arm
    fallback below) - the real reason this design was settled on, after
    THREE rejected approaches found only by actually RENDERING real output
    and visually inspecting it (confirming this project's own standing
    rule that automated tests alone aren't enough for a layout-engine
    change - every rejected approach below passed every numeric check:
    zero holes, zero overlaps, often zero hard violations - yet produced
    visibly broken geometry):
      1. Simple round-robin cell-by-cell growth let two seeds started
         close together (a suite's bedroom+bathroom, seeded deliberately
         adjacent) alternately "steal" cells back and forth right at their
         shared boundary, since contested cells sit at nearly identical
         distance from both seeds - a jagged staircase of 1-cell slivers.
      2. A proper synchronized multi-source BFS (the textbook fix for
         exactly that 2-room striping problem) made the SAME underlying
         issue WORSE once scaled to 10 simultaneously competing rooms - a
         true Voronoi-style region grown against many neighbors at once is
         generally an irregular many-sided polygon, not something that
         decomposes into a small, clean set of rectangles (L=2 parts,
         U=3) the way this engine's `parts` representation assumes.
      3. Growing rooms fully SEQUENTIALLY (one room to completion before
         the next starts) removed the striping, but let early rooms in
         priority order (Entry, Living, Kitchen...) greedily consume space
         a LATER, suite-adjacent room (a bedroom's own bathroom) needed -
         bathrooms ended up at a single cell, far under their real
         minimum.
    Whole-strip extension sidesteps all three: it can't interleave (a
    strip is claimed atomically, not cell-by-cell), it can't produce a
    many-sided polygon (every claim is a straight-edged rectangle
    extension), and round-robin fairness (one strip-claim per room per
    round, not grow-to-completion) means an early room can't silently
    consume a later room's reachable space in one uninterrupted turn."""
    n = len(cells_by_room)

    # Phase D0 (2026-10-12): per-room growth row bounds enforce front-public/
    # back-private zoning. A public room (zone 0) may only grow within the
    # front band (rows [0, band)); every other room (private/circulation)
    # only behind it (rows [band, rows)). This is what keeps a large public
    # room wide-and-shallow instead of diving the full floor depth and
    # wrapping around the back bedrooms (the Phase A bowling-alley finding,
    # root-caused to unbounded DEPTH growth - see _public_band_rows()). When
    # `band` is 0 (no public rooms) or `zones` is None (a direct/legacy
    # caller), the bounds degrade to the full [0, rows) range - a strict
    # no-op, identical to the pre-D0 behavior.
    if zones is not None and band > 0:
        row_lo = [0 if zones[i] == 0 else band for i in range(n)]
        row_hi = [band - 1 if zones[i] == 0 else rows - 1 for i in range(n)]
    else:
        row_lo = [0] * n
        row_hi = [rows - 1] * n

    def current_rect(i: int) -> tuple[int, int, int, int] | None:
        cells = cells_by_room[i]
        if not cells:
            return None
        xs = [c for c, _ in cells]
        ys = [r for _, r in cells]
        x0, y0 = min(xs), min(ys)
        return x0, y0, max(xs) - x0 + 1, max(ys) - y0 + 1

    progressed = True
    while progressed:
        progressed = False
        for i in range(n):
            if len(cells_by_room[i]) >= target_cells[i] or len(cells_by_room[i]) >= max_cells[i]:
                continue
            rect = current_rect(i)
            if rect is None:
                continue
            x0, y0, w, h = rect
            candidates: dict[str, tuple[int, int, int, int]] = {}
            if y0 > row_lo[i] and all(owner[y0 - 1][c] == -1 for c in range(x0, x0 + w)):
                candidates["up"] = (x0, y0 - 1, w, h + 1)
            if y0 + h <= row_hi[i] and all(owner[y0 + h][c] == -1 for c in range(x0, x0 + w)):
                candidates["down"] = (x0, y0, w, h + 1)
            if x0 > 0 and all(owner[r][x0 - 1] == -1 for r in range(y0, y0 + h)):
                candidates["left"] = (x0 - 1, y0, w + 1, h)
            if x0 + w < cols and all(owner[r][x0 + w] == -1 for r in range(y0, y0 + h)):
                candidates["right"] = (x0, y0, w + 1, h)
            if not candidates:
                continue

            def aspect(dims: tuple[int, int, int, int]) -> float:
                _, _, cw, ch = dims
                return max(cw, ch) / max(min(cw, ch), 1)

            best_dir = min(
                candidates, key=lambda d: (aspect(candidates[d]), _EXTEND_DIRECTIONS.index(d))
            )
            nx0, ny0, nw, nh = candidates[best_dir]
            for r in range(ny0, ny0 + nh):
                for c in range(nx0, nx0 + nw):
                    if owner[r][c] == -1:
                        owner[r][c] = i
                        cells_by_room[i].add((c, r))
            progressed = True

    # Arm fallback (the real, but RARE and bounded, source of an L-shape -
    # see module docstring's "Known simplifications"): a room boxed in on
    # every side by pure rectangle extension that's STILL genuinely under
    # its own real MINIMUM area (not merely under its preferred/target -
    # settling a bit short of preferred is a normal, already-accepted soft
    # outcome, see layout_quality.py's own "below_preferred" check) gets
    # ONE single-source BFS reach into whatever free territory remains
    # connected to it, claiming only as many cells as needed to clear its
    # minimum. This never runs for the common case - most rooms satisfy
    # their target via pure rectangle extension alone, so most rooms stay
    # a plain single rectangle, exactly like the rect engine's own output.
    for i in range(n):
        if not cells_by_room[i] or len(cells_by_room[i]) >= min_cells[i]:
            continue
        _grow_arm(
            owner, cells_by_room[i], i, cols, rows,
            min_cells[i] - len(cells_by_room[i]), max_cells[i], row_lo[i], row_hi[i],
        )

    # Steal fallback (2026-10, Phase A2 "Bug B" fix - absolute last resort,
    # one rung below the free-cell arm fallback above): a room can still be
    # genuinely under its own minimum here even after BOTH whole-strip
    # extension AND the free-cell arm BFS - confirmed via direct
    # instrumentation on the real reproducing case (east/west-facing Master
    # Bedroom): its seed ended up completely walled in by an already-grown
    # neighbor on one side and a circulation spine cell (owner == -2) on
    # the other, with ZERO free (-1) cells reachable at all, so _grow_arm's
    # free-cell-only BFS found nothing to claim. A seed-time reachability
    # check was tried first and rejected - at seed placement almost the
    # whole grid is still empty, so a reachability probe can never predict
    # which direction OTHER rooms' own whole-strip growth will race to
    # claim first later in the SAME round-robin pass; the trap is a
    # dynamic race during growth, not a static seed-placement defect. This
    # pass instead lets a still-starved room reach into an ALREADY-CLAIMED
    # neighbor's cells (never a spine cell - circulation is never
    # stealable), nearest first, but only takes a cell whose current owner
    # would stay at/above ITS OWN real minimum afterward - a donor is never
    # pushed into its own violation to fix this room's.
    for i in range(n):
        if not cells_by_room[i] or len(cells_by_room[i]) >= min_cells[i]:
            continue
        _steal_arm(
            owner, cells_by_room, i, cols, rows, min_cells[i] - len(cells_by_room[i]), max_cells[i],
            min_cells, row_lo[i], row_hi[i],
        )


def _top_up_to_target(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    cols: int,
    rows: int,
    target_cells: list[int],
    max_cells: list[int],
    zones: list[int] | None,
    band: int,
) -> None:
    """Planner path (Phase 2): after rectangle growth stalls, every room
    still under its TARGET claims the nearest free cells it can reach (one
    bounded BFS arm, never past target or max, never outside its zone band) -
    largest relative shortfall first. Without this, a room boxed in short of
    its target leaves free cells that only an already-full neighbour touches,
    and the bounded fill then has to overflow that neighbour."""
    n = len(cells_by_room)
    if zones is not None and band > 0:
        lo = [0 if zones[i] == 0 else band for i in range(n)]
        hi = [band - 1 if zones[i] == 0 else rows - 1 for i in range(n)]
    else:
        lo, hi = [0] * n, [rows - 1] * n
    order = sorted(
        (i for i in range(n) if cells_by_room[i] and len(cells_by_room[i]) < target_cells[i]),
        key=lambda i: (len(cells_by_room[i]) / max(target_cells[i], 1), i),
    )
    for i in order:
        need = min(target_cells[i], max_cells[i]) - len(cells_by_room[i])
        if need > 0:
            _grow_arm(owner, cells_by_room[i], i, cols, rows, need, max_cells[i], lo[i], hi[i])


def _steal_arm(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    room_id: int,
    cols: int,
    rows: int,
    needed: int,
    cap: int,
    min_cells: list[int],
    row_lo: int = 0,
    row_hi: int | None = None,
) -> None:
    """Absolute last resort for a room still under its own real minimum
    after both whole-strip growth and the free-cell arm fallback
    (_grow_arm) - see _grow_rooms()'s own call-site comment for the real,
    reproduced trap this exists for. A single-source BFS reach from the
    room's own claimed cells, exactly like _grow_arm, but allowed to cross
    into a NEIGHBORING room's already-claimed territory (never a spine
    cell - owner == -2 is skipped, circulation is never stealable) when
    free cells alone run out. Claims the nearest stealable cell first;
    skips (but still expands the search past) any candidate whose current
    owner would drop below its OWN min_cells if it lost that cell - this
    bounds the damage to "borrow only genuine slack", never cascading one
    room's shortfall into a different hard violation. Like
    _ensure_minimum_presence()'s own last-resort donor logic, a stolen
    cell can in a genuinely pathological case leave the donor with an odd
    notch - an accepted, honestly-stated edge case for an absolute last
    resort, not a general guarantee."""
    # Phase D0: never steal outside this room's own zone band (row_lo..hi),
    # or a public room would pull a back-zone cell in and re-introduce the
    # front-public/back-private leak the growth band gate prevents.
    hi = rows - 1 if row_hi is None else row_hi
    heap: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int]] = set(cells_by_room[room_id])
    for (c, r) in cells_by_room[room_id]:
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and row_lo <= nr <= hi and (nc, nr) not in seen:
                seen.add((nc, nr))
                heapq.heappush(heap, (1, nr, nc))

    claimed = 0
    while heap and claimed < needed and len(cells_by_room[room_id]) < cap:
        dist, r, c = heapq.heappop(heap)
        owner_val = owner[r][c]
        if owner_val >= 0 and owner_val != room_id:
            donor_cells = cells_by_room[owner_val]
            if len(donor_cells) - 1 >= min_cells[owner_val]:
                donor_cells.discard((c, r))
                owner[r][c] = room_id
                cells_by_room[room_id].add((c, r))
                claimed += 1
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and row_lo <= nr <= hi and (nc, nr) not in seen:
                seen.add((nc, nr))
                heapq.heappush(heap, (dist + 1, nr, nc))


def _grow_arm(
    owner: list[list[int]],
    cells: set[tuple[int, int]],
    room_id: int,
    cols: int,
    rows: int,
    needed: int,
    cap: int,
    row_lo: int = 0,
    row_hi: int | None = None,
) -> None:
    """A single-source BFS reach from `cells` (a room's already-claimed
    territory) outward through whatever free cells remain, claiming the
    nearest `needed` of them (bounded by `cap`, the room's real maximum) -
    a bounded, last-resort top-up for a room pure rectangle extension left
    genuinely under its own minimum, not the primary growth mechanism.
    Phase D0: confined to the room's own zone band (row_lo..row_hi) so a
    public room's top-up never spills into the private back zone (the
    front-public/back-private leak the growth band gate prevents)."""
    hi = rows - 1 if row_hi is None else row_hi
    heap: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int]] = set(cells)
    for (c, r) in cells:
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and row_lo <= nr <= hi and owner[nr][nc] == -1 and (nc, nr) not in seen:
                seen.add((nc, nr))
                heapq.heappush(heap, (1, nr, nc))

    claimed = 0
    while heap and claimed < needed and len(cells) < cap:
        dist, r, c = heapq.heappop(heap)
        if owner[r][c] != -1:
            continue
        owner[r][c] = room_id
        cells.add((c, r))
        claimed += 1
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and row_lo <= nr <= hi and owner[nr][nc] == -1 and (nc, nr) not in seen:
                seen.add((nc, nr))
                heapq.heappush(heap, (dist + 1, nr, nc))


def _preclaim_garage(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    garage_i: int,
    seed: tuple[int, int],
    w_cells: int,
    d_cells: int,
    cols: int,
    rows: int,
) -> None:
    """Phase D4: claim the garage's real width x depth rectangle from its
    front-corner seed BEFORE general round-robin growth, so the garage keeps
    a car-fitting shape (wide along the facing edge) instead of being
    out-competed for frontage by larger public rooms. Only claims currently-
    free cells (never a spine or another seed); the block is anchored at the
    seed's own column/row (the front-left corner) and clamped into the grid.
    The garage's seed row is 0 (front edge) so the block hugs the facing wall,
    guaranteeing it touches the road edge for the Phase-C driveway."""
    sc, sr = seed
    c0 = _clamp(sc, 0, max(0, cols - w_cells))
    r0 = _clamp(sr, 0, max(0, rows - d_cells))
    for r in range(r0, min(rows, r0 + d_cells)):
        for c in range(c0, min(cols, c0 + w_cells)):
            if owner[r][c] == -1:
                owner[r][c] = garage_i
                cells_by_room[garage_i].add((c, r))


def _fill_leftovers(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    target_cells: list[int],
    max_cells: list[int],
    cols: int,
    rows: int,
    spine_cells: set[tuple[int, int]],
    zones: list[int] | None = None,
    band: int = 0,
) -> None:
    """Any cell still unclaimed after growth stalls (owner == -1, not
    spine) is absorbed by an adjacent already-claimed room - the footprint
    always tiles completely.

    Phase D0 (2026-10-12): the candidate room a leftover region is absorbed
    into is kept ZONE-COMPATIBLE with where the region sits - a region fully
    inside the front public band (every cell row < band) only goes to a
    PUBLIC room, a region fully behind the band only to a private/
    circulation room. Without this, a public room that stopped at the band
    but is still under its preferred area would greedily win a
    directly-behind-the-band leftover (it's the "most under target"
    candidate), get a back-zone cell, and then cascade down the whole floor
    depth on the next pass - re-introducing the exact bowling-alley leak the
    growth band gate (see _grow_rooms()) exists to prevent. The filter falls
    back to the unfiltered adjacency set if it would otherwise empty the
    pool, so no cell is ever left unclaimed (the tiling guarantee wins over
    the zone preference in a genuine corner case).

    Real bug fixed 2026-10 (Phase A2, "Bug A" - a literal unlabeled solid
    black hole rendered inside the house, reproduced deterministically on
    several Phase A baseline cases): a leftover connected region whose ONLY
    real-room neighbors are NONE at all - every bordering cell is either
    another unclaimed cell already in the same region, a SPINE cell
    (owner == -2), or the grid edge - used to be silently skipped
    (`if not adjacent_rooms: continue`), because the neighbor scan below
    only ever recorded `owner_val >= 0` (a real room) into `adjacent_rooms`;
    a spine cell is neither -1 (joins the region) nor >= 0 (counted as an
    adjacent room), so it was simply ignored as a neighbor. This is a real,
    reproducible geometry case, not a hypothetical: the cross-link spine
    (see _reserve_spine()) is a plus-shape that can fully wall off one of
    its four quadrants if growth stalls there before reaching every cell -
    the trapped pocket's only real borders are the spine arms + the grid's
    own outer edge, so `adjacent_rooms` came back empty and the region
    stayed owner == -1 forever, rendering as an unexplained solid black
    poché block (blueprint_svg.py only carves cells it was actually handed
    a room/part for). Fixed by giving a spine-bordered, room-less region
    the SAME treatment _grow_floor() already gives genuine circulation
    cells: it's absorbed INTO the spine/Hallway, not left unclaimed - a
    spine cell is a valid, intentional occupant of the grid, so widening
    the Hallway by a dead-end pocket it already walls off is architecturally
    sound (it just means the corridor's own poché fills that leftover
    nook), unlike silently discarding the cells. `_grow_floor()` recomputes
    the Hallway's final rect from `owner` (not the original, pre-fill
    `spine_cells` set) specifically so this absorption is reflected in the
    rendered output, not just internal bookkeeping.

    A second, narrower last-resort case (not expected to occur in practice -
    kept only so NO cell can ever stay unclaimed under any room program,
    not even a pathological one with zero rooms seeded and no spine at all)
    hands a region with neither a real-room neighbor NOR a spine neighbor to
    whichever room anywhere on the floor is furthest under its own target -
    the same "most under target" selection rule used for a normal adjacent
    region, just without requiring physical adjacency, since there is no
    adjacent candidate at all to choose from.

    Real bug fixed during development, found by actually RENDERING real
    output and visually inspecting it (confirming this project's own
    standing rule, yet again, that automated tests alone miss real
    geometry problems): the rectangle-extension growth in _grow_rooms()
    leaves thin leftover GAPS between two rectangles that stopped growing
    toward each other without perfectly meeting (ordinary, expected
    behavior for rectangle-based packing - any two independently-sized
    rectangles rarely tile edge to edge with no remainder). An earlier
    version of this function assigned each leftover cell to a neighbor
    ONE CELL AT A TIME, in row-major scan order - which scattered a thin,
    ugly 1-cell-wide sliver onto nearly EVERY room's own shape (since a
    gap column/row touches many different rooms along its length, and
    naive per-cell assignment doesn't keep them together). Fixed by
    processing each leftover gap as a whole CONNECTED REGION: find every
    maximal connected pocket of unclaimed cells, then assign the ENTIRE
    pocket to whichever ONE adjacent room is furthest under its own
    preferred cell count (and still under its max, when possible) - this
    produces at most one clean extra part per room instead of a scattered
    sliver attached to several (see _grow_rooms()'s own docstring for why
    thin leftover gaps between independently-grown rectangles are expected
    here, not a bug on their own).

    A "prefer whichever candidate shares the LONGEST boundary with the
    region" variant was also tried during development and rejected - it
    looked like the more geometrically natural rule, but real rendering
    comparisons showed it made the OVERALL result worse (it could pick a
    boundary-heavy but already-oversized neighbor over a genuinely
    under-filled one, starving that under-filled room instead of topping
    it up) - "most under target" stayed the better real-world result across
    the actual test matrix, even though it doesn't always produce the
    prettiest single merge. A small number of rooms can still end up with a
    thin sliver part from this pass on a dense, suite-heavy room program -
    a known, honestly-stated M1 limitation (see this module's own "Known
    simplifications"), not hidden as solved.

    Deliberately UNBOUNDED by max_cells (the `under_cap` preference above
    is a soft ordering hint, not a hard stop): capping how many cells of a
    region a room may absorb was also tried - twice, with two different
    "which cells to keep" strategies (an arbitrary prefix of the region,
    then a proper nearest-cell BFS from the room's own boundary) - and
    BOTH made real results measurably worse on a sparse (2-4 room) program
    specifically, fragmenting every room into far more parts than the
    uncapped version (which can let one room exceed its own max on a
    sparse program with no circulation spine to absorb the slack, but
    produces a visibly cleaner floor plan everywhere this was actually
    rendered and compared). Reverted in favor of this simpler, better-
    performing version - see _mask_footprint_dimensions() for the one real
    fix that DID help the sparse case (capping the FOOTPRINT itself by
    every room's combined real maximum, so there's less excess to dump in
    the first place)."""
    assigned = True
    while assigned:
        assigned = False
        visited: set[tuple[int, int]] = set()
        for r0 in range(rows):
            for c0 in range(cols):
                if owner[r0][c0] != -1 or (c0, r0) in visited:
                    continue
                region = [(c0, r0)]
                visited.add((c0, r0))
                stack = [(c0, r0)]
                adjacent_rooms: set[int] = set()
                touches_spine = False
                while stack:
                    c, r = stack.pop()
                    for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                        nc, nr = c + dc, r + dr
                        if not (0 <= nc < cols and 0 <= nr < rows):
                            continue
                        owner_val = owner[nr][nc]
                        if owner_val == -1:
                            # Phase D0: the front-public band boundary acts as
                            # a region separator, so a leftover pocket can
                            # never span from the band into the back zone -
                            # that straddle is exactly what let a band pocket's
                            # unclaimed cells (which an under-filled public room
                            # stalled short of) connect down to the back and
                            # get the whole lot dumped on that public room,
                            # re-opening the bowling-alley leak. Split at the
                            # band so each pocket is wholly in-band or wholly
                            # behind it, and the zone filter below assigns each
                            # to the correct zone's rooms.
                            crosses_band = (
                                zones is not None and band > 0 and (r < band) != (nr < band)
                            )
                            if (nc, nr) not in visited and not crosses_band:
                                visited.add((nc, nr))
                                region.append((nc, nr))
                                stack.append((nc, nr))
                        elif owner_val >= 0:
                            adjacent_rooms.add(owner_val)
                        elif owner_val == -2:
                            touches_spine = True
                if not adjacent_rooms:
                    # No real room borders this pocket at all - see this
                    # function's own docstring ("Real bug fixed 2026-10,
                    # Phase A2 Bug A") for why this is a real, reproducible
                    # case (a quadrant fully walled off by the cross-link
                    # spine), not a hypothetical. A spine-bordered pocket is
                    # absorbed into the spine/Hallway itself; a pocket with
                    # NEITHER a room nor a spine neighbor (only possible on
                    # a pathological room program, e.g. zero rooms seeded at
                    # all) falls through to the universal last resort below
                    # rather than being left permanently unclaimed.
                    if touches_spine:
                        for (c, r) in region:
                            owner[r][c] = -2
                            spine_cells.add((c, r))
                        assigned = True
                        continue
                    fallback_candidates = [j for j, c in enumerate(cells_by_room) if c]
                    if not fallback_candidates:
                        continue
                    best = min(
                        fallback_candidates, key=lambda i: len(cells_by_room[i]) - target_cells[i]
                    )
                    for (c, r) in region:
                        owner[r][c] = best
                        cells_by_room[best].add((c, r))
                    assigned = True
                    continue
                # Zone-compatible candidate set (Phase D0): a region fully
                # in the band may only go to a public room, a region fully
                # behind the band only to a non-public room. Fall back to the
                # full adjacency set if filtering would empty it, so a cell
                # is never left unclaimed.
                candidate_rooms = adjacent_rooms
                if zones is not None and band > 0:
                    rmax = max(r for _, r in region)
                    rmin = min(r for _, r in region)
                    if rmax < band:
                        zone_ok = {i for i in adjacent_rooms if zones[i] == 0}
                    elif rmin >= band:
                        zone_ok = {i for i in adjacent_rooms if zones[i] != 0}
                    else:
                        zone_ok = adjacent_rooms
                    candidate_rooms = zone_ok or adjacent_rooms
                under_cap = [i for i in candidate_rooms if len(cells_by_room[i]) < max_cells[i]]
                pool = under_cap or candidate_rooms
                best = min(pool, key=lambda i: len(cells_by_room[i]) - target_cells[i])
                # NOTE (corrected 2026-10, Phase 2): this flag-OFF path dumps the
                # WHOLE connected region on `best`, with NO cap at its
                # max_cells - `under_cap` above is only a soft ordering hint. A
                # previous comment here claimed the region was capped at best's
                # remaining capacity; it never was. That unbounded dump is a
                # root cause of oversized rooms (whole leftover regions landing
                # in one room). The bounded version is
                # _fill_leftovers_bounded(), used on the planner path
                # (Settings.house_planner_enabled); this path is left as-is so
                # flag-off output stays byte-identical.
                for (c, r) in region:
                    owner[r][c] = best
                    cells_by_room[best].add((c, r))
                assigned = True


def _leftover_region(
    owner: list[list[int]], cols: int, rows: int, zones: list[int] | None, band: int
) -> tuple[list[tuple[int, int]], set[int], bool] | None:
    """The first (row-major scan) connected pocket of unclaimed cells, split
    at the public-band boundary exactly like _fill_leftovers() - returns
    (cells, adjacent_room_ids, touches_spine), or None when the grid is full."""
    for r0 in range(rows):
        for c0 in range(cols):
            if owner[r0][c0] != -1:
                continue
            region = [(c0, r0)]
            seen = {(c0, r0)}
            stack = [(c0, r0)]
            adjacent: set[int] = set()
            touches_spine = False
            while stack:
                c, r = stack.pop()
                for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    nc, nr = c + dc, r + dr
                    if not (0 <= nc < cols and 0 <= nr < rows):
                        continue
                    v = owner[nr][nc]
                    if v == -1:
                        crosses_band = zones is not None and band > 0 and (r < band) != (nr < band)
                        if (nc, nr) not in seen and not crosses_band:
                            seen.add((nc, nr))
                            region.append((nc, nr))
                            stack.append((nc, nr))
                    elif v >= 0:
                        adjacent.add(v)
                    elif v == -2:
                        touches_spine = True
            return region, adjacent, touches_spine
    return None


def _spine_strips(spine_cells: set[tuple[int, int]], cols: int, rows: int, band: int) -> tuple[set[int], set[int]]:
    """(vertical_strip_cols, cross_link_rows) of the reserved spine."""
    if not spine_cells:
        return set(), set()
    lo = _clamp(band, 0, max(0, rows - 1))
    vertical = {c for c in range(cols) if all((c, r) in spine_cells for r in range(lo, rows))}
    cross = {r for r in range(rows) if all((c, r) in spine_cells for c in range(cols))}
    return vertical, cross


def _aligned_with_spine(region: list[tuple[int, int]], vertical: set[int], cross: set[int]) -> bool:
    """True when a pocket simply extends a spine strip at its FULL width -
    every row of the pocket spans exactly the vertical strip's columns, or
    every column spans exactly the cross-link's rows."""
    if vertical:
        by_row: dict[int, set[int]] = {}
        for c, r in region:
            by_row.setdefault(r, set()).add(c)
        if all(cs == vertical for cs in by_row.values()):
            return True
    if cross:
        by_col: dict[int, set[int]] = {}
        for c, r in region:
            by_col.setdefault(c, set()).add(r)
        if all(rs == cross for rs in by_col.values()):
            return True
    return False


_DIRS = ((0, -1), (0, 1), (-1, 0), (1, 0))


def _layered_order(start: list[tuple[int, int]], allowed: set[tuple[int, int]], toward: set[tuple[int, int]]) -> list[tuple[int, int]]:
    """Cells of `allowed` reachable from `start`, ordered in straight LAYERS
    parallel to the contact with `toward` (0-1 BFS: a step along the contact
    wall costs 0, a step away from it costs 1). Peeling layer by layer keeps
    both sides rectilinear - a plain BFS peels diamond-shaped fronts, which
    is what produced the stair-stepped diagonal walls."""
    horiz = sum(1 for (c, r) in start if (c - 1, r) in toward or (c + 1, r) in toward)
    vert = sum(1 for (c, r) in start if (c, r - 1) in toward or (c, r + 1) in toward)
    perp_is_x = horiz >= vert
    dist: dict[tuple[int, int], int] = {}
    dq: deque = deque()
    for cell in sorted(start, key=lambda cr: (cr[1], cr[0])):
        if cell not in dist:
            dist[cell] = 0
            dq.append(cell)
    order: list[tuple[int, int]] = []
    done: set[tuple[int, int]] = set()
    while dq:
        cell = dq.popleft()
        if cell in done:
            continue
        done.add(cell)
        order.append(cell)
        for dc, dr in _DIRS:
            n = (cell[0] + dc, cell[1] + dr)
            if n not in allowed or n in done:
                continue
            cost = (1 if dc else 0) if perp_is_x else (1 if dr else 0)
            nd = dist[cell] + cost
            if nd < dist.get(n, 1 << 30):
                dist[n] = nd
                if cost == 0:
                    dq.appendleft(n)
                else:
                    dq.append(n)
    order.sort(key=lambda cr: (dist[cr], cr[1], cr[0]))
    return order


def _claim_nearest(
    owner: list[list[int]], cells_by_room: list[set[tuple[int, int]]], room: int,
    region: list[tuple[int, int]], k: int,
) -> None:
    """`room` claims the `k` region cells nearest its own territory (BFS
    within the region from the cells touching it; deterministic order)."""
    region_set = set(region)
    mine = cells_by_room[room]
    start = [cell for cell in region if any((cell[0] + dc, cell[1] + dr) in mine for dc, dr in _DIRS)]
    for c, r in _layered_order(start, region_set, mine)[:k]:
        owner[r][c] = room
        mine.add((c, r))


def _claim_round_robin(
    owner: list[list[int]], cells_by_room: list[set[tuple[int, int]]], rooms: list[int],
    region: list[tuple[int, int]],
) -> dict[int, int]:
    """Multi-source BFS: every cell of `region` goes to whichever of `rooms`
    reaches it first (ties -> lower room order). Spreads an unavoidable
    overflow across all neighbours so no single room absorbs it. Returns
    {room: cells_claimed}."""
    region_set = set(region)
    queue: deque = deque()
    taken: dict[tuple[int, int], int] = {}
    for room in rooms:
        for cell in sorted(region, key=lambda cr: (cr[1], cr[0])):
            if cell in taken:
                continue
            if any((cell[0] + dc, cell[1] + dr) in cells_by_room[room] for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0))):
                taken[cell] = room
                queue.append(cell)
    while queue:
        c, r = queue.popleft()
        room = taken[(c, r)]
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            n = (c + dc, r + dr)
            if n in region_set and n not in taken:
                taken[n] = room
                queue.append(n)
    counts: dict[int, int] = {}
    for (c, r), room in taken.items():
        owner[r][c] = room
        cells_by_room[room].add((c, r))
        counts[room] = counts.get(room, 0) + 1
    return counts


def _fill_leftovers_bounded(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    target_cells: list[int],
    max_cells: list[int],
    cols: int,
    rows: int,
    spine_cells: set[tuple[int, int]],
    zones: list[int] | None,
    band: int,
    stats: dict,
) -> None:
    """Planner-path leftover fill (Phase 2) - BOUNDED by every room's
    max_cells, unlike _fill_leftovers() (whose whole-region dump is a root
    cause of oversized rooms). Each pass takes the next leftover pocket and:

      1. hands it to the zone-compatible adjacent room furthest under its
         target that still has headroom - but ONLY up to that headroom (the
         nearest cells first); the rest stays for the next pass, which offers
         it to another neighbour;
      2. if no neighbour has headroom: extends the Hallway only when the
         pocket lines up with a spine strip at its full width;
      3. otherwise (absolute last resort) spreads the pocket across ALL its
         neighbours (multi-source BFS) so the overflow past max is as small as
         possible per room - and COUNTS it (stats["overflow_cells"],
         stats["overflow_rooms"]) so the scorecard shows it.

    A pocket with no room neighbour at all keeps the Bug-A rule (absorbed
    into the spine, so no black hole) - counted in
    stats["hallway_unaligned_cells"] when it does not line up with a strip.
    Since the planner sizes the footprint so targets roughly sum to the
    usable area, leftovers here are normally just rounding residue."""
    stats.setdefault("overflow_cells", 0)
    stats.setdefault("overflow_rooms", [])
    stats.setdefault("hallway_aligned_cells", 0)
    stats.setdefault("hallway_unaligned_cells", 0)
    vertical, cross = _spine_strips(spine_cells, cols, rows, band)
    guard = cols * rows + 10
    while guard > 0:
        guard -= 1
        found = _leftover_region(owner, cols, rows, zones, band)
        if found is None:
            return
        region, adjacent, touches_spine = found
        candidates = adjacent
        if adjacent and zones is not None and band > 0:
            rmax = max(r for _, r in region)
            rmin = min(r for _, r in region)
            if rmax < band:
                zone_ok = {i for i in adjacent if zones[i] == 0}
            elif rmin >= band:
                zone_ok = {i for i in adjacent if zones[i] != 0}
            else:
                zone_ok = adjacent
            candidates = zone_ok or adjacent

        if candidates:
            headroom = [i for i in candidates if len(cells_by_room[i]) < max_cells[i]]
            if headroom:
                best = min(
                    headroom,
                    key=lambda i: (len(cells_by_room[i]) - target_cells[i], -(max_cells[i] - len(cells_by_room[i])), i),
                )
                k = min(max_cells[best] - len(cells_by_room[best]), len(region))
                _claim_nearest(owner, cells_by_room, best, region, k)
                continue
            if touches_spine and _aligned_with_spine(region, vertical, cross):
                for (c, r) in region:
                    owner[r][c] = -2
                    spine_cells.add((c, r))
                stats["hallway_aligned_cells"] += len(region)
                continue
            # Last resort: the whole pocket goes to ONE neighbour (a block,
            # not a Voronoi split - splitting across several neighbours drew
            # diagonal stair-step walls); _rebalance_over_max() then pushes
            # the excess along adjacent rooms to wherever headroom remains.
            best = min(candidates, key=lambda i: (len(cells_by_room[i]) - max_cells[i], i))
            for (c, r) in region:
                owner[r][c] = best
                cells_by_room[best].add((c, r))
            stats["overflow_cells"] += len(region)
            stats["overflow_rooms"] = sorted(set(stats["overflow_rooms"]) | {str(best)})
            continue

        if touches_spine:
            aligned = _aligned_with_spine(region, vertical, cross)
            for (c, r) in region:
                owner[r][c] = -2
                spine_cells.add((c, r))
            stats["hallway_aligned_cells" if aligned else "hallway_unaligned_cells"] += len(region)
            continue

        fallback = [j for j, cs in enumerate(cells_by_room) if cs]
        if not fallback:
            return
        best = min(fallback, key=lambda i: len(cells_by_room[i]) - target_cells[i])
        for (c, r) in region:
            owner[r][c] = best
            cells_by_room[best].add((c, r))
        stats["overflow_cells"] += len(region)
        stats["overflow_rooms"] = sorted(set(stats["overflow_rooms"]) | {str(best)})


_MIN_SHARED_WALL_CELLS = 3


def _room_adjacency(owner: list[list[int]], cols: int, rows: int, n: int) -> list[set[int]]:
    """Room adjacency, counting only shared walls at least
    _MIN_SHARED_WALL_CELLS long - a transfer across a 1-2 cell contact would
    push a thin tongue deep into the donor."""
    shared: dict[tuple[int, int], int] = {}
    for r in range(rows):
        for c in range(cols):
            a = owner[r][c]
            if a < 0:
                continue
            for b in ((owner[r][c + 1] if c + 1 < cols else -1), (owner[r + 1][c] if r + 1 < rows else -1)):
                if b >= 0 and b != a:
                    key = (min(a, b), max(a, b))
                    shared[key] = shared.get(key, 0) + 1
    adj: list[set[int]] = [set() for _ in range(n)]
    for (a, b), length in shared.items():
        if length >= _MIN_SHARED_WALL_CELLS:
            adj[a].add(b)
            adj[b].add(a)
    return adj


def _stays_connected(cells: set[tuple[int, int]], removed: tuple[int, int]) -> bool:
    """True when `cells` minus `removed` is still one 4-connected piece."""
    rest = len(cells) - 1
    if rest <= 1:
        return rest >= 0
    start = next(
        ((removed[0] + dc, removed[1] + dr) for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0))
         if (removed[0] + dc, removed[1] + dr) in cells),
        None,
    )
    if start is None:
        return False
    seen = {start, removed}
    stack = [start]
    count = 1
    while stack:
        c, r = stack.pop()
        for dc, dr in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            n = (c + dc, r + dr)
            if n in cells and n not in seen:
                seen.add(n)
                stack.append(n)
                count += 1
    return count == rest


def _transfer(
    owner: list[list[int]], cells_by_room: list[set[tuple[int, int]]], donor: int, receiver: int, k: int
) -> int:
    """Moves up to `k` of `donor`'s cells to `receiver`, starting at their
    shared boundary and working inward (BFS), never disconnecting the donor.
    Returns the number moved."""
    d_cells = cells_by_room[donor]
    r_cells = cells_by_room[receiver]
    frontier = [cell for cell in d_cells if any((cell[0] + dc, cell[1] + dr) in r_cells for dc, dr in _DIRS)]
    moved = 0
    for cell in _layered_order(frontier, set(d_cells), r_cells):
        if moved >= k or len(d_cells) <= 1:
            break
        if not any((cell[0] + dc, cell[1] + dr) in r_cells for dc, dr in _DIRS):
            continue
        if not _stays_connected(d_cells, cell):
            continue
        d_cells.discard(cell)
        r_cells.add(cell)
        owner[cell[1]][cell[0]] = receiver
        moved += 1
    return moved


def _usable_width(cells: set, min_widths: list[float] | None, i: int, cell_w: float, cell_h: float) -> bool:
    """True when the room's largest decomposed part is at least its real
    minimum width (the scorer's HARD "narrow" rule)."""
    if not min_widths or not cells:
        return True
    parts = _decompose(cells)
    _c, _r, pw, ph = max(parts, key=lambda p: p[2] * p[3])
    return min(pw * cell_w, ph * cell_h) >= min_widths[i] * 0.98


def _rebalance_over_max(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    target_cells: list[int],
    max_cells: list[int],
    cols: int,
    rows: int,
    zones: list[int] | None,
    band: int,
    pinned: set[int],
    stats: dict,
    min_widths: list[float] | None = None,
    cell_w: float = 1.0,
    cell_h: float = 1.0,
) -> None:
    """Planner path (Phase 2): an area-flow relaxation after the bounded fill.
    A room left over its max (the fill's last-resort overflow) pushes its
    excess along the shortest chain of adjacent rooms (same zone: front band
    or back) to the nearest room that still has headroom - each room on the
    chain hands cells across its shared wall to the next, so intermediate
    rooms keep their size and only the end of the chain grows. Never moves
    garage/pinned cells, never crosses the band, never disconnects a donor.
    Anything that still cannot move stays counted in stats."""
    n = len(cells_by_room)
    if zones is not None and band > 0:
        group = [0 if zones[i] == 0 else 1 for i in range(n)]
    else:
        group = [1] * n
    for _ in range(4 * n + 4):
        over = sorted(
            (i for i in range(n) if len(cells_by_room[i]) > max_cells[i] and i not in pinned),
            key=lambda i: (-(len(cells_by_room[i]) - max_cells[i]), i),
        )
        if not over:
            break
        adj = _room_adjacency(owner, cols, rows, n)
        progressed = False
        for a in over:
            excess = len(cells_by_room[a]) - max_cells[a]
            if excess <= 0:
                continue
            prev = {a: None}
            queue = deque([a])
            end = None
            while queue and end is None:
                cur = queue.popleft()
                for nb in sorted(adj[cur], key=lambda j: (len(cells_by_room[j]) - target_cells[j], j)):
                    if nb in prev or group[nb] != group[a] or nb in pinned:
                        continue
                    prev[nb] = cur
                    if len(cells_by_room[nb]) < max_cells[nb]:
                        end = nb
                        break
                    queue.append(nb)
            if end is None:
                continue
            path = [end]
            while prev[path[-1]] is not None:
                path.append(prev[path[-1]])
            path.reverse()  # a ... end
            k = min(excess, max_cells[end] - len(cells_by_room[end]))
            snapshot = {i: set(cells_by_room[i]) for i in path}
            ok_before = {i: _usable_width(cells_by_room[i], min_widths, i, cell_w, cell_h) for i in path}
            for step in range(len(path) - 1, 0, -1):
                k = _transfer(owner, cells_by_room, path[step - 1], path[step], k)
                if k <= 0:
                    break
            # Never trade an oversized room for a too-narrow one: undo the
            # whole chain if it left any room on it narrower than its real
            # minimum width when it was not before.
            if any(
                ok_before[i] and not _usable_width(cells_by_room[i], min_widths, i, cell_w, cell_h)
                for i in path
            ):
                for i, cells in snapshot.items():
                    cells_by_room[i] = cells
                    for (c, r) in cells:
                        owner[r][c] = i
                adj[a] = set()  # don't retry this donor this round
                continue
            if k > 0:
                progressed = True
                adj = _room_adjacency(owner, cols, rows, n)
        if not progressed:
            break
    stats["over_max_after_rebalance"] = sum(
        max(len(cells_by_room[i]) - max_cells[i], 0) for i in range(n) if i not in pinned
    )


def _ensure_minimum_presence(
    cells_by_room: list[set[tuple[int, int]]], min_cells: list[int], owner: list[list[int]]
) -> None:
    """Defensive backstop: a room that ended up with ZERO cells (growth and
    fill both failed to reach it - only possible on a genuinely infeasible
    room program, which app/pipeline/feasibility.py's hard gate is expected
    to already have caught before this engine ever runs) steals one cell
    from whichever room is most OVER its own minimum, rather than producing
    an empty room that would crash rectangle decomposition. A stolen cell
    can leave the donor's own region with a hole in a genuinely
    pathological case - an accepted, honestly-stated edge case for this
    last-resort path, not a general guarantee."""
    for i, cells in enumerate(cells_by_room):
        if cells:
            continue
        candidates = [j for j, c in enumerate(cells_by_room) if c]
        if not candidates:
            continue
        donor = max(candidates, key=lambda j: len(cells_by_room[j]) - min_cells[j])
        stolen = min(cells_by_room[donor])
        cells_by_room[donor].discard(stolen)
        cells.add(stolen)
        owner[stolen[1]][stolen[0]] = i


def _decompose(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    """Partitions a cell-set into a deterministic, non-overlapping set of
    (col, row, w, h) rectangles that exactly cover it - a plain rectangular
    blob always yields exactly 1 rect; an L/U blob yields 2-3. Greedy
    maximal-rectangle extraction (smallest remaining cell first, by
    row-major position, for determinism): grow right while still in the
    set, then grow down while the full-width row below is also in the set.
    Not guaranteed to produce the globally minimal rectangle count (that's
    a harder exact-cover problem), but always produces a correct, exact
    partition - an extra part or two just means a slightly more detailed
    wall/door pass, never a rendering bug."""
    remaining = set(cells)
    rects: list[tuple[int, int, int, int]] = []
    while remaining:
        c0, r0 = min(remaining, key=lambda cr: (cr[1], cr[0]))
        w = 1
        while (c0 + w, r0) in remaining:
            w += 1
        h = 1
        while all((c0 + i, r0 + h) in remaining for i in range(w)):
            h += 1
        for rr in range(r0, r0 + h):
            for cc in range(c0, c0 + w):
                remaining.discard((cc, rr))
        rects.append((c0, r0, w, h))
    return rects


# ---- Phase 3: shape discipline (planner path only) ----


def _max_rect(cells) -> tuple[int, int, int, int] | None:
    """Largest-area axis-aligned rectangle (col, row, w, h) fully inside
    `cells` (histogram/stack method over the bbox). Ties: squarer first, then
    lowest (row, col) - deterministic."""
    if not cells:
        return None
    xs = [c for c, _ in cells]
    ys = [r for _, r in cells]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    width = x1 - x0 + 1
    heights = [0] * (width + 1)  # sentinel 0 at the end
    best = None
    best_area = 0
    best_key = None
    for r in range(y0, y1 + 1):
        for k in range(width):
            heights[k] = heights[k] + 1 if (x0 + k, r) in cells else 0
        stack: list[int] = []
        for k in range(width + 1):
            h = heights[k]
            while stack and heights[stack[-1]] >= h:
                top = stack.pop()
                hh = heights[top]
                left = stack[-1] + 1 if stack else 0
                ww = k - left
                area = ww * hh
                if area <= 0 or area < best_area:
                    continue
                key = (-area, max(ww, hh) / min(ww, hh), r - hh + 1, x0 + left)
                if best_key is None or key < best_key:
                    best_key = key
                    best_area = area
                    best = (x0 + left, r - hh + 1, ww, hh)
            stack.append(k)
    return best


def _row_masks(cells, transpose: bool = False) -> dict[int, int]:
    masks: dict[int, int] = {}
    if transpose:
        for c, r in cells:
            masks[c] = masks.get(c, 0) | (1 << r)
    else:
        for c, r in cells:
            masks[r] = masks.get(r, 0) | (1 << c)
    return masks


def _decompose_masks(masks: dict[int, int]) -> list[tuple[int, int, int, int]]:
    """Row-major greedy on per-row bitmasks - the same partition _decompose()
    produces (smallest (row, col) cell first, grow right, then down while the
    full-width row below is present), much faster. Consumes `masks`."""
    rects: list[tuple[int, int, int, int]] = []
    for r in sorted(masks):
        m = masks[r]
        while m:
            c0 = (m & -m).bit_length() - 1
            x = m >> c0
            w = (~x & (x + 1)).bit_length() - 1
            seg = ((1 << w) - 1) << c0
            h = 1
            while (masks.get(r + h, 0) & seg) == seg:
                masks[r + h] &= ~seg
                h += 1
            m &= ~seg
            rects.append((c0, r, w, h))
        masks[r] = 0
    return rects


def _decompose_fast(cells) -> list[tuple[int, int, int, int]]:
    """Same output as _decompose() (row-major greedy), on bitmasks."""
    return _decompose_masks(_row_masks(cells))


def _reflex_corners(cells) -> int:
    """Number of concave (reflex) corners of the cell-set's outline = 2x2
    windows holding exactly 3 cells - a cheap lower bound on rectangle count:
    parts >= reflex/2 + 1 (exact for an L)."""
    masks = _row_masks(cells)
    n = 0
    for r, a0 in masks.items():
        b0 = masks.get(r + 1, 0)
        if not b0:
            continue
        a1, b1 = a0 >> 1, b0 >> 1
        n += (
            (~a0 & a1 & b0 & b1) | (a0 & ~a1 & b0 & b1) | (a0 & a1 & ~b0 & b1) | (a0 & a1 & b0 & ~b1)
        ).bit_count()
    return n


def _cost_lower_bound(cells) -> float:
    if not cells:
        return 0.0
    reflex = _reflex_corners(cells)
    if reflex == 0:
        return 0.0
    parts = -(-reflex // 2) + 1
    return 1.0 if parts == 2 else 10.0 + 4.0 * (parts - 2)


def _decompose_planner(cells) -> list[tuple[int, int, int, int]]:
    """Planner-path decomposition (Phase 3): row-major or column-major greedy,
    whichever gives FEWER rectangles (an L whose long arm runs vertically is
    1+1 column-major but can be 3+ row-major); ties prefer the one with the
    bigger largest part (what the scorer judges for width), then row-major.
    Exact partition either way."""
    row_major = _decompose_fast(cells)
    if len(row_major) == 1:
        return row_major
    col_major = [(r, c, h, w) for (c, r, w, h) in _decompose_masks(_row_masks(cells, transpose=True))]
    best = None
    best_key = None
    for order, parts in enumerate((row_major, col_major)):
        key = (len(parts), -max(w * h for _, _, w, h in parts), order)
        if best_key is None or key < best_key:
            best_key = key
            best = parts
    return best


def _parts_components(parts: list[tuple[int, int, int, int]]) -> int:
    """Number of 4-connected pieces of an exact rectangle partition: rects
    that share an edge segment of positive length are joined (union-find)."""
    parent = list(range(len(parts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(parts)):
        c1, r1, w1, h1 = parts[i]
        for j in range(i + 1, len(parts)):
            c2, r2, w2, h2 = parts[j]
            touch_x = (c1 + w1 == c2 or c2 + w2 == c1) and min(r1 + h1, r2 + h2) > max(r1, r2)
            touch_y = (r1 + h1 == r2 or r2 + h2 == r1) and min(c1 + w1, c2 + w2) > max(c1, c2)
            if touch_x or touch_y:
                parent[find(i)] = find(j)
    return len({find(i) for i in range(len(parts))})


def _components(cells) -> int:
    """Number of 4-connected pieces of a cell-set."""
    return _parts_components(_decompose_fast(cells)) if cells else 0


class _ShapeCtx:
    """Per-candidate context for _rectify_rooms (grid geometry + room meta)."""

    def __init__(self, cols, rows, cell_w, cell_h, unit, targets):
        self.cols, self.rows, self.cell_w, self.cell_h = cols, rows, cell_w, cell_h
        self.need_daylight = to_plot_unit(0.9, unit)
        self.min_w = [
            0.0 if t["category"] in ("garage", "staircase", "hallway") else min_width_for_room(t["name"], unit)
            for t in targets
        ]
        from app.pipeline.planning_rules import DAYLIGHT_HARD_CATEGORIES, DAYLIGHT_SOFT_CATEGORIES

        self.daylit = [t["category"] in DAYLIGHT_HARD_CATEGORIES + DAYLIGHT_SOFT_CATEGORIES for t in targets]
        self._cache: dict = {}

    def info(self, cells) -> tuple[float, list, float]:
        """(shape cost, decomposed parts, best daylight edge in real units)."""
        key = frozenset(cells)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        if not cells:
            res = (0.0, [], 0.0)
            self._cache[key] = res
            return res
        parts = _decompose_planner(cells)
        cost = 0.0
        comps = _parts_components(parts) if len(parts) > 1 else 1
        if comps > 1:
            cost += 50.0 * (comps - 1)
        if len(parts) == 2:
            dicts = [self._part_dict(p) for p in sorted(parts, key=lambda p: -p[2] * p[3])]
            total = sum(d["w"] * d["h"] for d in dicts)
            cost += 1.0 if secondary_part_ok(dicts[0], dicts[1], total) else 10.0
        elif len(parts) > 2:
            cost += 10.0 + 4.0 * (len(parts) - 2)
        daylight = 0.0
        for (c, r, w, h) in parts:
            if r == 0 or r + h == self.rows:
                daylight = max(daylight, w * self.cell_w)
            if c == 0 or c + w == self.cols:
                daylight = max(daylight, h * self.cell_h)
        res = (cost, parts, daylight)
        self._cache[key] = res
        return res

    def lower_bound(self, cells) -> float:
        hit = self._cache.get(cells)
        if hit is not None:
            return hit[0]
        key = ("lb", cells)
        lb = self._cache.get(key)
        if lb is None:
            lb = _cost_lower_bound(cells)
            self._cache[key] = lb
        return lb

    def _part_dict(self, p) -> dict:
        c, r, w, h = p
        return {"x": c * self.cell_w, "y": r * self.cell_h, "w": w * self.cell_w, "h": h * self.cell_h}

    def usable(self, i, parts) -> bool:
        if not parts or self.min_w[i] <= 0:
            return True
        _c, _r, pw, ph = max(parts, key=lambda p: p[2] * p[3])
        return min(pw * self.cell_w, ph * self.cell_h) >= self.min_w[i] * 0.98


_RECTIFY_MAX_MOVES = 120
# Shape cost per cell a room sits over its max: moves that relieve an
# over-max room count as improvements (50 excess cells ~ one wrapped room).
_OVER_MAX_WEIGHT = 0.25


def _over(i: int, size: int, max_cells: list[int]) -> float:
    return _OVER_MAX_WEIGHT * max(0, size - max_cells[i])


def _rectify_rooms(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    targets: list[dict],
    target_cells: list[int],
    min_cells: list[int],
    max_cells: list[int],
    zones: list[int] | None,
    band: int,
    ctx: _ShapeCtx,
    stats: dict | None = None,
) -> None:
    """Phase 3 shape discipline (planner path only), run after growth / bounded
    fill / over-max rebalance and before decomposition.

    Deterministic local search (at most _RECTIFY_MAX_MOVES moves). Three move
    kinds, all between a room and an ADJACENT room:
      1. give a fragment (a non-primary decomposed part, or a branch hanging
         off the largest rectangle, or a stray disconnected piece) to the
         neighbour;
      2. swap: the neighbour takes the fragment and, if that would put it past
         its max, hands back the same number of its own cells farthest from
         the fragment (so a small room enclosed by a big one slides inside it);
      3. pair re-split: one straight cut, or a corner-carved rectangle, across
         the two rooms' combined cells (tried only when no 1/2 move helps a
         wrapped or over-max room).
    A move is applied only if it lowers the two rooms' combined cost (shape
    cost from _ShapeCtx.info plus _OVER_MAX_WEIGHT per cell over max) and
    keeps every guarantee:
      - every cell keeps an owner (cells only change hands; tiling is exact);
      - spine/Hallway cells are never moved and never receive a fragment;
      - garage/staircase never receive (they are pinned to a functional size);
      - no room ends above max(its max, its current size);
      - the donor never drops below its min area (rounded up), never loses a
        usable primary width it had, never loses a >= 0.9 m daylight edge it
        had (the same holds for the recipient's width/daylight);
      - nothing crosses the front public band boundary.
    A fragment no neighbour can legally take stays where it is (the room then
    stays `wrapped` and is scored as such - never silently hidden), except a
    disconnected piece, which _reconnect_rooms() relocates afterwards."""
    n = len(cells_by_room)
    cols, rows = ctx.cols, ctx.rows
    if zones is not None and band > 0:
        row_lo = [0 if zones[i] == 0 else band for i in range(n)]
        row_hi = [band - 1 if zones[i] == 0 else rows - 1 for i in range(n)]
    else:
        row_lo, row_hi = [0] * n, [rows - 1] * n
    no_receive = {i for i in range(n) if targets[i]["category"] in ("garage", "staircase", "hallway")}
    # Min-area floor in cells, rounded UP (min_cells rounds to nearest, which
    # can sit just under the scorer's 0.98 x min-area tolerance).
    cell_area = max(ctx.cell_w * ctx.cell_h, 1e-9)
    floor_cells = [
        max(min_cells[i], math.ceil(0.985 * float(targets[i].get("min_area") or 0.0) / cell_area))
        for i in range(n)
    ]

    def over(i: int, size: int) -> float:
        return _OVER_MAX_WEIGHT * max(0, size - max_cells[i])

    moves = 0
    # A donor whose own cells and every neighbour's cells are unchanged since
    # it last found no move cannot find one now - skip it (version counters).
    version = [0] * n
    no_move_at: dict[int, tuple] = {}
    while moves < _RECTIFY_MAX_MOVES:
        order = sorted(
            (i for i in range(n) if cells_by_room[i]),
            key=lambda i: (-(ctx.info(cells_by_room[i])[0] + over(i, len(cells_by_room[i]))), i),
        )
        applied = False
        for d in order:
            d_cells = cells_by_room[d]
            d_cost, d_parts, d_day = ctx.info(d_cells)
            d_over = over(d, len(d_cells))
            if d_cost + d_over <= 1.0:
                break  # sorted by cost: nothing worse remains
            around = set()
            for (c, r) in d_cells:
                for dc, dr in _DIRS:
                    nc, nr = c + dc, r + dr
                    if 0 <= nc < cols and 0 <= nr < rows and owner[nr][nc] >= 0 and owner[nr][nc] != d:
                        around.add(owner[nr][nc])
            state = (version[d],) + tuple((o, version[o]) for o in sorted(around))
            if no_move_at.get(d) == state:
                continue
            d_usable = ctx.usable(d, d_parts)
            fragments = _fragments(d_cells, d_parts)
            best = None
            for frag in fragments:
                if len(d_cells) - len(frag) < floor_cells[d]:
                    continue
                f_rows = [r for _, r in frag]
                f_lo, f_hi = min(f_rows), max(f_rows)
                nbrs = set()
                for (c, r) in frag:
                    for dc, dr in _DIRS:
                        nc, nr = c + dc, r + dr
                        if 0 <= nc < cols and 0 <= nr < rows:
                            o = owner[nr][nc]
                            if o >= 0 and o != d and o not in no_receive:
                                nbrs.add(o)
                if not nbrs:
                    continue
                d_after = d_cells - frag
                da_cost, da_parts, da_day = ctx.info(d_after)
                if d_usable and not ctx.usable(d, da_parts):
                    continue
                if ctx.daylit[d] and d_day >= ctx.need_daylight - 1e-6 and da_day < ctx.need_daylight - 1e-6:
                    continue
                for rcv in sorted(nbrs):
                    if f_lo < row_lo[rcv] or f_hi > row_hi[rcv]:
                        continue
                    r_cells = cells_by_room[rcv]
                    r_cost, r_parts, r_day = ctx.info(r_cells)
                    r_grown = r_cells | frag
                    excess = len(r_grown) - max_cells[rcv]
                    give_back: frozenset = frozenset()
                    if excess > 0:
                        # Swap: the recipient takes the fragment and hands back
                        # `excess` of its own cells farthest from it (still
                        # touching the donor) - a small room enclosed by a big
                        # one slides inside it instead of staying wrapped.
                        give_back = _give_back(r_cells, frag, d_cells - frag, excess, cols, rows)
                        if len(give_back) != excess or any(
                            r < row_lo[d] or r > row_hi[d] for _, r in give_back
                        ):
                            continue
                        r_after = r_grown - give_back
                        d_after_s = (d_cells - frag) | give_back
                        if len(d_after_s) < floor_cells[d]:
                            continue
                        if len(d_after_s) > max(max_cells[d], len(d_cells)):
                            continue  # a swap never pushes the donor past its max
                        das_cost, das_parts, das_day = ctx.info(d_after_s)
                        if d_usable and not ctx.usable(d, das_parts):
                            continue
                        if ctx.daylit[d] and d_day >= ctx.need_daylight - 1e-6 and das_day < ctx.need_daylight - 1e-6:
                            continue
                        donor_cost = das_cost
                    else:
                        if d_usable and not ctx.usable(d, da_parts):
                            continue
                        if ctx.daylit[d] and d_day >= ctx.need_daylight - 1e-6 and da_day < ctx.need_daylight - 1e-6:
                            continue
                        r_after = r_grown
                        donor_cost = da_cost
                    ra_cost, ra_parts, ra_day = ctx.info(r_after)
                    if ctx.usable(rcv, r_parts) and not ctx.usable(rcv, ra_parts):
                        continue
                    if ctx.daylit[rcv] and r_day >= ctx.need_daylight - 1e-6 and ra_day < ctx.need_daylight - 1e-6:
                        continue
                    d_new = len(d_cells) - len(frag) + len(give_back)
                    r_new = len(r_cells) + len(frag) - len(give_back)
                    delta = (donor_cost + ra_cost + over(d, d_new) + over(rcv, r_new)) - (
                        d_cost + r_cost + d_over + over(rcv, len(r_cells))
                    )
                    if delta >= -1e-9:
                        continue
                    # Prefer the biggest improvement, then a plain move over a
                    # swap, then a recipient under target, then the most
                    # headroom; deterministic tie-break.
                    key = (
                        delta,
                        len(give_back),
                        len(r_cells) - target_cells[rcv],
                        -(max_cells[rcv] - len(r_cells)),
                        rcv,
                        min(frag),
                    )
                    if best is None or key < best[0]:
                        best = (key, frag, rcv, give_back)
            # Pair re-split: one straight cut across (donor + a neighbour).
            pair = None
            if best is None and (d_cost >= 10.0 or d_over > 0):
                pair = _best_pair_resplit(
                    d, owner, cells_by_room, targets, floor_cells, max_cells, row_lo, row_hi, ctx, no_receive
                )
            if pair is not None:
                _delta, other, new_d, new_o = pair
                for (c, r) in new_d:
                    owner[r][c] = d
                for (c, r) in new_o:
                    owner[r][c] = other
                cells_by_room[d] = set(new_d)
                cells_by_room[other] = set(new_o)
                version[d] += 1
                version[other] += 1
                moves += 1
                applied = True
                break
            if best is not None:
                _key, frag, rcv, give_back = best
                for (c, r) in frag:
                    owner[r][c] = rcv
                for (c, r) in give_back:
                    owner[r][c] = d
                cells_by_room[d] = (d_cells - frag) | give_back
                cells_by_room[rcv] = (cells_by_room[rcv] | frag) - give_back
                version[d] += 1
                version[rcv] += 1
                moves += 1
                applied = True
                break
            no_move_at[d] = state
        if not applied:
            break
    _reconnect_rooms(owner, cells_by_room, targets, target_cells, max_cells, row_lo, row_hi, no_receive, ctx, stats)
    if stats is not None:
        stats["rectify_moves"] = stats.get("rectify_moves", 0) + moves


def _reconnect_rooms(
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    targets: list[dict],
    target_cells: list[int],
    max_cells: list[int],
    row_lo: list[int],
    row_hi: list[int],
    no_receive: set[int],
    ctx: "_ShapeCtx",
    stats: dict | None,
) -> None:
    """Last resort after the local search: a room still split into several
    disconnected pieces (a piece stranded across the hallway renders as an
    unlabeled, door-less box) keeps its largest piece; every other piece goes
    to an adjacent room with headroom, else into an adjacent Hallway, else to
    the adjacent room most under its target even past its max (as the plan
    allows when no neighbour has headroom). Every cell keeps an owner.
    Counted in stats."""
    cols, rows = ctx.cols, ctx.rows
    for d in range(len(cells_by_room)):
        cells = cells_by_room[d]
        if not cells or ctx.info(cells)[0] < 50.0:
            continue  # cost >= 50 only when disconnected
        pieces = []
        rest = set(cells)
        while rest:
            start = min(rest, key=lambda cr: (cr[1], cr[0]))
            comp = {start}
            rest.discard(start)
            stack = [start]
            while stack:
                c, r = stack.pop()
                for dc, dr in _DIRS:
                    nb = (c + dc, r + dr)
                    if nb in rest:
                        rest.discard(nb)
                        comp.add(nb)
                        stack.append(nb)
            pieces.append(comp)
        pieces.sort(key=lambda p: (-len(p), min(p)))
        for piece in pieces[1:]:
            p_lo = min(r for _, r in piece)
            p_hi = max(r for _, r in piece)
            nbrs = set()
            touches_hall = False
            for (c, r) in piece:
                for dc, dr in _DIRS:
                    nc, nr = c + dc, r + dr
                    if 0 <= nc < cols and 0 <= nr < rows:
                        o = owner[nr][nc]
                        if o == -2:
                            touches_hall = True
                        elif o >= 0 and o != d and o not in no_receive and row_lo[o] <= p_lo and p_hi <= row_hi[o]:
                            nbrs.add(o)
            roomy = [i for i in nbrs if len(cells_by_room[i]) + len(piece) <= max_cells[i]]
            if roomy or (nbrs and not touches_hall):
                # Headroom first; else (no hallway either) the neighbour most
                # under its target, even past its max.
                rcv = min(roomy or nbrs, key=lambda i: (len(cells_by_room[i]) - target_cells[i], i))
                for (c, r) in piece:
                    owner[r][c] = rcv
                cells_by_room[rcv] = cells_by_room[rcv] | piece
            elif touches_hall:
                for (c, r) in piece:
                    owner[r][c] = -2
            else:
                continue  # nowhere legal to go: stays (and stays scored)
            cells_by_room[d] = cells_by_room[d] - piece
            if stats is not None:
                stats["reconnected_cells"] = stats.get("reconnected_cells", 0) + len(piece)


def _best_pair_resplit(
    d: int,
    owner: list[list[int]],
    cells_by_room: list[set[tuple[int, int]]],
    targets: list[dict],
    min_cells: list[int],
    max_cells: list[int],
    row_lo: list[int],
    row_hi: list[int],
    ctx: "_ShapeCtx",
    no_receive: set[int],
):
    """Best re-partition of room `d` and one adjacent room - by one straight
    cut across their combined cells, or by carving one of them as a rectangle
    into a corner of the union - the move that untangles a small room
    enclosed by a bigger one, which single fragment moves cannot do without
    passing through worse states. Both rooms stay within [min, max], inside
    their zone rows, keep a usable primary width and any daylight edge they
    already had. Returns (delta, other, new_d_cells, new_other_cells) for the
    best strictly-improving partition, else None. Garage/staircase/hallway
    never take part."""
    if d in no_receive:
        return None
    d_cells = cells_by_room[d]
    nbrs = set()
    for (c, r) in d_cells:
        for dc, dr in _DIRS:
            nc, nr = c + dc, r + dr
            if 0 <= nc < ctx.cols and 0 <= nr < ctx.rows:
                o = owner[nr][nc]
                if o >= 0 and o != d and o not in no_receive:
                    nbrs.add(o)
    d_cost, d_parts, d_day = ctx.info(d_cells)
    need = ctx.need_daylight - 1e-6
    best = None
    for o in sorted(nbrs):
        o_cells = cells_by_room[o]
        o_cost, o_parts, o_day = ctx.info(o_cells)
        before = d_cost + o_cost + _over(d, len(d_cells), max_cells) + _over(o, len(o_cells), max_cells)
        if before <= 0:
            continue
        union = d_cells | o_cells
        ux = [c for c, _ in union]
        uy = [r for _, r in union]
        if len(union) < _PAIR_MIN_BBOX_FILL * (max(ux) - min(ux) + 1) * (max(uy) - min(uy) + 1):
            continue  # irregular union: no two-clean-shapes split worth searching
        d_ok, o_ok = ctx.usable(d, d_parts), ctx.usable(o, o_parts)
        u_lo, u_hi = min(uy), max(uy)
        zone_free = row_lo[d] <= u_lo and u_hi <= row_hi[d] and row_lo[o] <= u_lo and u_hi <= row_hi[o]
        # Bounds: never shrink below min, never grow past max - but a room the
        # bounded fill already left over its max may keep (not grow) its size.
        d_bounds = (min(min_cells[d], len(d_cells)), max(max_cells[d], len(d_cells)))
        o_bounds = (min(min_cells[o], len(o_cells)), max(max_cells[o], len(o_cells)))
        for new_d, new_o in _pair_partitions(union, d_bounds, o_bounds, len(d_cells), len(o_cells)):
            if not zone_free and (
                any(r < row_lo[d] or r > row_hi[d] for _, r in new_d)
                or any(r < row_lo[o] or r > row_hi[o] for _, r in new_o)
            ):
                continue
            new_over = _over(d, len(new_d), max_cells) + _over(o, len(new_o), max_cells)
            if ctx.lower_bound(new_d) + ctx.lower_bound(new_o) + new_over >= before - 1e-9:
                continue
            nd_cost, nd_parts, nd_day = ctx.info(new_d)
            no_cost, no_parts, no_day = ctx.info(new_o)
            delta = nd_cost + no_cost + new_over - before
            if delta >= -1e-9:
                continue
            if (d_ok and not ctx.usable(d, nd_parts)) or (o_ok and not ctx.usable(o, no_parts)):
                continue
            if ctx.daylit[d] and d_day >= need and nd_day < need:
                continue
            if ctx.daylit[o] and o_day >= need and no_day < need:
                continue
            drift = abs(len(new_d) - len(d_cells))
            key = (delta, drift, o, min(new_d))
            if best is None or key < best[0]:
                best = (key, o, new_d, new_o)
    if best is None:
        return None
    key, o, new_d, new_o = best
    return key[0], o, new_d, new_o


_PAIR_MIN_BBOX_FILL = 0.5  # pair re-split only for near-rectangular unions
_CARVE_SIZE_SLACK = 0.25  # corner carves stay within +-25% of the room's current size
_CARVE_MAX_ASPECT = 3.0


def _pair_partitions(
    union: set, d_bounds: tuple[int, int], o_bounds: tuple[int, int], d_size: int, o_size: int
):
    """Candidate (new_d, new_o) splits of `union`, both sizes within bounds:
    every straight vertical/horizontal cut (both assignments), plus a
    rectangle carved into each corner of the union's bbox for either room
    (the rest going to the other). Deterministic order."""
    xs = [c for c, _ in union]
    ys = [r for _, r in union]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    seen: set = set()

    def ok(a, b):
        return d_bounds[0] <= len(a) <= d_bounds[1] and o_bounds[0] <= len(b) <= o_bounds[1]

    total = len(union)
    for axis in (0, 1):
        lo, hi = (x0, x1) if axis == 0 else (y0, y1)
        counts: dict[int, int] = {}
        span_lo: dict[int, int] = {}
        span_hi: dict[int, int] = {}
        for cell in union:
            a_coord, b_coord = cell[axis], cell[1 - axis]
            counts[a_coord] = counts.get(a_coord, 0) + 1
            span_lo[a_coord] = min(span_lo.get(a_coord, b_coord), b_coord)
            span_hi[a_coord] = max(span_hi.get(a_coord, b_coord), b_coord)
        # Prefix/suffix (count, min, max) so "is the low/high side an exact
        # rectangle" is O(1) per cut - only cuts with at least one exact
        # rectangle side are worth evaluating.
        pre = [(0, 10**9, -1)]
        for k in range(lo, hi + 1):
            cnt, mn, mx = pre[-1]
            pre.append((cnt + counts.get(k, 0), min(mn, span_lo.get(k, 10**9)), max(mx, span_hi.get(k, -1))))
        suf = [(0, 10**9, -1)]
        for k in range(hi, lo - 1, -1):
            cnt, mn, mx = suf[-1]
            suf.append((cnt + counts.get(k, 0), min(mn, span_lo.get(k, 10**9)), max(mx, span_hi.get(k, -1))))
        for k in range(lo + 1, hi + 1):
            n_low, mn_l, mx_l = pre[k - lo]
            n_high, mn_h, mx_h = suf[hi - k + 1]
            low_rect = n_low == (k - lo) * (mx_l - mn_l + 1)
            high_rect = n_high == (hi - k + 1) * (mx_h - mn_h + 1)
            if not (low_rect or high_rect):
                continue
            for a_n, b_n, low_is_d in ((n_low, n_high, True), (n_high, n_low, False)):
                if not (d_bounds[0] <= a_n <= d_bounds[1] and o_bounds[0] <= b_n <= o_bounds[1]):
                    continue
                if abs(a_n - d_size) > _CARVE_SIZE_SLACK * max(d_size, o_size):
                    continue
                low = frozenset(cell for cell in union if cell[axis] < k)
                high = frozenset(union - low)
                a, b = (low, high) if low_is_d else (high, low)
                if a and b and a not in seen:
                    seen.add(a)
                    yield a, b
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    # 2-D prefix sums over the bbox: O(1) "is this rectangle inside union".
    pre = [[0] * (bw + 1) for _ in range(bh + 1)]
    for j in range(bh):
        row_acc = 0
        for i in range(bw):
            row_acc += 1 if (x0 + i, y0 + j) in union else 0
            pre[j + 1][i + 1] = pre[j][i + 1] + row_acc

    def inside(cx, cy, w, h):
        i0, j0 = cx - x0, cy - y0
        return pre[j0 + h][i0 + w] - pre[j0][i0 + w] - pre[j0 + h][i0] + pre[j0][i0] == w * h

    for carve_d in (True, False):
        lo_n, hi_n = d_bounds if carve_d else o_bounds
        size = d_size if carve_d else o_size
        lo_n = max(lo_n, int(size * (1 - _CARVE_SIZE_SLACK)))
        hi_n = min(hi_n, int(size * (1 + _CARVE_SIZE_SLACK)) + 1)
        for w in range(1, bw):
            # One or two heights per width: the carved room keeps ~its size.
            for h in sorted({max(1, size // w), max(1, -(-size // w))}):
                if h >= bh or not (lo_n <= w * h <= hi_n) or max(w, h) > _CARVE_MAX_ASPECT * min(w, h):
                    continue
                for cx, cy in ((x0, y0), (x1 - w + 1, y0), (x0, y1 - h + 1), (x1 - w + 1, y1 - h + 1)):
                    if not inside(cx, cy, w, h):
                        continue
                    rest_n = total - w * h
                    if carve_d and not (o_bounds[0] <= rest_n <= o_bounds[1]):
                        continue
                    if not carve_d and not (d_bounds[0] <= rest_n <= d_bounds[1]):
                        continue
                    rect = frozenset((c, r) for r in range(cy, cy + h) for c in range(cx, cx + w))
                    rest = frozenset(union - rect)
                    a, b = (rect, rest) if carve_d else (rest, rect)
                    if a and b and ok(a, b) and a not in seen:
                        seen.add(a)
                        yield a, b


def _give_back(r_cells: set, frag: frozenset, donor_rest: set, k: int, cols: int, rows: int) -> frozenset:
    """Picks `k` of the recipient's ORIGINAL cells to hand back to the donor
    in a swap: farthest (BFS distance through recipient + fragment) from the
    fragment first, each one touching the donor or an already-picked cell, so
    the hand-back stays attached to the donor. Never picks a fragment cell.
    Returns fewer than k cells when it cannot find enough (the caller then
    rejects the swap)."""
    region = set(r_cells) | set(frag)
    dist = {c: 0 for c in frag}
    queue = deque(sorted(frag))
    while queue:
        c, r = queue.popleft()
        for dc, dr in _DIRS:
            nb = (c + dc, r + dr)
            if nb in region and nb not in dist:
                dist[nb] = dist[(c, r)] + 1
                queue.append(nb)
    picked: set = set()
    attach = set(donor_rest)
    pool = sorted((c for c in r_cells if c not in frag), key=lambda c: (-dist.get(c, 10**6), c[1], c[0]))
    progressed = True
    while len(picked) < k and progressed:
        progressed = False
        for cell in pool:
            if cell in picked:
                continue
            if any((cell[0] + dc, cell[1] + dr) in attach for dc, dr in _DIRS):
                picked.add(cell)
                attach.add(cell)
                progressed = True
                break
    return frozenset(picked)


def _fragments(cells: set, parts: list) -> list[frozenset]:
    """Candidate pieces a room may give away: every non-primary decomposed
    part, every connected branch of (cells - largest rectangle), and every
    connected component that does not contain the primary. Deduplicated,
    deterministic order."""
    out: list[frozenset] = []
    seen: set = set()

    def add(fs):
        if fs and fs not in seen and len(fs) < len(cells):
            seen.add(fs)
            out.append(fs)

    ordered = sorted(parts, key=lambda p: (-p[2] * p[3], p[1], p[0]))
    for (c, r, w, h) in ordered[1:]:
        add(frozenset((cc, rr) for rr in range(r, r + h) for cc in range(c, c + w)))
    mr = _max_rect(cells)
    if mr is not None:
        c0, r0, w0, h0 = mr
        primary = {(cc, rr) for rr in range(r0, r0 + h0) for cc in range(c0, c0 + w0)}
        rest = set(cells) - primary
        while rest:
            start = min(rest, key=lambda cr: (cr[1], cr[0]))
            comp = {start}
            rest.discard(start)
            stack = [start]
            while stack:
                c, r = stack.pop()
                for dc, dr in _DIRS:
                    nb = (c + dc, r + dr)
                    if nb in rest:
                        rest.discard(nb)
                        comp.add(nb)
                        stack.append(nb)
            add(frozenset(comp))
    return out


def _map_room(room: dict, fn) -> dict:
    """Applies `fn` (a coordinate transform taking/returning a plain
    {"x","y","w","h"} dict) identically to a room's own bounding-box fields
    AND every one of its parts - keeps the room-level x/y/w/h always equal
    to the true bbox of its (transformed) parts, the invariant
    floor_layout.room_parts()/footprint_bbox() depend on."""
    new_room = {**room, **fn({"x": room["x"], "y": room["y"], "w": room["w"], "h": room["h"]})}
    if room.get("parts"):
        new_room["parts"] = [fn(p) for p in room["parts"]]
    return new_room


def _front_setback_offset(
    facing: str, footprint_length: float, footprint_width: float, net_length: float, net_width: float, unit: str
) -> tuple[float, float]:
    """Phase C0's "real front offset, not left to centering" - extra (dx,
    dy) added ON TOP OF `_anchor_offsets()`'s own plain front-flush (always
    0 on the facing axis). Without this, every bit of slack a Phase-C0
    coverage-cap shrink freed up would land entirely at the REAR (since
    `_anchor_offsets` only centers the CROSS axis and front-flushes the
    facing axis) - leaving zero room between the house and the road for a
    driveway + a parked car even on a plot that's genuinely reserving yard.

    Bounded by whatever real slack exists on the facing axis
    (`net_* - footprint_*`) - never larger than that, never negative, so
    this is a strict no-op (0.0) whenever the footprint already fills the
    net envelope on that axis (a small/right-sized plot, or a plot whose
    room program was never shrunk by the coverage cap in the first place).
    Deliberately NOT gated on a garage/driveway actually existing - a real
    front yard (lawn + walkway, even with no driveway) is still the correct
    "house on a property" look per the priority directive, and gating this
    on garage presence would make otherwise-identical large plots look
    inconsistent depending on whether a garage happened to be requested.

    `_anchor_offsets` itself (shared with the frozen `rect` engine, Golden
    Rule 1) is NOT modified - this is applied here, in mask's own transform
    pipeline, as the plan's own C0 task explicitly calls for."""
    setback = to_plot_unit(MIN_FRONT_SETBACK_M, unit)
    if facing in ("north", "south"):
        slack = max(net_width - footprint_width, 0.0)
        return 0.0, min(slack, setback)
    slack = max(net_length - footprint_length, 0.0)
    return min(slack, setback), 0.0


def _apply_facing_transform(
    rooms: list[dict],
    facing: str,
    footprint_length: float,
    footprint_width: float,
    net_length: float,
    net_width: float,
    exterior: float,
    real_length: float,
    real_width: float,
    unit: str,
) -> list[dict]:
    """The same 4-step transform floor_layout.layout_floor_with_warnings()
    applies around its core algorithm (east/west transpose, anchor within
    any real yard, net-envelope offset, south/east mirror) - applied here
    per-ROOM via _map_room() so it reaches every part, not just the room's
    own bbox. Phase C0 (2026-10) adds one more offset on top of the plain
    anchor - see _front_setback_offset()'s own docstring."""
    if facing in ("east", "west"):
        rooms = [_map_room(r, lambda p: {**p, "x": p["y"], "y": p["x"], "w": p["h"], "h": p["w"]}) for r in rooms]

    anchor_dx, anchor_dy = _anchor_offsets(facing, footprint_length, footprint_width, net_length, net_width)
    setback_dx, setback_dy = _front_setback_offset(
        facing, footprint_length, footprint_width, net_length, net_width, unit
    )
    anchor_dx += setback_dx
    anchor_dy += setback_dy
    if anchor_dx or anchor_dy:
        rooms = [
            _map_room(r, lambda p, dx=anchor_dx, dy=anchor_dy: {**p, "x": p["x"] + dx, "y": p["y"] + dy})
            for r in rooms
        ]

    rooms = [_map_room(r, lambda p: {**p, "x": p["x"] + exterior, "y": p["y"] + exterior}) for r in rooms]

    if facing == "south":
        rooms = [_map_room(r, lambda p: {**p, "y": real_width - p["y"] - p["h"]}) for r in rooms]
    elif facing == "east":
        rooms = [_map_room(r, lambda p: {**p, "x": real_length - p["x"] - p["w"]}) for r in rooms]

    return rooms
