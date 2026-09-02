"""Pure, deterministic room-rectangle layout for one floor - no I/O, no model
calls, nothing to mock in tests. Takes Gemini's free-text room list
(GeminiProvider.generate_room_layout) and slices the plot into non-overlapping
room rectangles that exactly tile it.

Room "area" values from Gemini are treated as RELATIVE WEIGHTS, not literal
square footage - the layout always rescales to exactly fill the plot's real
length x width regardless of what absolute numbers the model returned, so the
algorithm never needs those numbers to sum to anything in particular.

Algorithm: simple recursive slicing (a "slice and dice" treemap, not the more
complex squarified-treemap variant). At each step the room list is split into
two roughly weight-balanced halves and the box is cut along its LONGER side -
this keeps resulting rectangles closer to square than naive fixed-axis
alternation would, without the bookkeeping of a true squarified layout, which
is unnecessary complexity for a v1 concept blueprint.

PUBLIC/PRIVATE ZONE GROUPING (added after real user feedback: "no sense of
living room being in the middle" - a living room could land dead-center of
the plot purely by coincidence of the weight-balanced split, since the
original algorithm had zero notion of real-world room-placement conventions).
Fix: `_slice()` itself is UNCHANGED, still pure area-weighted recursive
splitting - only the ORDER of the room list fed into it changed.
`_slice()` always splits a CONTIGUOUS sublist into two halves, so the input
order directly determines which rooms end up spatially grouped together.
`layout_floor()` now does one STABLE sort (Python's `sorted()` preserves
relative order within equal keys) grouping every room into a "public zone"
(garage, entry/foyer, living/lounge/family/drawing, dining, kitchen, powder/
guest bath, study/office - the keyword list mirrors blueprint_svg.py's own
furniture-dispatch vocabulary for consistency) ahead of everything else (the
"private zone" - bedrooms, bathrooms, hallways, storage, utility, anything
unrecognized). This guarantees public rooms cluster together and private
rooms cluster together as two contiguous regions of the plot, without
disturbing whatever fine-grained order Gemini/fallback already gave rooms
WITHIN a zone (e.g. a "Master Bedroom" immediately followed by "Master
Bathroom" in the input list stays adjacent - only the coarse public-vs-
private split is enforced, not a full per-room adjacency solver, which is a
much harder problem deliberately still out of scope - see CLAUDE.md). This
contiguity guarantee is PURELY structural (recursively splitting a
contiguous range always yields two still-contiguous sub-ranges) and holds
regardless of the specific weight VALUES used at each split - so the
minimum-area guarantee added below doesn't disturb it.

MINIMUM-AREA GUARANTEE (added 2026-08-27, part of the same pass that added
app/pipeline/feasibility.py's hard feasibility gate): Gemini's "area" field
is only a RELATIVE WEIGHT, with no floor - a room could previously shrink to
an unusable sliver purely because the LLM guessed a low weight relative to
many other rooms in the same floor, independent of whether that room type
has a real-world minimum usable size. `layout_floor()` now first reserves
each room's real minimum area (app/pipeline/room_specs.py, keyed by a
lightweight room-type classifier - bedroom/bathroom/kitchen/etc. each have a
real minimum footprint, a garage's minimum additionally scales with
requested car count), THEN distributes whatever area remains across the
plot proportionally to Gemini's relative weights - so a weight only ever
controls how much space a room gets ABOVE its guaranteed usable minimum,
never whether it gets one at all. When the sum of every room's minimum
exceeds the plot's actual area, this falls back to the original pure-weight
behavior instead of computing a nonsensical negative remainder - that case
is expected to have already been caught by feasibility.check_feasibility()
BEFORE layout_floor() is ever called (see generate_house.py's hard gate),
so this fallback is a defensive backstop, not the normal path.

REAL CIRCULATION CORRIDOR (2026-09-02, real user feedback: "I don't want
crisp, I want an intelligent one which doesn't just make boxes and lines but
adds some true meaning to the map"). Diagnosis: the AI Concept Layout model
can't add this "meaning" itself - it's forced to trace our own geometry
almost exactly (see kaggle_autocad.py's controlnet_conditioning_scale), so
the fix has to be here, in the deterministic engine. Before this, every
private-zone room just touched its neighbor directly (bedroom-to-bedroom
doors, purely an artifact of the slice-and-dice split, not a real design
choice). Now: the top-level public/circulation-vs-private split (previously
just an ordering trick within one big recursive `_slice()` call - zones were
never actually separate boxes) becomes a REAL one-time box split
(`_split_box()`), giving the private zone its own real sub-box. When that
box holds at least MIN_ROOMS_FOR_CORRIDOR rooms and has room to spare, a
real hallway strip (HALLWAY_WIDTH_M) is reserved along whichever edge
borders the public/circulation zone (so it's actually reachable from there),
and the remaining rooms are packed in their EXISTING input order along the
corridor via `_pack_row()` - preserving the previously-established "master
bedroom next to its own ensuite bathroom" adjacency automatically, with no
separate suite-detection logic needed, since sequential packing keeps
sequential neighbors adjacent to EACH OTHER as well as to the corridor.
blueprint_svg.py then suppresses the direct door between two adjacent
non-bathroom private rooms whenever a hallway exists, so bedrooms open onto
the hallway instead of into each other. Explicitly NOT attempted here (real,
harder problems, left for later - see future-plans/house-layout-spec-checklist.md):
non-rectangular/L-shaped rooms, a double-loaded (two-facing-rows) corridor,
plumbing-zone vertical stacking across floors, a full adjacency-graph
solver. Small private zones (fewer than MIN_ROOMS_FOR_CORRIDOR rooms, or not
enough depth left after reserving the corridor) fall back unchanged to the
plain `_slice()` behavior that existed before this - confirmed by the
pre-existing test_layout_floor_preserves_relative_order_within_a_zone test
(only 2 private rooms) continuing to pass untouched.
"""

