"""Real-world minimum/preferred/absolute-max room dimensions, used by
app/pipeline/floor_layout.py to guarantee every room in a generated layout is
actually usable (not an artifact of Gemini's relative-weight guess shrinking
it to near zero, or growing it unboundedly large) and by
app/pipeline/feasibility.py to check whether a requested room program can
physically fit a stated plot before any geometry is generated.

Pure data + pure classification - no I/O, nothing to mock. Deliberately a
SEPARATE keyword classifier from blueprint_svg.py's furniture-dispatch
`_draw_furniture()` - that one decides which furniture SYMBOLS to draw
inside an already-sized rectangle; this one decides how big the rectangle
should be allowed to get in the first place.

All dimensions are specified in METERS and converted to the plot's actual
unit ("ft" or "m") via to_plot_unit()/to_plot_unit_area() before use.

MAJOR REFINEMENT (2026-10, "Measurements Model" pass) - real, live-reported
problems this rewrite fixes:
  1. A 50x50ft plot's capacity math reported "fits up to 15 bedrooms" -
     fiction, since the old chart only guaranteed AREA, never WIDTH/DEPTH,
     and had no absolute size ceiling on anything except via a loose
     multiplier. This file now carries explicit min width/depth (a real
     physical floor, not just an area product) AND an absolute, finite
     maximum area per category (ABSOLUTE_MAX_AREA_SQM) independent of
     whatever multiplier a room's own minimum would otherwise allow.
  2. Doors rendered at one near-fixed size regardless of which room they
     led into - a bathroom got the same wide door as a bedroom, eating a
     short wall. DOOR_WIDTH_BY_CATEGORY_M + door_width_for_wall() give every
     renderer (blueprint_svg.py, blueprint_dxf.py, conditioning_image.py,
     kaggle_autocad.py) one shared, room-type-aware door size.
  3. All dimensions here are NET INTERIOR-CLEAR (the usable space inside a
     room's own walls) - EXTERIOR_WALL_THICKNESS_M/INTERIOR_WALL_THICKNESS_M
     are the single source of truth every renderer derives its wall
     rendering from, so a "100 sq ft bedroom" is genuinely ~100 sq ft of
     clear floor, not a gross rectangle that shrinks once walls are drawn.
     Masonry defaults (9in exterior / 4.5in interior) - this is a
     Pakistan-market product (PKR/Karachi), not US wood-frame construction.
  4. A real, much broader room-name taxonomy (ROOM_SYNONYMS +
     _CATEGORY_KEYWORDS) covering regional South-Asian terms (dirty kitchen,
     drawing room, baithak, sehan, puja room, mumty) alongside the standard
     Western vocabulary (den, powder room, walk-in closet, mudroom...) - see
     classify_room_category()'s docstring for the full graceful-resolution
     ladder. An unrecognized name is NEVER an error and never shown to the
     user as "unsupported" - it always resolves to a sensible category,
     worst case the generic "default" fallback.

Sources for the size/door/wall numbers (see the approved plan for the full
citation list): IRC minimum room size (70 sq ft / 7ft min dimension),
standard interior door widths (24-36in), IRC hallway clear width (36in),
typical US residential room-size surveys (bedroom/living/kitchen/dining/
bath), and standard masonry wall thickness conventions.
"""

METERS_TO_FEET = 3.280839895
_SQFT_PER_SQM = METERS_TO_FEET * METERS_TO_FEET  # area conversion factor


def to_plot_unit(meters: float, unit: str) -> float:
    """Converts a LINEAR meters value into the plot's stated unit ("ft" or
    "m", same values app/main.py's _compute_room_dimensions() already
    accepts)."""
    if (unit or "m").strip().lower().startswith("f"):
        return meters * METERS_TO_FEET
    return meters


def _area_to_plot_unit(sqm: float, unit: str) -> float:
    """Same as to_plot_unit() but for an AREA value (meters squared) - the
    conversion factor is squared, not linear."""
    if (unit or "m").strip().lower().startswith("f"):
        return sqm * _SQFT_PER_SQM
    return sqm


