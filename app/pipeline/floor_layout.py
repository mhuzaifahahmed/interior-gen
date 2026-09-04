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
and the remaining rooms are packed along the corridor via `_pack_row()`.

REAL BEDROOM+BATHROOM SUITES (2026-09-03, real user feedback: "master
washroom has to be attached with master bedroom... all the washrooms are on
a side and all the bedrooms are on the side with a hallway in between - it
doesn't make sense"). Before this, the corridor packed private rooms in
Gemini's RAW list order, so if the model returned all bedrooms first and all
bathrooms last, they clustered on opposite ends - a "master bathroom" could
end up nowhere near the master bedroom. `_arrange_suites()` now reorders the
private list into real ensuite pairs ([MasterBed, MasterBath, Bed2, Bath2,
...]) and tags each pair with a shared `suite` id carried into the packed
rects. blueprint_svg.py's `_should_suppress_direct_door()` uses that tag to
draw a door ONLY between a bathroom and its OWN bedroom (never into a
neighbor it merely got packed beside), keeps an ensuite bathroom from also
opening onto the hallway, and still sends every bedroom and every
common/standalone bathroom onto the hallway. FRONT-OF-HOUSE ORDERING was
added in the same pass (`_public_order_key`): the public zone's rooms are
ordered garage/entry -> living -> dining -> study -> kitchen so the
entrance/garage cluster lands together at the "main side" and the kitchen
sits next to the private zone's hallway as the transition, instead of
scattering by whatever order Gemini returned. Small private zones (fewer
than MIN_ROOMS_FOR_CORRIDOR rooms, or not enough depth left after reserving
the corridor) fall back unchanged to the plain `_slice()` behavior that
existed before this - confirmed by the pre-existing
test_layout_floor_preserves_relative_order_within_a_zone test (only 2
private rooms) continuing to pass untouched.

TRUE FRONT-TO-BACK ZONING + REAL MIN/MAX ROOM PROPORTIONS + KITCHEN-DINING
ADJACENCY (2026-09-04, real user critique of a live layout: garage sat in a
disruptive central position instead of the front, the dining room was
oversized relative to its function, and kitchen/dining/living didn't read
as a coherent zone). Three changes:
  1. `layout_floor()`'s ONE top-level public-vs-private split now ALWAYS
     cuts along the plot's y-axis (`_split_box_along_y()`, public in the
     low-y "front" band, private in the high-y "back" band) instead of
     `_split_box()`'s "cut whichever side is longer" rule - this project
     already treats the plot's y=0 edge as the road-facing front everywhere
     else (house_requirements.py's front-yard convention reserves depth
     along that same edge), so this makes the layout engine consistent with
     its own established front-facing convention on every plot, regardless
     of aspect ratio. Every NESTED split (`_slice()` within each zone, the
     hallway placement in `_layout_private_zone()`) is unaffected.
  2. `ROOM_MAX_MULTIPLIER` (room_specs.py) caps how far a room can grow
     ABOVE its guaranteed minimum before the excess area is redistributed to
     other rooms that haven't hit their own cap yet (`_clamp_to_max_and_
     redistribute()`) - previously a room's area above its minimum was
     driven by Gemini's relative weight alone, unbounded, which is exactly
     why a heavily-weighted dining room could balloon. `garage`/`staircase`
     are pinned to a 1.0 multiplier (exactly their functional minimum,
     never grown by leftover weight) since their size is a real requirement
     (vehicle clearance / a stair run), not a preference.
  3. `_PUBLIC_ORDER_RANKS` moved `dining` to immediately precede `kitchen`
     (previously separated by `study`/`living`, so they were never even
     list-adjacent) - kitchen still ranks last (closest to the hallway
     transition, unchanged intent), with dining now beside it.

Explicitly NOT attempted (real, harder problems, left for later - see
future-plans/house-layout-spec-checklist.md): non-rectangular/L-shaped
rooms, a double-loaded (two-facing-rows) corridor, plumbing-zone vertical
stacking across floors, a full adjacency-graph solver, a real validate-
then-regenerate loop, true road-facing orientation (this project has no
plot-orientation input at all - the y=0-is-front convention is a project-
wide assumption, not a verified fact), and zone-level (public-vs-private)
area capping (the max-area caps above are intra-zone only - an unusually
weight-heavy public room list could still claim a disproportionate share of
the total footprint before the private zone's own split).
"""

from app.pipeline.room_specs import (
    classify_room_category,
    garage_dimensions,
    max_area_for_room,
    min_area_for_room,
    to_plot_unit,
)

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


# Front-of-house ordering WITHIN the public zone (2026-09-03, real user
# feedback: "garage and entrances from the main side and first it is the
# living room and then kitchen and then rooms"). The public zone is one
# contiguous sliced region (see module docstring); ordering its rooms by
# this real-world convention - entrance cluster first, then the social
# rooms, then the kitchen nearest the private zone as the transition - makes
# the garage/entry land together at one end and the kitchen sit next to the
# bedrooms' hallway, instead of scattering them by whatever order Gemini
# happened to return. A STABLE secondary sort key: rooms with the same rank
# keep Gemini's own relative order.
#
# 2026-09-04: dining and kitchen moved to ADJACENT ranks (previously
# dining=2/kitchen=4, separated by study - not even list-adjacent, so the
# "list order -> likely spatial adjacency" mechanism this whole scheme
# relies on never applied to them). Real user critique: "the kitchen/dining/
# living relationship is not sufficiently coherent." Kitchen still stays
# LAST among these ranks (closest to the private zone's hallway, unchanged
# intent) with dining now immediately before it, so the two are list-
# adjacent, not living any longer.
_PUBLIC_ORDER_RANKS = (
    ("garage", "entry", "foyer", "lobby"),            # entrance / vehicle side (the "main side")
    ("living", "lounge", "family", "drawing"),         # social core
    ("study", "office"),                                # work, quieter end of public
    ("dining",),                                        # dining, immediately beside the kitchen
    ("kitchen",),                                       # kitchen last - the transition toward the private zone
    ("powder", "guest bath", "guest wc", "guest toilet"),
)


def _public_order_key(room_name: str) -> int:
    name = room_name.lower()
    for rank, keywords in enumerate(_PUBLIC_ORDER_RANKS):
        if any(keyword in name for keyword in keywords):
            return rank
    return len(_PUBLIC_ORDER_RANKS)


def _sort_key(room_name: str) -> tuple[int, int]:
    """Primary key = zone (public 0 / circulation 1 / private 2). Secondary
    key orders WITHIN the public zone by front-of-house convention (see
    _public_order_key); circulation/private rooms all share secondary 0, so
    their own incoming relative order is preserved by the stable sort."""
    zone = _zone_key(room_name)
    return (zone, _public_order_key(room_name) if zone == 0 else 0)


_VALID_FACINGS = ("north", "south", "east", "west")


def layout_floor(
    rooms: list[dict], dimensions: dict, garage_cars: int | None = None, facing: str | None = None
) -> list[dict]:
    """rooms: [{"name": str, "area": number}, ...] (area is a relative weight).
    dimensions: {"length": float, "width": float, "unit": str}. garage_cars,
    when given, sizes any room classified as a garage using the real
    per-car minimum instead of the single-car default - see
    room_specs.garage_min_area_sqm().

    facing: which compass direction the entrance/public zone faces -
    "north"/"south"/"east"/"west" (case-insensitive), defaulting to "north"
    for anything else (None, empty, unrecognized). This is this FUNCTION's
    own neutral library default, unrelated to the PRODUCT default of "south"
    when a user doesn't choose one on the form (2026-09-04) - that "south"
    default is resolved one layer up, in generate_house.py, which then
    passes an explicit facing="south" here. "north" here just keeps every
    existing direct caller/test (none of which pass `facing` at all)
    behaving exactly as before this parameter was added: front at low-y,
    matching the decorative north arrow blueprint_svg.py already draws
    pointing up.

    The core algorithm below is UNCHANGED and always computes with front at
    low internal-y (the pre-existing behavior) - `facing` is implemented as
    a coordinate TRANSFORM applied to the result, not by threading direction
    through the recursive splitting/corridor/suite logic. For north, that
    transform is a no-op. For south, the whole result is mirrored along y
    (front moves from low-y to high-y). For east/west, the algorithm is run
    on dimensions with length/width SWAPPED (so its own low-y front band
    ends up spanning the real x-axis once transposed back), then transposed
    (x<->y, w<->h) and, for east, additionally mirrored along x. This keeps
    every nested piece of this module (`_slice()`, `_layout_private_zone()`,
    `_pack_row()`, the suite/hallway/door logic in blueprint_svg.py) working
    in one single, unchanged coordinate convention - only the very top-level
    wrapper needs to know about compass directions at all.

    Returns [{"name": str, "x": float, "y": float, "w": float, "h": float}, ...]
    in the same unit as dimensions, tiling exactly [0, length] x [0, width]
    (the REAL, un-swapped dimensions - the length/width swap above is purely
    an internal computation detail, invisible to the caller).
    """
    if not rooms:
        return []

    facing = (facing or "north").strip().lower()
    if facing not in _VALID_FACINGS:
        facing = "north"

    real_length = float(dimensions.get("length") or 1)
    real_width = float(dimensions.get("width") or 1)

    if facing in ("east", "west"):
        core_dimensions = {
            "length": real_width,
            "width": real_length,
            "unit": dimensions.get("unit"),
        }
    else:
        core_dimensions = dimensions

    rects = _layout_floor_core(rooms, core_dimensions, garage_cars)

    if facing in ("east", "west"):
        # Transpose back to real coordinates - the core algorithm's own x
        # (spanning [0, real_width]) becomes the real y, and its own y
        # (spanning [0, real_length], front at its low end) becomes the
        # real x, so front now sits at low real-x (west).
        rects = [{**r, "x": r["y"], "y": r["x"], "w": r["h"], "h": r["w"]} for r in rects]

    if facing == "south":
        rects = [{**r, "y": real_width - r["y"] - r["h"]} for r in rects]
    elif facing == "east":
        rects = [{**r, "x": real_length - r["x"] - r["w"]} for r in rects]

    return rects


def _layout_floor_core(rooms: list[dict], dimensions: dict, garage_cars: int | None = None) -> list[dict]:
    """The actual layout algorithm - always computes with the public/front
    zone at low-y, private/back zone at high-y (see layout_floor()'s
    docstring for how compass facing is applied as a coordinate transform
    around this function, not inside it)."""
    length = float(dimensions.get("length") or 1)
    width = float(dimensions.get("width") or 1)
    unit = dimensions.get("unit") or "ft"

    # Stable sort into public-then-circulation-then-private zones (see module
    # docstring), ordering rooms WITHIN the public zone by front-of-house
    # convention (see _sort_key) - preserves each zone's own internal
    # relative order for circulation/private rooms.
    rooms = sorted(rooms, key=lambda r: _sort_key(str(r.get("name") or "")))

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
        # ROOM PROPORTION CAP (2026-09-04, see module docstring's "REAL
        # MIN/MAX ROOM PROPORTIONS" section) - clamp each room's area to its
        # own real-world maximum and give the excess to whichever rooms
        # haven't hit their cap yet, so one heavily-weighted room (e.g.
        # dining) can no longer balloon just because Gemini assigned it a
        # large relative weight.
        max_areas = [max_area_for_room(name, unit, garage_cars) for name in names]
        # PINNED rooms (garage/staircase, ROOM_MAX_MULTIPLIER == 1.0) must
        # never absorb redistributed excess - their size is a real
        # functional requirement (vehicle clearance / a stair run), not a
        # preference, and letting them balloon past their minimum would
        # produce an unusable shape (e.g. a stair run stretched into a thin
        # sliver) purely as a side effect of OTHER rooms hitting their own
        # caps. See _clamp_to_max_and_redistribute()'s docstring for exactly
        # how this changes which rooms are eligible to receive it.
        pinned = [classify_room_category(name) in ("garage", "staircase") for name in names]
        weights = _clamp_to_max_and_redistribute(weights, max_areas, pinned)
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
        return _slice_reserving_garage(names, weights, 0.0, 0.0, length, width, sum(weights), unit, garage_cars)

    front_names, private_names = names[:private_start], names[private_start:]
    front_weights, private_weights = weights[:private_start], weights[private_start:]
    front_total, private_total = sum(front_weights), sum(private_weights)
    front_fraction = front_total / (front_total + private_total) if (front_total + private_total) else 0.5

    # TRUE FRONT-TO-BACK ZONING (2026-09-04, see module docstring) - this ONE
    # top-level public-vs-private split always cuts along the plot's y-axis
    # (front/low-y for public, back/high-y for private), regardless of which
    # side of the box is longer. Every NESTED split (_slice() within each
    # zone, the hallway placement in _layout_private_zone()) is unaffected -
    # they still pick whichever axis suits that sub-region.
    front_box, private_box, split_along_width = _split_box_along_y(0.0, 0.0, length, width, front_fraction)
    front_rects = _slice_reserving_garage(front_names, front_weights, *front_box, front_total, unit, garage_cars)
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


def _split_box_along_y(
    x: float, y: float, w: float, h: float, front_fraction: float
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float], bool]:
    """Splits one box along its HEIGHT axis ALWAYS (front/low-y band first,
    back/high-y band second), regardless of which side is longer - unlike
    _split_box()'s "always cut the longer side" rule. Used ONLY for
    layout_floor()'s single top-level public-vs-private split (see module
    docstring's "TRUE FRONT-TO-BACK ZONING" section) - this project already
    treats the plot's y=0 edge as the road-facing front everywhere else
    (house_requirements.py's front-yard convention), so orienting public/
    private front-to-back along that same axis keeps every part of this
    codebase consistent about which edge is "the front."

    Returns (front_box, private_box, split_along_width) in the exact same
    shape _split_box() returns, so _layout_private_zone() needs no changes -
    a y-axis split always sets split_along_width=False (this is literally
    _split_box()'s own w<h branch, just taken unconditionally instead of
    only when h happens to be the shorter side)."""
    front_h = h * front_fraction
    return (x, y, w, front_h), (x, y + front_h, w, h - front_h), False


def _clamp_to_max_and_redistribute(
    weights: list[float], max_areas: list[float], pinned: list[bool]
) -> list[float]:
    """Clamps each room's computed area to its own real-world maximum (see
    room_specs.ROOM_MAX_MULTIPLIER), then gives the excess taken from any
    clamped room to whichever rooms can still absorb it - preferring
    non-pinned rooms that haven't hit their own cap yet, split
    proportionally to their current share of that group.

    `pinned` (parallel to weights/max_areas) marks rooms whose size is a
    real functional requirement, not a preference (garage/staircase,
    ROOM_MAX_MULTIPLIER == 1.0 - see the caller). Pinned rooms are eligible
    to GIVE UP excess (they're still clamped to their cap above) but are
    NEVER a redistribution TARGET, at any tier below - a pinned room must
    never be pushed larger than its own functional minimum just because
    other rooms in the same zone also hit their caps.

    Three-tier fallback for WHERE the excess goes, each only used if the
    previous tier has no eligible room:
      1. Non-pinned rooms that haven't hit their own cap yet (the common
         case - e.g. a capped dining room's excess flows to an uncapped
         living room).
      2. Non-pinned rooms even if they've ALSO hit their cap (better to let
         a flexible room type - living, bedroom, kitchen... - modestly
         exceed its own cap than to distort a pinned room's shape).
      3. Every room, pinned or not (only reachable when a zone consists
         ENTIRELY of pinned rooms, e.g. a "floor" that's just a garage and a
         staircase - genuinely has nowhere non-pinned to put the excess).

    Deliberately a SINGLE redistribution pass, not an iterative solver: a
    tier-1 room boosted by this pass could in principle end up exceeding ITS
    OWN cap too, in which case the small residual overflow is simply
    accepted rather than clamped again - consistent with this module's
    existing "not a full constraint solver" honesty elsewhere (see the
    MINIMUM-AREA GUARANTEE section above)."""
    weights = list(weights)
    excess = 0.0
    capped = [False] * len(weights)
    for i, (w, cap) in enumerate(zip(weights, max_areas)):
        if w > cap:
            excess += w - cap
            weights[i] = cap
            capped[i] = True

    if excess <= 0:
        return weights

    receivers = [i for i in range(len(weights)) if not pinned[i] and not capped[i]]
    if not receivers:
        receivers = [i for i in range(len(weights)) if not pinned[i]]
    if not receivers:
        # Every room in this zone is pinned - genuinely nowhere non-pinned
        # to put the excess. Since _slice() only ever uses these numbers as
        # RATIOS (it always tiles 100% of whatever box it's given), leaving
        # them under-summed here doesn't waste area - _slice() proportionally
        # rescales everything up to fill the box, preserving the RELATIVE
        # relationship between the capped values. Accepted as a rare,
        # pathological edge case rather than engineered around further.
        return weights

    receiver_total = sum(weights[i] for i in receivers)
    for i in receivers:
        share = weights[i] / receiver_total if receiver_total else 1 / len(receivers)
        weights[i] += excess * share

    return weights


def _slice_reserving_garage(
    names: list[str],
    weights: list[float],
    x: float,
    y: float,
    w: float,
    h: float,
    total_weight: float,
    unit: str,
    garage_cars: int | None,
) -> list[dict]:
    """Same job as _slice() below, but when a garage is among `names`, its
    rectangle is carved out FIRST as a real, correctly-proportioned vertical
    strip (exact real width for the requested car count, spanning the box's
    FULL height) instead of emerging from the general area-only weighted
    split - see room_specs.garage_dimensions()'s docstring for the real bug
    this fixes (a garage could previously come out too NARROW for a car to
    actually fit, despite having the "correct" total area, since the general
    algorithm only ever guaranteed area, never width specifically).

    This does mean a garage can end up DEEPER than its bare minimum (the
    strip's height is the box's full height, usually more than the 5.4m a
    car alone needs) - a deliberate, documented trade-off: guaranteeing a
    real, usable width takes priority over exact minimality for the one room
    type where a wrong shape (not just a slightly-too-small area) makes it
    genuinely non-functional. Every other room's sizing/redistribution logic
    upstream of this call is completely unaffected - garage's weight/cap
    still influenced how much area the REST of the zone had available, this
    only changes garage's own final SHAPE.

    Falls back to the plain, unchanged _slice() (no garage carve-out) when
    there's no garage in `names`, or when the box is too narrow for the
    carve-out to make geometric sense (defensive - expected to already be
    caught by feasibility checks, not the normal path)."""
    garage_index = next((i for i, name in enumerate(names) if classify_room_category(name) == "garage"), None)
    if garage_index is None:
        return _slice(names, weights, x, y, w, h, total_weight)

    garage_width, _garage_depth = garage_dimensions(garage_cars or 1, unit)
    if garage_width <= 0 or garage_width >= w:
        # Box too narrow for a real carve-out to leave any room for the rest
        # of the zone - defensive fallback, not the normal path (see
        # docstring).
        return _slice(names, weights, x, y, w, h, total_weight)

    garage_name = names[garage_index]
    garage_rect = {"name": garage_name, "x": x, "y": y, "w": garage_width, "h": h}

    rest_names = names[:garage_index] + names[garage_index + 1 :]
    rest_weights = weights[:garage_index] + weights[garage_index + 1 :]
    if not rest_names:
        return [garage_rect]

    rest_total = sum(rest_weights)
    rest_rects = _slice(rest_names, rest_weights, x + garage_width, y, w - garage_width, h, rest_total)
    return [garage_rect] + rest_rects


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


def _is_master(name: str) -> bool:
    return "master" in (name or "").lower()


def _arrange_suites(
    names: list[str], weights: list[float]
) -> tuple[list[str], list[float], list[int | None]]:
    """Reorders the private-zone room list into real bedroom+bathroom SUITES
    before it's packed along the corridor, and tags each paired room with a
    shared suite id. Real user feedback that drove this (2026-09-03): a
    "master washroom" that isn't adjacent to the master bedroom is
    architecturally nonsensical, and the previous behavior clustered all
    bathrooms together and all bedrooms together (an artifact of packing
    rooms in Gemini's raw list order, which had no bed<->bath pairing at
    all). This pairs them explicitly:

      1. A master bedroom is paired FIRST, preferring a bathroom whose own
         name says "master"/"ensuite", else the next available bathroom.
      2. Every remaining bedroom pairs with the next available bathroom, in
         order, until bathrooms run out.
      3. Leftover bathrooms (a common/shared bath with no bedroom of its
         own) and every non-bed/bath room (storage, closet, utility...) stay
         standalone (suite id None) and go at the END of the row.

    The returned order interleaves each bedroom immediately followed by its
    paired bathroom ([MasterBed, MasterBath, Bed2, Bath2, Bed3, ...]) so
    _pack_row() keeps each pair physically adjacent, and the shared suite id
    lets blueprint_svg._should_suppress_direct_door() draw a door ONLY
    between a bathroom and its own bedroom - never into an unrelated
    neighbor. Purely a reordering + tagging step: it never changes the set
    of rooms or the sum of weights, so exact plot tiling is unaffected.
    """
    bedrooms = [i for i, n in enumerate(names) if classify_room_category(n) == "bedroom"]
    bathrooms = [i for i, n in enumerate(names) if classify_room_category(n) == "bathroom"]

    # Masters first, otherwise preserve the incoming relative order.
    bedrooms.sort(key=lambda i: 0 if _is_master(names[i]) else 1)
    available_baths = list(bathrooms)  # original order

    ordered: list[tuple[int, int | None]] = []
    suite_counter = 0
    for bi in bedrooms:
        bath = None
        if _is_master(names[bi]):
            bath = next(
                (c for c in available_baths if _is_master(names[c]) or "ensuite" in names[c].lower()),
                None,
            )
        if bath is None and available_baths:
            bath = available_baths[0]

        if bath is not None:
            available_baths.remove(bath)
            ordered.append((bi, suite_counter))
            ordered.append((bath, suite_counter))
            suite_counter += 1
        else:
            ordered.append((bi, None))  # bedroom with no bath left - opens onto the hallway

    placed = {i for i, _ in ordered}
    for i in range(len(names)):
        if i not in placed:  # leftover baths (common) + storage/closet/utility/unrecognized rooms
            ordered.append((i, None))

    ordered_names = [names[i] for i, _ in ordered]
    ordered_weights = [weights[i] for i, _ in ordered]
    ordered_suites = [s for _, s in ordered]
    return ordered_names, ordered_weights, ordered_suites


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

    When a corridor IS built, rooms are first reordered into bedroom+bathroom
    suites (_arrange_suites) so each bathroom lands next to its own bedroom
    with a shared suite tag, instead of all bathrooms clustering on one side.
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

    names, weights, suite_ids = _arrange_suites(names, weights)
    total_weight = sum(weights)
    if split_along_width:
        # Front zone is to the LEFT (private box's own left edge borders it)
        # - hallway is a vertical strip along that left edge, rooms stack
        # vertically (packed along y) to its right.
        hallway_rect = {"name": "Hallway", "x": x, "y": y, "w": hallway_width, "h": h}
        room_rects = _pack_row(
            names, weights, x + hallway_width, y, w - hallway_width, h, total_weight,
            along_width=False, suite_ids=suite_ids,
        )
    else:
        # Front zone is ABOVE (private box's own top edge borders it) -
        # hallway is a horizontal strip along that top edge, rooms line up
        # horizontally (packed along x) below it.
        hallway_rect = {"name": "Hallway", "x": x, "y": y, "w": w, "h": hallway_width}
        room_rects = _pack_row(
            names, weights, x, y + hallway_width, w, h - hallway_width, total_weight,
            along_width=True, suite_ids=suite_ids,
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
    suite_ids: list[int | None] | None = None,
) -> list[dict]:
    """Packs rooms SEQUENTIALLY, in their existing input order, along one
    fixed axis (along_width: cut along x, each room gets the full h; else
    cut along y, each room gets the full w) - unlike the general _slice(),
    this never flips axis based on aspect ratio, since a corridor row's
    orientation is fixed by definition. Preserving input order (rather than
    _slice()'s own weight-balanced recursive splitting) is what keeps
    sequential neighbors (e.g. a master bedroom immediately followed by its
    own ensuite bathroom) adjacent to each other, same as the pre-existing
    zone-grouping guarantee elsewhere in this module.

    suite_ids (parallel to names, when given) tags each room's rect with a
    "suite" id so blueprint_svg._should_suppress_direct_door() can draw an
    ensuite door ONLY between a bathroom and its own bedroom - see
    _arrange_suites(). A None entry means "not part of a suite" and adds no
    tag, keeping the rect dict identical to before for standalone rooms."""
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
            rect = {"name": name, "x": pos, "y": y, "w": span, "h": h}
        else:
            rect = {"name": name, "x": x, "y": pos, "w": w, "h": span}
        if suite_ids is not None and suite_ids[i] is not None:
            rect["suite"] = suite_ids[i]
        rects.append(rect)
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
