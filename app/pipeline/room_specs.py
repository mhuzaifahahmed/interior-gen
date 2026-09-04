"""Real-world minimum/preferred room dimensions, used by
app/pipeline/floor_layout.py to guarantee every room in a generated layout is
actually usable (not an artifact of Gemini's relative-weight guess shrinking
it to near zero) and by app/pipeline/feasibility.py to check whether a
requested room program can physically fit a stated plot before any geometry
is generated.

Pure data + pure classification - no I/O, nothing to mock. Deliberately a
SEPARATE keyword classifier from blueprint_svg.py's furniture-dispatch
`_draw_furniture()` - that one decides which furniture SYMBOLS to draw
inside an already-sized rectangle; this one decides how big the rectangle
should be allowed to get in the first place. The two lists are similar
(same real-world room types) but not unified on purpose - they answer
different questions and unifying them would risk furniture-drawing
regressions for a sizing-only change.

All dimensions are specified in METERS (the unit architectural convention
this document's own room-sizing spec used) and converted to the plot's
actual unit ("ft" or "m") via to_plot_unit() before use - so a size table
edit never needs to know which unit a given house project happens to use.
"""

import math

METERS_TO_FEET = 3.280839895

# min_width/min_depth: the smallest usable real-world footprint for this
# room type - below this, the room stops being usable regardless of what
# Gemini's relative-weight guess would otherwise produce. Not a fixed/
# preferred size - see layout_floor()'s docstring for how the remaining
# plot area beyond these floors is still distributed by relative weight.
ROOM_SIZE_SPECS_M: dict[str, dict[str, float]] = {
    "bathroom": {"min_width": 1.5, "min_depth": 2.1},
    "bedroom": {"min_width": 2.7, "min_depth": 2.7},
    "kitchen": {"min_width": 2.4, "min_depth": 3.0},
    "dining": {"min_width": 2.7, "min_depth": 3.0},
    "living": {"min_width": 3.3, "min_depth": 3.6},
    "study": {"min_width": 2.4, "min_depth": 2.4},
    "garage": {"min_width": 2.7, "min_depth": 5.4},  # per-car width, see garage_min_area_sqm()
    "laundry": {"min_width": 1.8, "min_depth": 2.1},
    "closet": {"min_width": 1.2, "min_depth": 1.5},
    "foyer": {"min_width": 1.5, "min_depth": 1.8},
    # A real reserved footprint for a compact stair run + landing (2026-08-29
    # - see floor_layout.py/generate_house.py for how this room gets
    # injected). 1.2m width is a comfortable single-run clearance; 2.7m depth
    # fits either a straight run or the two shorter flights of an L-shaped
    # run - blueprint_svg.py picks which shape to draw from the room's own
    # REAL resulting aspect ratio after layout, not guessed here.
    "staircase": {"min_width": 1.2, "min_depth": 2.7},
    "default": {"min_width": 2.1, "min_depth": 2.1},
}

# MAXIMUM multiplier on a room's own minimum area (2026-09-04, real user
# critique: "the dining room occupies too much area relative to its
# function... every square meter should have a clear purpose"). Before this,
# layout_floor() guaranteed each room its minimum then handed 100% of the
# REMAINING plot area to relative weight alone, unbounded on the high end -
# a heavily-weighted room (e.g. dining) could balloon arbitrarily. This caps
# how far ABOVE its minimum a room is allowed to grow before the excess is
# redistributed elsewhere (see layout_floor()'s clamping pass) - `living`
# gets a high cap since it's meant to be the dominant everyday social space;
# `garage`/`staircase` get 1.0 (exactly their minimum) since their size is a
# real functional requirement (vehicle clearance / a stair run), not
# something that should grow just because Gemini assigned it extra weight.
ROOM_MAX_MULTIPLIER: dict[str, float] = {
    "bathroom": 1.5,
    "bedroom": 1.8,
    "kitchen": 2.2,
    "dining": 1.8,
    "living": 4.0,
    "study": 1.8,
    "garage": 1.0,
    "laundry": 1.4,
    "closet": 1.4,
    "foyer": 1.6,
    "staircase": 1.0,
    # No real cap for an UNCLASSIFIED room name - we don't know its actual
    # function, so there's no real-world size to bound it against (unlike
    # every category above, each backed by a genuine functional footprint
    # limit). math.inf is exact and self-documenting here, not a made-up
    # "big enough" number.
    "default": math.inf,
}