# ---- Wall thickness (2026-10, Part 0 of the Measurements Model refinement)
# ----
# Single source of truth for every renderer's wall thickness - previously
# blueprint_svg.py (WALL_HALF_THICKNESS_PX, a bare pixel constant with no
# exterior/interior distinction) and blueprint_dxf.py (WALL_THICKNESS_M=0.15,
# used for BOTH exterior and interior walls) each had their own, DISAGREEING
# wall thickness, and neither reserved real wall area when sizing rooms - a
# room's guaranteed "min area" was being measured on the GROSS rectangle, not
# what's left after the walls are drawn. Masonry defaults (not wood-frame) -
# this is a Pakistan-market (PKR/Karachi) product.
EXTERIOR_WALL_THICKNESS_M = 0.23  # standard 9in brick masonry
INTERIOR_WALL_THICKNESS_M = 0.115  # standard 4.5in (half-brick) partition


# min_width/min_depth: the smallest usable real-world NET-clear footprint for
# this room type - below this, the room stops being usable regardless of
# what Gemini's relative-weight guess would otherwise produce. Chosen so
# min_width * min_depth lands on the plan's researched "practical minimum
# area" per category (see the plan's size chart) - not a fixed/preferred
# size, see floor_layout.py's docstring for how remaining plot area beyond
# these floors is distributed by relative weight, then capped by
# ABSOLUTE_MAX_AREA_SQM below.
ROOM_SIZE_SPECS_M: dict[str, dict[str, float]] = {
    "bathroom": {"min_width": 1.5, "min_depth": 2.48},  # ~40 sqft full bath
    "powder": {"min_width": 1.4, "min_depth": 1.2},  # ~18 sqft half bath/WC
    "bedroom": {"min_width": 2.7, "min_depth": 3.44},  # ~100 sqft secondary bedroom
    "kitchen": {"min_width": 2.4, "min_depth": 2.9},  # ~75 sqft
    "dirty_kitchen": {"min_width": 2.1, "min_depth": 2.21},  # ~50 sqft secondary/spice kitchen
    "pantry": {"min_width": 1.2, "min_depth": 1.55},  # ~20 sqft (also scullery)
    "dining": {"min_width": 2.7, "min_depth": 3.44},  # ~100 sqft
    "living": {"min_width": 3.3, "min_depth": 4.22},  # ~150 sqft
    # A smaller-footprint leisure category (den/TV room/media/game/gym/bar/
    # hobby/studio) - distinct from "living" so these rooms get their own,
    # tighter absolute cap (see ABSOLUTE_MAX_AREA_SQM) rather than inheriting
    # living's generous 400 sqft ceiling.
    "family": {"min_width": 3.0, "min_depth": 3.72},  # ~120 sqft
    "study": {"min_width": 2.4, "min_depth": 2.71},  # ~70 sqft (also office/library)
    "prayer": {"min_width": 1.5, "min_depth": 1.7},  # ~26 sqft puja/meditation room
    # Bumped 2026-09-19 (real user report: "impractical", too tight for an
    # actual car) - 3.05x6.1m (~10x20ft) matches the commonly cited standard
    # single-car garage footprint. Per-car width, see
    # garage_min_area_sqm()/garage_dimensions().
    "garage": {"min_width": 3.05, "min_depth": 6.1},
    "laundry": {"min_width": 2.1, "min_depth": 1.55},  # ~35 sqft (also utility/mudroom)
    "closet": {"min_width": 1.2, "min_depth": 1.24},  # ~16 sqft (also wardrobe/dressing/linen)
    "storage": {"min_width": 1.5, "min_depth": 1.55},  # ~25 sqft box/attic/basement storage
    "mechanical": {"min_width": 1.2, "min_depth": 1.16},  # ~15 sqft boiler/plant/server/safe room
    "foyer": {"min_width": 1.2, "min_depth": 1.86},  # ~24 sqft, see min_depth_for_room()
    "staircase": {"min_width": 1.2, "min_depth": 2.7},  # compact stair run + landing
    "hallway": {"min_width": 1.1, "min_depth": 1.1},  # IRC 36in clear corridor minimum
    "porch": {"min_width": 1.8, "min_depth": 2.07},  # balcony/terrace/patio/deck/veranda
    "courtyard": {"min_width": 3.0, "min_depth": 3.1},  # sehan/atrium (open-to-sky core)
    "default": {"min_width": 2.1, "min_depth": 2.1},
}