from app.pipeline.room_specs import min_area_for_room, to_plot_unit

# A real, standard single-loaded corridor width - reserved along whichever
# edge of the private zone's box borders the public/circulation zone, so the
# hallway is actually reachable from there, not just present.
HALLWAY_WIDTH_M = 1.1

# Below this many private rooms, a corridor adds overhead without much real
# benefit (a 1-2 room private zone can just connect directly) - falls back to
# the original plain _slice() behavior instead.
MIN_ROOMS_FOR_CORRIDOR = 3

# After reserving the hallway's width, the remaining row depth must still fit
# a usable room - a conservative fixed floor (not tied to any one room type's
# own minimum, since the row holds a mix of room types).
MIN_ROW_DEPTH_M = 2.0

MIN_WEIGHT = 0.01

# Same category vocabulary as blueprint_svg.py's furniture dispatcher, reused
# here for consistency - a room matching any of these keywords is "public"
# (front-of-house / shared spaces); everything else (bedrooms, bathrooms,
# hallways, storage, utility, unrecognized names) is "private".
_PUBLIC_ZONE_KEYWORDS = (
    "garage",
    "entry", "foyer", "lobby",
    "living", "lounge", "family", "drawing",
    "dining",
    "kitchen",
    "powder", "guest bath", "guest wc", "guest toilet",
    "study", "office",
)

# A THIRD zone, added 2026-08-29 alongside the real staircase-as-a-room
# feature (see generate_house.py's per-floor injection): a staircase sits
# BETWEEN public and private in the sort order on every floor, not lumped
# into either. Since _slice() always splits a CONTIGUOUS sublist (see the
# public/private docstring above), sorting every floor's rooms the same way
# - public, then staircase, then private - means the staircase lands in a
# consistent RELATIVE region of the plot across floors even though nothing
# here can guarantee pixel-exact interior alignment (this project's
# rectangular slice-and-dice algorithm can't reserve an arbitrary interior
# rectangle at the exact same coordinates across floors without a
# fundamentally different, non-rectangular-region layout algorithm - a real,
# stated limitation, not silently glossed over). This is what "multi-floor
# coordination" means in this codebase today: consistent zone-relative
# placement + consistent guaranteed sizing (see room_specs.py's "staircase"
# entry), not pixel-identical positioning. Exterior wall alignment across
# floors is already exact for a different, simpler reason - every floor
# renders the identical plot boundary at the identical canvas position.
_CIRCULATION_ZONE_KEYWORDS = ("stair",)


def _zone_key(room_name: str) -> int:
    name = room_name.lower()
    if any(keyword in name for keyword in _PUBLIC_ZONE_KEYWORDS):
        return 0
    if any(keyword in name for keyword in _CIRCULATION_ZONE_KEYWORDS):
        return 1
    return 2