# Same category vocabulary/order as blueprint_svg.py's _draw_furniture()
# dispatch, for consistency - see module docstring for why it's a separate
# list rather than a shared import.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bathroom", ("bath", "wc", "washroom", "toilet")),
    ("bedroom", ("bed",)),
    ("kitchen", ("kitchen",)),
    ("dining", ("dining",)),
    ("living", ("living", "lounge", "family", "drawing")),
    ("study", ("study", "office")),
    ("garage", ("garage",)),
    ("laundry", ("laundry", "utility")),
    ("closet", ("closet", "wardrobe", "dressing")),
    ("foyer", ("foyer", "entry", "lobby")),
    ("staircase", ("stair",)),
)


def classify_room_category(room_name: str) -> str:
    """Returns one of ROOM_SIZE_SPECS_M's keys, or "default" for anything
    unrecognized - never raises, same "unrecognized name is a valid, quiet
    no-op category" treatment as blueprint_svg.py's furniture dispatch."""
    name = (room_name or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return category
    return "default"


def to_plot_unit(meters: float, unit: str) -> float:
    """Converts a meters value into the plot's stated unit ("ft" or "m",
    same values app/main.py's _compute_room_dimensions() already accepts)."""
    if (unit or "m").strip().lower().startswith("f"):
        return meters * METERS_TO_FEET
    return meters


def min_area_for_room(room_name: str, unit: str, cars: int | None = None) -> float:
    """Minimum usable area (in the plot's unit, squared) for a room, given its
    name. `cars` only matters for a garage - see garage_min_area_sqm()."""
    category = classify_room_category(room_name)
    if category == "garage":
        return garage_min_area_sqm(cars or 1, unit)
    spec = ROOM_SIZE_SPECS_M[category]
    return to_plot_unit(spec["min_width"], unit) * to_plot_unit(spec["min_depth"], unit)


def max_area_for_room(room_name: str, unit: str, cars: int | None = None) -> float:
    """Maximum sensible area (in the plot's unit, squared) for a room -
    ROOM_MAX_MULTIPLIER times its own minimum (see that constant's docstring
    for why this exists and why garage/staircase are pinned to exactly their
    minimum). Used by layout_floor() to cap how far a room can grow beyond
    its guaranteed minimum before the excess is redistributed elsewhere."""
    category = classify_room_category(room_name)
    return min_area_for_room(room_name, unit, cars) * ROOM_MAX_MULTIPLIER[category]


def garage_min_area_sqm(cars: int, unit: str) -> float:
    """A garage's minimum footprint scales with vehicle count - side-by-side
    parking bays, not one fixed size regardless of how many cars were asked
    for. Width scales per car (2.7m/car is a standard single-bay clearance);
    depth stays fixed (one car's length + door/pedestrian clearance) since
    multiple cars are assumed side-by-side, not stacked front-to-back."""
    cars = max(1, cars)
    spec = ROOM_SIZE_SPECS_M["garage"]
    width_m = spec["min_width"] * cars
    depth_m = spec["min_depth"]
    return to_plot_unit(width_m, unit) * to_plot_unit(depth_m, unit)


def garage_dimensions(cars: int, unit: str) -> tuple[float, float]:
    """Real (width, depth) - not just area - a garage needs for `cars` to
    actually fit, in the plot's own unit. Added 2026-09-04: real user
    feedback that a garage produced by area-only sizing could come out
    unusably narrow (e.g. ~6ft wide - too narrow for a real car door to even
    open) despite having the "correct" total area, since the general
    rectangle-slicing algorithm only ever guaranteed AREA, never width/depth
    individually (a known, documented limitation for every OTHER room type
    too - see floor_layout.py - but garage is the one room type where a
    wrong SHAPE, not just a small area, makes it genuinely unusable for its
    one specific function). Used by floor_layout.py to carve out a real,
    correctly-proportioned rectangle for the garage before the general
    weighted algorithm runs, instead of only ever constraining its area."""
    cars = max(1, cars)
    spec = ROOM_SIZE_SPECS_M["garage"]
    return to_plot_unit(spec["min_width"] * cars, unit), to_plot_unit(spec["min_depth"], unit)