# PREFERRED area (what this room type comfortably wants, not just its bare
# minimum) and ABSOLUTE MAX area (a real, finite ceiling independent of
# ROOM_MAX_MULTIPLIER - the actual fix for rooms ballooning without bound,
# e.g. the "9,451 sq ft Lounge" / "50x50 fits 15 bedrooms" bugs). Stored in
# square meters, converted via _area_to_plot_unit(). Categories pinned to
# exactly their minimum (garage, staircase, hallway) are intentionally
# omitted - nothing should ever grow them beyond their functional minimum.
PREFERRED_AREA_SQM: dict[str, float] = {
    "bathroom": 5.02, "powder": 2.32, "bedroom": 11.15, "kitchen": 11.15,
    "dirty_kitchen": 6.5, "pantry": 2.79, "dining": 13.01, "living": 22.3,
    "family": 16.72, "study": 9.29, "prayer": 3.72, "laundry": 4.65,
    "closet": 2.32, "storage": 3.72, "mechanical": 2.32, "foyer": 3.72,
    "porch": 7.43, "courtyard": 18.58, "default": 6.5,
}

ABSOLUTE_MAX_AREA_SQM: dict[str, float] = {
    "bathroom": 11.61, "powder": 4.65, "bedroom": 16.72, "kitchen": 17.84,
    "dirty_kitchen": 9.29, "pantry": 4.65, "dining": 23.41,
    "living": 37.16,  # ~400 sqft - kills the unbounded-living-room ballooning bug
    "family": 23.23,  # ~250 sqft - tighter than living, for den/media/game/gym rooms
    "study": 13.01, "prayer": 6.5, "laundry": 7.43, "closet": 4.65,
    "storage": 6.5, "mechanical": 3.72, "foyer": 9.01,
    "porch": 13.94, "courtyard": 37.16,
    "default": 18.58,  # ~200 sqft - the actual fix for an unrecognized room (e.g. an
    # LLM-invented "Hallway") absorbing unlimited redistributed area.
}

# Door widths by room category - a SINGLE shared source every renderer reads
# (previously blueprint_svg.py used one near-fixed pixel value and
# blueprint_dxf.py used one fixed 0.9m value, neither aware of which room
# type was on either side of the door - a bathroom got the same wide door as
# a bedroom, visually eating a short wall). Categories not listed fall back
# to "default" (a standard interior door).
DOOR_WIDTH_BY_CATEGORY_M: dict[str, float] = {
    "bathroom": 0.7,
    "powder": 0.6,
    "closet": 0.6,
    "pantry": 0.6,
    "storage": 0.6,
    "mechanical": 0.6,
    "default": 0.8,
}
FRONT_DOOR_WIDTH_M = 0.9


def door_width_for_wall(category_a: str, category_b: str, unit: str) -> float:
    """The real door width (in the plot's own unit) for a wall between two
    rooms of the given categories - the SMALLER/more-private room governs
    (a bathroom door stays narrow even next to a bedroom). Either category
    may be None/unrecognized - falls back to the standard "default" width."""
    base_a = DOOR_WIDTH_BY_CATEGORY_M.get(category_a or "", DOOR_WIDTH_BY_CATEGORY_M["default"])
    base_b = DOOR_WIDTH_BY_CATEGORY_M.get(category_b or "", DOOR_WIDTH_BY_CATEGORY_M["default"])
    return to_plot_unit(min(base_a, base_b), unit)


