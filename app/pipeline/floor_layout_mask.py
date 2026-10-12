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
from collections import deque

from app.pipeline.floor_layout import (
    _VALID_FACINGS,
    _anchor_offsets,
    _arrange_suites,
    _footprint_target_area,
    _sort_key,
    _zone_key,
)
from app.pipeline.layout_quality import score_floor_layout
from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    max_area_for_room,
    min_area_for_room,
    preferred_area_for_room,
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

# Extra safety margin (on top of floor_layout._footprint_target_area()'s
# own preferred-area-sum + overhead) - see module docstring's "Known
# simplifications" for why this engine uses a fixed multiplier instead of
# the rect engine's geometry-verification probe.
FOOTPRINT_EXTRA_MARGIN = 1.3

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
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None
) -> list[dict]:
    """Same contract as floor_layout.layout_floor() - plain rects/parts
    list, no warnings."""
    rects, _warnings = layout_floor_with_warnings(rooms, dimensions, garage_cars, facing)
    return rects


def layout_floor_with_warnings(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None
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
            candidate = _grow_floor(targets, core_dimensions, width_delta, cross_link)
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
        target_area = min(target_area, max_absorbable)

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
    targets: list[dict], dimensions: dict, spine_width_delta: int, cross_link: bool
) -> list[dict]:
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
    band = _public_band_rows(targets, public_idx, cols, rows, cell_area)
    zones = [_zone_key(t["name"]) for t in targets]
    spine_cells: set[tuple[int, int]] = set()
    if has_spine:
        spine_cells = _reserve_spine(
            cols, rows, cell_w, cell_h, unit, spine_width_delta, cross_link, len(targets), band
        )

    owner = [[-1 for _ in range(cols)] for _ in range(rows)]
    for (c, r) in spine_cells:
        owner[r][c] = -2

    target_cells = [max(1, round(t["preferred_area"] / cell_area)) for t in targets]
    min_cells = [max(1, round(t["min_area"] / cell_area)) for t in targets]
    max_cells = [max(min_cells[i], round(targets[i]["max_area"] / cell_area)) for i in range(len(targets))]

    seeds = _seed_rooms(
        targets, public_idx, circulation_idx, private_idx, cols, rows, spine_cells, min_cells, band
    )

    cells_by_room: list[set[tuple[int, int]]] = [set() for _ in targets]
    for i, seed in enumerate(seeds):
        if seed is not None:
            cells_by_room[i].add(seed)
            owner[seed[1]][seed[0]] = i

    _grow_rooms(owner, cells_by_room, seeds, cols, rows, target_cells, min_cells, max_cells, zones, band)
    _fill_leftovers(owner, cells_by_room, target_cells, max_cells, cols, rows, spine_cells, zones, band)
    _ensure_minimum_presence(cells_by_room, min_cells, owner)

    rooms_out = []
    for t, cells in zip(targets, cells_by_room):
        room = _cells_to_room(t["name"], cells, cell_w, cell_h)
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
        hallway = _cells_to_room("Hallway", final_spine_cells, cell_w, cell_h)
        if hallway is not None:
            rooms_out.append(hallway)

    return rooms_out


def _cells_to_room(name: str, cells: set[tuple[int, int]], cell_w: float, cell_h: float) -> dict | None:
    if not cells:
        return None
    part_cells = _decompose(cells)
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


def _reserve_spine(
    cols: int, rows: int, cell_w: float, cell_h: float, unit: str, width_delta: int, cross_link: bool,
    room_count: int, band_start: int = 0
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
    hw = min(_hallway_width_cells(min(cols, rows), cell_w, unit, width_delta), max(1, cols - 2))
    cx0 = max(0, (cols - hw) // 2)
    spine = {(c, r) for r in range(band_start, rows) for c in range(cx0, min(cols, cx0 + hw))}

    if cross_link and room_count >= _MIN_ROOMS_FOR_CROSS_LINK:
        hh = min(_hallway_width_cells(min(cols, rows), cell_h, unit, width_delta), max(1, rows - 2))
        ry0 = _clamp(round(rows * 0.62) - hh // 2, band_start, max(band_start, rows - hh))
        candidate = spine | {(c, r) for r in range(ry0, min(rows, ry0 + hh)) for c in range(cols)}
        if len(candidate) <= cols * rows * 0.6:
            spine = candidate

    return spine


def _public_band_rows(
    targets: list[dict], public_idx: list[int], cols: int, rows: int, cell_area: float
) -> int:
    """Depth (in rows) of the front public band - enough rows for every
    public room's own PREFERRED area to fit across the full footprint width
    (`cols`), so public rooms grow wide-and-shallow rather than deep
    slivers, clamped to at most _MAX_PUBLIC_BAND_FRACTION of the floor so
    the private zone + spine always keep real depth behind it. Returns 0
    when there are no public rooms (no band, whole floor is private/
    circulation - the growth row-bounds then degrade to the full [0, rows)
    range, i.e. a strict no-op)."""
    if not public_idx or cols <= 0 or cell_area <= 0:
        return 0
    total = sum(max(1, round(targets[i]["preferred_area"] / cell_area)) for i in public_idx)
    band = max(1, -(-total // cols))  # ceil(total / cols)
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

    # Public rooms: spread along the front row, left to right in their
    # already front-of-house-ordered sequence (floor_layout._sort_key()).
    n_public = len(public_idx)
    for k, i in enumerate(public_idx):
        col = _clamp(round(((k + 0.5) / n_public) * (cols - 1)), 0, cols - 1) if n_public else 0
        cell = _first_free_near(col, 0, cols, rows, occupied)
        if cell is not None:
            seeds[i] = cell
            occupied.add(cell)

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
    private_other = [i for i in private_idx if i not in private_egress]
    n_egress = len(private_egress)
    boundary_path = _exterior_boundary_path(cols, rows, band)
    for k, i in enumerate(private_egress):
        idx = _clamp(round(((k + 0.5) / n_egress) * (len(boundary_path) - 1)), 0, len(boundary_path) - 1) if n_egress else 0
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
                # Real bug fixed during development (found by rendering a
                # genuinely minimal 4-room program and seeing a bedroom
                # balloon to 983 cells against a 180-cell real maximum):
                # the WHOLE connected region used to be dumped on `best`
                # regardless of size, even when `best` only had a little
                # real capacity left - because a region with few/no private
                # rooms competing for it (no spine reserved at all below
                # _MIN_ROOMS_FOR_SPINE) can be the size of most of the
                # remaining plot. When `best` is genuinely under its cap,
                # only claim up to its REMAINING capacity from this region -
                # leftover cells stay unclaimed for this same outer loop's
                # next pass, which re-discovers them as a (now smaller)
                # region and offers them to a different candidate. Only
                # skip this cap when `best` was picked as a last resort
                # (already at/over its own max, no under-cap candidate
                # existed at all) - the whole-region dump is the correct,
                # already-accepted behavior there (see this function's own
                # docstring), not a fresh case to also bound.
                for (c, r) in region:
                    owner[r][c] = best
                    cells_by_room[best].add((c, r))
                assigned = True


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