def layout_floor(rooms: list[dict], dimensions: dict, garage_cars: int | None = None) -> list[dict]:
    """rooms: [{"name": str, "area": number}, ...] (area is a relative weight).
    dimensions: {"length": float, "width": float, "unit": str}. garage_cars,
    when given, sizes any room classified as a garage using the real
    per-car minimum instead of the single-car default - see
    room_specs.garage_min_area_sqm().

    Returns [{"name": str, "x": float, "y": float, "w": float, "h": float}, ...]
    in the same unit as dimensions, tiling exactly [0, length] x [0, width].
    """
    if not rooms:
        return []

    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"

    # Stable sort into public-then-private zones (see module docstring) -
    # preserves each zone's own internal relative order.
    rooms = sorted(rooms, key=lambda r: _zone_key(str(r.get("name") or "")))

    names = [str(r.get("name") or f"Room {i + 1}") for i, r in enumerate(rooms)]
    raw_weights = [max(float(r.get("area") or 0), MIN_WEIGHT) for r in rooms]

    total_area = length * width
    min_areas = [min_area_for_room(name, unit, garage_cars) for name in names]
    sum_min = sum(min_areas)

    if 0 < sum_min <= total_area:
        # Guarantee every room at least its real-world minimum area (see
        # module docstring's MINIMUM-AREA GUARANTEE section), then distribute
        # whatever area remains proportionally to Gemini's relative weights.
        remaining_area = total_area - sum_min
        weight_sum = sum(raw_weights)
        weights = [
            min_areas[i]
            + remaining_area * (raw_weights[i] / weight_sum if weight_sum else 1 / len(raw_weights))
            for i in range(len(names))
        ]
    else:
        # Plot smaller than the guaranteed minimums - expected to already
        # have been caught by feasibility.check_feasibility() before this is
        # called. Defensive fallback to the original pure-weight behavior
        # rather than a nonsensical negative remainder.
        weights = raw_weights

    # Real circulation corridor (see module docstring) - find where the
    # private zone starts in the (already zone-sorted) list. If there's no
    # real public/circulation-vs-private boundary (everything is one zone,
    # or there are no private rooms at all), there's nothing to split
    # specially - same single recursive _slice() call as before.
    private_start = next((i for i, name in enumerate(names) if _zone_key(name) == 2), len(names))
    if private_start == 0 or private_start == len(names):
        return _slice(names, weights, 0.0, 0.0, length, width, sum(weights))

    front_names, private_names = names[:private_start], names[private_start:]
    front_weights, private_weights = weights[:private_start], weights[private_start:]
    front_total, private_total = sum(front_weights), sum(private_weights)
    left_fraction = front_total / (front_total + private_total) if (front_total + private_total) else 0.5

    front_box, private_box, split_along_width = _split_box(0.0, 0.0, length, width, left_fraction)
    front_rects = _slice(front_names, front_weights, *front_box, front_total)
    private_rects = _layout_private_zone(
        private_names, private_weights, *private_box, unit, split_along_width
    )
    return front_rects + private_rects


def _split_box(
    x: float, y: float, w: float, h: float, left_fraction: float
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float], bool]:
    """Splits one box into two along its LONGER side (same convention as
    _slice()'s own inline splitting, factored out here so it can also be
    called once, manually, for the real public/circulation-vs-private zone
    boundary in layout_floor()). Returns (left_box, right_box,
    split_along_width) - each box is (x, y, w, h); split_along_width tells
    the caller which axis was used, since the "right" box's shared edge with
    the "left" box is always its own left edge (if split along width) or its
    own top edge (if split along height) - this is what
    _layout_private_zone() uses to know which edge of its own box actually
    borders the front zone."""
    if w >= h:
        left_w = w * left_fraction
        return (x, y, left_w, h), (x + left_w, y, w - left_w, h), True
    else:
        left_h = h * left_fraction
        return (x, y, w, left_h), (x, y + left_h, w, h - left_h), False


def _slice(
    names: list[str], weights: list[float], x: float, y: float, w: float, h: float, total_weight: float
) -> list[dict]:
    if len(names) == 1:
        return [{"name": names[0], "x": x, "y": y, "w": w, "h": h}]

    split_index = _balanced_split_index(weights)
    left_names, right_names = names[:split_index], names[split_index:]
    left_weights, right_weights = weights[:split_index], weights[split_index:]
    left_total = sum(left_weights)
    right_total = sum(right_weights)
    left_fraction = left_total / total_weight if total_weight else 0.5

    left_box, right_box, _ = _split_box(x, y, w, h, left_fraction)
    return _slice(left_names, left_weights, *left_box, left_total) + _slice(
        right_names, right_weights, *right_box, right_total
    )