# MAXIMUM multiplier on a room's own minimum area (2026-09-04, real user
# critique: "the dining room occupies too much area relative to its
# function... every square meter should have a clear purpose"). Caps how far
# ABOVE its minimum a room is allowed to grow before the excess is
# redistributed elsewhere (see floor_layout.py's clamping pass). As of the
# 2026-10 refinement, max_area_for_room() takes the SMALLER of
# (min_area * multiplier) and ABSOLUTE_MAX_AREA_SQM - the multiplier alone is
# no longer the only ceiling, closing the real gap where a heavily-weighted
# room could still balloon on an unusually large plot even with a capped
# multiplier.
ROOM_MAX_MULTIPLIER: dict[str, float] = {
    "bathroom": 1.5, "powder": 1.5, "bedroom": 1.8, "kitchen": 2.2,
    "dirty_kitchen": 2.0, "pantry": 1.6, "dining": 1.8, "living": 4.0,
    "family": 2.5, "study": 1.8, "prayer": 1.6, "garage": 1.0,
    "laundry": 1.4, "closet": 1.4, "storage": 1.6, "mechanical": 1.4,
    "foyer": 1.6, "staircase": 1.0, "hallway": 1.0, "porch": 2.0,
    "courtyard": 2.0,
    "default": 4.0,
}

# Same category vocabulary as blueprint_svg.py's _draw_furniture() dispatch
# for the ORIGINAL categories (kept for consistency) - see module docstring
# for why it's a separate list rather than a shared import. Ordered: more
# specific/narrower phrases are listed BEFORE the broader category they'd
# otherwise be swallowed by (e.g. "powder"/"half bath" before "bathroom"'s
# bare "bath", "dirty kitchen" before "kitchen"'s bare "kitchen") - the first
# matching category wins, see classify_room_category().
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("powder", ("powder", "half bath", "half-bath", "guest toilet", "guest wc", "cloakroom")),
    ("bathroom", ("bath", "wc", "washroom", "toilet", "ensuite", "en-suite")),
    ("dirty_kitchen", ("dirty kitchen", "spice kitchen", "wet kitchen")),
    ("pantry", ("pantry", "larder", "scullery")),
    ("kitchen", ("kitchen",)),
    ("dining", ("dining",)),
    ("bedroom", ("bed", "bunk", "nursery")),
    (
        "family",
        (
            "den", "tv room", "tv lounge", "media room", "cinema", "home theater", "home theatre",
            "game room", "games room", "billiard", "arcade", "playroom", "play room",
            "gym", "fitness", "workout", "exercise room",
            "bar", "wine cellar", "tasting room",
            "hobby room", "craft room",
            "studio", "rec room", "recreation room",
        ),
    ),
    # "sitting" is deliberately kept mapped to "living" (NOT the new "family"
    # category above) - see generate_house.py's _ensure_upper_floor_lounge(),
    # which checks classify_room_category(...) == "living" to detect an
    # existing "Sitting Area" room it itself injects; moving it would break
    # that real, already-fixed ballooning-prevention check. "sitting" is also
    # deliberately NOT in floor_layout._PUBLIC_ZONE_KEYWORDS - see that
    # module for why a "Sitting Area" must size/furnish like a living room
    # without being treated as a ground-floor-style public room.
    ("living", ("living", "lounge", "sitting", "front room", "great room", "sunroom", "conservatory", "drawing", "formal")),
    ("prayer", ("prayer", "puja", "meditation", "namaz room")),
    ("study", ("study", "office", "library")),
    ("garage", ("garage", "carport")),
    ("laundry", ("laundry", "utility", "mudroom", "mud room")),
    ("closet", ("closet", "wardrobe", "dressing", "walk-in", "walk in", "linen")),
    ("storage", ("storage", "box room", "attic store", "basement store")),
    ("mechanical", ("mechanical", "boiler", "plant room", "server room", "safe room", "panic room")),
    ("foyer", ("foyer", "entry", "lobby", "vestibule", "entrance hall", "entryway")),
    ("hallway", ("hallway", "corridor", "passageway", "passage")),
    ("staircase", ("stair",)),
    ("porch", ("balcony", "terrace", "patio", "deck", "porch", "veranda", "verandah")),
    ("courtyard", ("courtyard", "atrium")),
)

