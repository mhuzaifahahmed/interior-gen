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
    # Bumped 2026-09-19 (real user report: "impractical", too tight for an
    # actual car) - 2.7x5.4m (8.9x17.7ft) undersold real single-garage
    # convention. 3.05x6.1m (~10x20ft) matches the commonly cited standard
    # single-car garage footprint (enough width for the car itself plus
    # real door-opening/walk-around clearance, not just the vehicle's own
    # width). Per-car width, see garage_min_area_sqm()/garage_dimensions().
    "garage": {"min_width": 3.05, "min_depth": 6.1},
    # Bumped 2026-09-19 (real user report: too cramped) - a real utility/
    # laundry room fitting a washer, dryer, sink, and some storage, not just
    # a stacked-machine closet.
    "laundry": {"min_width": 2.1, "min_depth": 2.4},
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
    # Was math.inf ("no real cap for an unclassified name") until a real,
    # live-reproduced bug (2026-09-29): a floor whose room list included a
    # generic "Hallway" (classify_room_category() has no "hallway"/
    # "corridor" keyword, so it falls to "default") let that ONE room absorb
    # effectively all of layout_floor()'s weight-based redistribution once
    # every bounded room nearby hit its own cap - the hallway ballooned to
    # roughly the size of the whole floor while real bedrooms/bathrooms were
    # squeezed into slivers. An unrecognized room still has no genuine
    # functional footprint limit to bound it against precisely, but
    # "unbounded" is strictly worse than a generous-but-finite ceiling -
    # capped at the same 4.0x multiplier `living` uses (the single highest
    # real-category cap), since a hallway/corridor/unknown space is at most
    # as dominant as the main social room, never larger than the floor
    # itself.
    "default": 4.0,
}

# Same category vocabulary/order as blueprint_svg.py's _draw_furniture()
# dispatch, for consistency - see module docstring for why it's a separate
# list rather than a shared import.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bathroom", ("bath", "wc", "washroom", "toilet")),
    ("bedroom", ("bed",)),
    ("kitchen", ("kitchen",)),
    ("dining", ("dining",)),
    # "sitting" (2026-09-30) is deliberately NOT in floor_layout.py's
    # _PUBLIC_ZONE_KEYWORDS, unlike every other keyword here - see
    # generate_house.py's _ensure_upper_floor_lounge() for why: a room
    # named "Sitting Area" still sizes/furnishes like a living room (same
    # category, same ROOM_MAX_MULTIPLIER, same blueprint_svg furniture
    # dispatch) but must NOT be treated as a ground-floor-style PUBLIC
    # room when injected on an upper floor - using a keyword that's absent
    # from the public-zone list is what keeps it out of that zone, without
    # touching "living"/"lounge"/"family"/"drawing" (still public, correct
    # for a real ground-floor room Gemini names any of those).
    ("living", ("living", "lounge", "family", "drawing", "sitting")),
    ("study", ("study", "office")),
    ("garage", ("garage",)),
    ("laundry", ("laundry", "utility")),
    ("closet", ("closet", "wardrobe", "dressing")),
    ("foyer", ("foyer", "entry", "lobby")),
    ("staircase", ("stair",)),
)


# Real user request (2026-09-19): a master bedroom should actually FEEL
# like one - noticeably bigger than a standard bedroom, not identically
# sized. Deliberately NOT a new classify_room_category() category - every
# consumer of that classifier (zoning, suite pairing, feasibility math,
# furniture dispatch) already treats "master" and regular bedrooms
# identically and correctly, and a new category would risk disturbing all
# of that for a change that's purely about SIZE. Instead, min_area_for_room()/
# max_area_for_room() apply an extra multiplier ON TOP of the regular
# "bedroom" category's own numbers whenever the room's own name says
# "master" - the room is still classified as a plain "bedroom" everywhere
# else in the codebase.
MASTER_BEDROOM_MIN_MULTIPLIER = 1.35  # guaranteed floor above a regular bedroom's own minimum
# Applied on top of the (already-bumped) minimum, not the regular bedroom
# category's own multiplier - a master bedroom's real ceiling ends up
# 1.35 * 2.4 = 3.24x a standard bedroom's raw minimum, vs a regular
# bedroom's 1.8x, so it has real room to grow larger via layout_floor()'s
# own weight-based redistribution, not just a bigger guaranteed floor.
MASTER_BEDROOM_MAX_MULTIPLIER = 2.4


def _is_master_bedroom(room_name: str) -> bool:
    return "master" in (room_name or "").lower() and classify_room_category(room_name) == "bedroom"


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
    base = to_plot_unit(spec["min_width"], unit) * to_plot_unit(spec["min_depth"], unit)
    if _is_master_bedroom(room_name):
        return base * MASTER_BEDROOM_MIN_MULTIPLIER
    return base


def max_area_for_room(room_name: str, unit: str, cars: int | None = None) -> float:
    """Maximum sensible area (in the plot's unit, squared) for a room -
    ROOM_MAX_MULTIPLIER times its own minimum (see that constant's docstring
    for why this exists and why garage/staircase are pinned to exactly their
    minimum). A master bedroom uses MASTER_BEDROOM_MAX_MULTIPLIER instead of
    the regular "bedroom" category's own multiplier - applied on top of its
    already-bumped minimum (see MASTER_BEDROOM_MIN_MULTIPLIER), so it has
    real room to grow larger via layout_floor()'s weight-based
    redistribution, not just a bigger guaranteed floor. Used by
    layout_floor() to cap how far a room can grow beyond its guaranteed
    minimum before the excess is redistributed elsewhere."""
    category = classify_room_category(room_name)
    min_area = min_area_for_room(room_name, unit, cars)
    if _is_master_bedroom(room_name):
        return min_area * MASTER_BEDROOM_MAX_MULTIPLIER
    return min_area * ROOM_MAX_MULTIPLIER[category]


def garage_min_area_sqm(cars: int, unit: str) -> float:
    """A garage's minimum footprint scales with vehicle count - side-by-side
    parking bays, not one fixed size regardless of how many cars were asked
    for. Width scales per car (ROOM_SIZE_SPECS_M["garage"]["min_width"] is a
    standard single-bay clearance); depth stays fixed (one car's length +
    door/pedestrian clearance) since multiple cars are assumed side-by-side,
    not stacked front-to-back."""
    cars = max(1, cars)
    spec = ROOM_SIZE_SPECS_M["garage"]
    width_m = spec["min_width"] * cars
    depth_m = spec["min_depth"]
    return to_plot_unit(width_m, unit) * to_plot_unit(depth_m, unit)


def staircase_dimensions(unit: str) -> tuple[float, float]:
    """Real (width, depth) for a staircase run, in the plot's own unit -
    mirrors garage_dimensions() exactly (same "give the layout engine a
    real, correctly-proportioned rectangle to carve out, not just an area
    target" reasoning). Added 2026-09-29 alongside floor_layout.py's real
    staircase carve-out (previously the staircase only ever got its real-
    world MINIMUM AREA guaranteed, same as any other room - its actual
    SHAPE was left to the generic weighted slicing algorithm, which could
    (and did, live-reproduced) squeeze it into a degenerate sliver, e.g.
    60ft wide x 0.6ft deep - unusable and almost certainly too thin for
    blueprint_svg.py's furniture-render size gate to draw the stair symbol
    at all."""
    spec = ROOM_SIZE_SPECS_M["staircase"]
    return to_plot_unit(spec["min_width"], unit), to_plot_unit(spec["min_depth"], unit)


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