def _layout_private_zone(
    names: list[str],
    weights: list[float],
    x: float,
    y: float,
    w: float,
    h: float,
    unit: str,
    split_along_width: bool,
) -> list[dict]:
    """Lays out the private zone's own box - either with a real hallway
    corridor (see module docstring) when there's enough room count/space to
    justify one, or falling back to the original plain _slice() otherwise
    (small private zones, e.g. a single bedroom+bathroom, keep direct
    adjacency - there's no benefit to a corridor there, and this is what
    keeps the pre-existing "master bedroom next to its own ensuite bathroom"
    test passing unchanged).

    split_along_width tells us which edge of THIS box borders the
    public/circulation zone (see _split_box()'s docstring) - the hallway is
    reserved along that same edge so it's actually reachable from there, not
    just present somewhere in the private zone.
    """
    hallway_width = to_plot_unit(HALLWAY_WIDTH_M, unit)
    min_row_depth = to_plot_unit(MIN_ROW_DEPTH_M, unit)
    # The row's depth is the box's dimension PERPENDICULAR to the corridor's
    # run direction - that's h when the corridor runs along the width axis
    # (split_along_width True, box is wide/short relative to the front zone)
    # and w when it runs along the height axis.
    cross_dim = h if split_along_width else w

    if len(names) < MIN_ROOMS_FOR_CORRIDOR or cross_dim - hallway_width < min_row_depth:
        return _slice(names, weights, x, y, w, h, sum(weights))

    total_weight = sum(weights)
    if split_along_width:
        # Front zone is to the LEFT (private box's own left edge borders it)
        # - hallway is a vertical strip along that left edge, rooms stack
        # vertically (packed along y) to its right.
        hallway_rect = {"name": "Hallway", "x": x, "y": y, "w": hallway_width, "h": h}
        room_rects = _pack_row(
            names, weights, x + hallway_width, y, w - hallway_width, h, total_weight, along_width=False
        )
    else:
        # Front zone is ABOVE (private box's own top edge borders it) -
        # hallway is a horizontal strip along that top edge, rooms line up
        # horizontally (packed along x) below it.
        hallway_rect = {"name": "Hallway", "x": x, "y": y, "w": w, "h": hallway_width}
        room_rects = _pack_row(
            names, weights, x, y + hallway_width, w, h - hallway_width, total_weight, along_width=True
        )

    return [hallway_rect] + room_rects


def _pack_row(
    names: list[str],
    weights: list[float],
    x: float,
    y: float,
    w: float,
    h: float,
    total_weight: float,
    along_width: bool,
) -> list[dict]:
    """Packs rooms SEQUENTIALLY, in their existing input order, along one
    fixed axis (along_width: cut along x, each room gets the full h; else
    cut along y, each room gets the full w) - unlike the general _slice(),
    this never flips axis based on aspect ratio, since a corridor row's
    orientation is fixed by definition. Preserving input order (rather than
    _slice()'s own weight-balanced recursive splitting) is what keeps
    sequential neighbors (e.g. a master bedroom immediately followed by its
    own ensuite bathroom) adjacent to each other, same as the pre-existing
    zone-grouping guarantee elsewhere in this module."""
    if not names:
        return []
    rects = []
    pos = x if along_width else y
    far_edge = (x + w) if along_width else (y + h)
    for i, (name, weight) in enumerate(zip(names, weights)):
        # Last room snaps to the exact far edge instead of a proportionally-
        # computed span, so floating-point rounding across many rooms can
        # never leave a gap or overlap at the boundary - same "exact tiling
        # by construction" guarantee _slice()'s own recursive halving has.
        if i == len(names) - 1:
            span = far_edge - pos
        else:
            span = (w if along_width else h) * (weight / total_weight if total_weight else 1 / len(names))
        if along_width:
            rects.append({"name": name, "x": pos, "y": y, "w": span, "h": h})
        else:
            rects.append({"name": name, "x": x, "y": pos, "w": w, "h": span})
        pos += span
    return rects


def _balanced_split_index(weights: list[float]) -> int:
    """Return the split index (1..len-1) whose left-side weight sum is closest
    to half of the total - keeps the two resulting groups as evenly sized as
    possible regardless of room order."""
    total = sum(weights)
    half = total / 2
    running = 0.0
    best_index = 1
    best_diff = None
    for i in range(1, len(weights)):
        running = sum(weights[:i])
        diff = abs(running - half)
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best_index = i
    return best_index