# Regional/alternate-name synonyms (2026-10, Part 1b of the Measurements
# Model refinement) - real phrase -> canonical category, checked AFTER
# _CATEGORY_KEYWORDS finds no match. Deliberately includes South-Asian/
# Pakistan-market terms (this product's own market) alongside Western
# alternate names, so a real user's own vocabulary is understood, not just a
# narrow US-centric room list. Product stance: this normalization layer IS a
# feature ("understands 120+ room types including regional names"), never
# surfaced to the user as a limitation - see classify_room_category()'s own
# docstring for the full graceful-resolution ladder this feeds into.
ROOM_SYNONYMS: dict[str, str] = {
    "baithak": "living",
    "sehan": "courtyard",
    "mumty": "staircase",
    "puja": "prayer",
    "pooja": "prayer",
    "namaz": "prayer",
    "spare room": "bedroom",
    "servant quarters": "bedroom",
    "servant room": "bedroom",
    "maid room": "bedroom",
    "maids room": "bedroom",
    "maid's room": "bedroom",
    "jack and jill": "bathroom",
    "jack-and-jill": "bathroom",
    "wic": "closet",
    "owner's suite": "bedroom",
    "owners suite": "bedroom",
    "primary bedroom": "bedroom",
    "primary bath": "bathroom",
    "primary suite": "bedroom",
    "florida room": "living",
    "solarium": "living",
    "lanai": "porch",
    "breakfast nook": "dining",
    "breakfast room": "dining",
    "dinette": "dining",
    "great room": "living",
    # Broader "archetype" fallback signals (plan's resolution-ladder step 3) -
    # words not covered by any precise category keyword above, mapped to the
    # nearest sensible category rather than falling all the way to "default".
    "parlor": "living",
    "parlour": "living",
    "man cave": "family",
    "cave": "family",
    "nook": "family",
    "atelier": "family",
    "sanctuary": "prayer",
    "chapel": "prayer",
    "yoga": "family",
    "sauna": "family",
    "spa room": "family",
    "music room": "family",
    "snug": "living",
}


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
MASTER_BEDROOM_MAX_MULTIPLIER = 2.4
# Real, absolute ceiling (2026-10) independent of the multiplier above - a
# master bedroom on a very large plot should still top out at a real-world
# plausible size (~350 sqft), not grow unbounded just because the multiplier
# alone allows it.
MASTER_BEDROOM_ABSOLUTE_MAX_SQM = 32.5


def _is_master_bedroom(room_name: str) -> bool:
    return "master" in (room_name or "").lower() and classify_room_category(room_name) == "bedroom"


def classify_room_category(room_name: str) -> str:
    """Returns one of ROOM_SIZE_SPECS_M's keys - NEVER raises, and never
    reports a room as "unsupported" (an explicit product decision - see the
    module docstring). A graceful, deterministic 3-tier resolution ladder,
    NO model call involved at any tier:
      1. _CATEGORY_KEYWORDS - precise, canonical keywords (incl. the user's
         own full real-world taxonomy: dirty kitchen, powder room, walk-in
         closet, mudroom, prayer room, courtyard, porch variations, etc).
      2. ROOM_SYNONYMS - regional/alternate full-phrase names (baithak,
         sehan, mumty, puja room...) and broader archetype-signal words for
         names tier 1 doesn't recognize at all.
      3. "default" - a genuinely novel/invented room name (e.g. a "Cigar
         Lounge" nobody anticipated) still gets a real, finite, sensible
         size (see ABSOLUTE_MAX_AREA_SQM["default"]) and is placed/labelled
         with the user's own words - never an error, never a blocked
         generation."""
    name = (room_name or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        if any(keyword in name for keyword in keywords):
            return category
    for phrase, category in ROOM_SYNONYMS.items():
        if phrase in name:
            return category
    return "default"


def min_width_for_room(room_name: str, unit: str) -> float:
    """The governing minimum horizontal dimension (in the plot's unit) for
    this room - used by the Part-4 layout-quality scorer to flag a room
    that's wide enough in AREA but too narrow to actually be usable (a
    sliver)."""
    category = classify_room_category(room_name)
    return to_plot_unit(ROOM_SIZE_SPECS_M[category]["min_width"], unit)


def min_depth_for_room(room_name: str, unit: str) -> float:
    """The governing minimum depth (in the plot's unit). Foyer carries an
    explicitly researched minimum here (real functional entry depth, not
    just whatever min_width*some-area implies) - used by floor_layout.py's
    foyer carve-out (Part 2) to directly fix the "1-foot entrance" bug."""
    category = classify_room_category(room_name)
    return to_plot_unit(ROOM_SIZE_SPECS_M[category]["min_depth"], unit)


def preferred_area_for_room(room_name: str, unit: str, cars: int | None = None) -> float:
    """The comfortable/preferred area for this room (in the plot's unit,
    squared) - falls back to min_area_for_room() for categories with no
    distinct preferred value (garage/staircase/hallway, pinned to their
    functional minimum). Used by the Part-4 layout-quality scorer's soft
    imbalance check and by Part-3's honest capacity math."""
    category = classify_room_category(room_name)
    if category not in PREFERRED_AREA_SQM:
        return min_area_for_room(room_name, unit, cars)
    return _area_to_plot_unit(PREFERRED_AREA_SQM[category], unit)


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
    """Maximum sensible area (in the plot's unit, squared) for a room - the
    SMALLER of (its own minimum x ROOM_MAX_MULTIPLIER) and its real,
    ABSOLUTE_MAX_AREA_SQM ceiling (2026-10 - the actual fix for unbounded
    ballooning on a large plot, since a multiplier alone has no upper bound
    independent of how big the room's own minimum already is). A master
    bedroom uses MASTER_BEDROOM_MAX_MULTIPLIER/MASTER_BEDROOM_ABSOLUTE_MAX_SQM
    instead of the regular "bedroom" category's own numbers. Used by
    layout_floor() to cap how far a room can grow beyond its guaranteed
    minimum before the excess is redistributed elsewhere."""
    category = classify_room_category(room_name)
    min_area = min_area_for_room(room_name, unit, cars)
    if _is_master_bedroom(room_name):
        via_multiplier = min_area * MASTER_BEDROOM_MAX_MULTIPLIER
        return min(via_multiplier, _area_to_plot_unit(MASTER_BEDROOM_ABSOLUTE_MAX_SQM, unit))
    via_multiplier = min_area * ROOM_MAX_MULTIPLIER.get(category, ROOM_MAX_MULTIPLIER["default"])
    if category in ABSOLUTE_MAX_AREA_SQM:
        return min(via_multiplier, _area_to_plot_unit(ABSOLUTE_MAX_AREA_SQM[category], unit))
    return via_multiplier


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
    mirrors garage_dimensions(). See floor_layout.py for how this gets
    carved into a real, correctly-shaped room rather than just an area
    target."""
    spec = ROOM_SIZE_SPECS_M["staircase"]
    return to_plot_unit(spec["min_width"], unit), to_plot_unit(spec["min_depth"], unit)


def garage_dimensions(cars: int, unit: str) -> tuple[float, float]:
    """Real (width, depth) - not just area - a garage needs for `cars` to
    actually fit, in the plot's own unit. Used by floor_layout.py to carve
    out a real, correctly-proportioned rectangle for the garage before the
    general weighted algorithm runs."""
    cars = max(1, cars)
    spec = ROOM_SIZE_SPECS_M["garage"]
    return to_plot_unit(spec["min_width"] * cars, unit), to_plot_unit(spec["min_depth"], unit)
