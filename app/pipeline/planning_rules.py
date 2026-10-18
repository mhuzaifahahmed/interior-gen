"""Planning rules table (mask planner, Phase 1): plot bands, coverage and
setback targets, size hierarchy, shape/daylight rules, bonus-room priority.

Pure data + small lookups - no I/O, no imports from the layout engines.
Lengths are metres unless a function takes a `unit` argument (then it is
converted via room_specs.to_plot_unit). Band thresholds are written in sq ft
(the unit the plan was authored in) and converted on lookup.

The daylight constants live HERE; layout_quality.py re-exports them.
"""

from dataclasses import dataclass

from app.pipeline import room_specs

_SQFT_PER_SQM = room_specs.METERS_TO_FEET ** 2


@dataclass(frozen=True)
class PlotBand:
    name: str
    max_net_sqft: float  # exclusive upper bound; inf for the last band
    target_cov: float
    max_cov: float
    front_ft: float
    rear_ft: float
    side_ft: float


PLOT_BANDS: tuple[PlotBand, ...] = (
    PlotBand("XS", 1500.0, 0.80, 0.85, 5.0, 3.0, 0.0),
    PlotBand("S", 3000.0, 0.70, 0.75, 8.0, 5.0, 3.0),
    PlotBand("M", 8000.0, 0.60, 0.65, 12.0, 8.0, 5.0),
    PlotBand("L", 25000.0, 0.45, 0.55, 18.0, 12.0, 8.0),
    PlotBand("XL", float("inf"), 0.35, 0.45, 25.0, 20.0, 15.0),
)


def _is_feet(unit: str) -> bool:
    return (unit or "ft").strip().lower().startswith("f")


def plot_band(net_area: float, unit: str = "ft") -> PlotBand:
    """Band for a NET plot area given in sq `unit` ("ft" or "m"). Lower bound
    inclusive: exactly 1500 sq ft is band S."""
    sqft = net_area if _is_feet(unit) else net_area * _SQFT_PER_SQM
    for band in PLOT_BANDS:
        if sqft < band.max_net_sqft:
            return band
    return PLOT_BANDS[-1]


def band_setbacks(band: PlotBand, unit: str = "ft") -> dict[str, float]:
    """Minimum front/rear/side setbacks in the plot's own unit."""
    factor = 1.0 if _is_feet(unit) else 1.0 / room_specs.METERS_TO_FEET
    return {"front": band.front_ft * factor, "rear": band.rear_ft * factor, "side": band.side_ft * factor}


GARAGE_MIN_FRONT_SETBACK_M = 5.5  # only when a garage is requested

# Upper floors share the ground floor's footprint, so total coverage of the
# plot is governed by the ground floor; multi-storey houses aim a bit lower.
FLOOR_COUNT_COVERAGE_ADJUST: dict[int, float] = {1: 1.0, 2: 0.95, 3: 0.90}
FLOOR_COUNT_COVERAGE_ADJUST_DEFAULT = 0.85


def floor_count_coverage_adjust(total_floors: int) -> float:
    return FLOOR_COUNT_COVERAGE_ADJUST.get(total_floors, FLOOR_COUNT_COVERAGE_ADJUST_DEFAULT)


# (exclusive upper bound on private-room count, circulation fraction)
CIRCULATION_FRACTION_BY_PRIVATE_COUNT: tuple[tuple[float, float], ...] = (
    (3, 0.08),
    (6, 0.12),
    (float("inf"), 0.15),
)


def circulation_fraction(private_room_count: int) -> float:
    for bound, fraction in CIRCULATION_FRACTION_BY_PRIVATE_COUNT:
        if private_room_count < bound:
            return fraction
    return CIRCULATION_FRACTION_BY_PRIVATE_COUNT[-1][1]


HIERARCHY_RULES = {
    "living_over_dining_min_ratio": 1.15,  # living >= dining * 1.15
    "dining_cap_sqft": 180.0,  # unless the floor is large
    "dining_cap_waived_floor_sqft": 3000.0,
    "kitchen_le_living": True,
}

SHAPE_RULES = {
    "max_parts": 2,
    "secondary_max_area_fraction": 0.20,  # of the room's total area
    "secondary_max_side_fraction": 0.35,  # of the primary's matching side
}

DAYLIGHT_HARD_CATEGORIES = ("bedroom", "living", "kitchen")
DAYLIGHT_SOFT_CATEGORIES = ("dining", "study", "family")
DAYLIGHT_MIN_EDGE_M = 0.9
DAYLIGHT_RULES = {
    "hard": DAYLIGHT_HARD_CATEGORIES,
    "soft": DAYLIGHT_SOFT_CATEGORIES,
    "min_edge_m": DAYLIGHT_MIN_EDGE_M,
}

# floors: "ground" | "upper" | "any". per_suite: one per master suite.
BONUS_ROOM_PRIORITY: tuple[dict, ...] = (
    {"name": "Media Room", "category": "family", "max_count": 1, "floors": "ground", "per_suite": False},
    {"name": "Study", "category": "study", "max_count": 1, "floors": "ground", "per_suite": False},
    {"name": "Walk-in Closet", "category": "closet", "max_count": 1, "floors": "any", "per_suite": True},
    {"name": "Pantry", "category": "pantry", "max_count": 1, "floors": "ground", "per_suite": False},
    {"name": "Laundry", "category": "laundry", "max_count": 1, "floors": "ground", "per_suite": False},
    {"name": "Powder Room", "category": "powder", "max_count": 1, "floors": "ground", "per_suite": False},
    {"name": "Storage", "category": "storage", "max_count": 1, "floors": "any", "per_suite": False},
)
UPPER_FLOOR_EXCESS_FALLBACK = "Terrace"

# Phase 2 finding (scorecard, 2026-10): on the mask engine's ~40-cell grid, a
# bonus room whose TARGET is under ~6.5 sq m (~70 sq ft) - walk-in closet,
# pantry, laundry, powder room, storage - came out as a 1-cell sliver with no
# room for a door in most matrix cases (hard violations roughly doubled with
# them on). Until shape discipline (Phase 3) lands, the planner only adds
# bonus rooms at least this big. Entries stay in BONUS_ROOM_PRIORITY so
# lowering this re-enables them.
# Phase 3 re-test (2026-10, with shape discipline): 3.0 raised the scorecard
# but broke the quality gate on 3 floors (an undersized Bathroom on 60x80 3F),
# 0 broke 6 floors; neither grew big-plot coverage measurably (300x300 stays
# ~2%) - the house size there is capped by the program ceiling, not by
# bonus-room drops. Kept at 6.5.
BONUS_ROOM_MIN_TARGET_SQM = 6.5


# ---------------------------------------------------------------------------
# Big-plot size tiers (planner flag only). A plot band maps to a SIZE TIER;
# a tier scales each category's MAX area (and with it the target, which sits
# halfway between preferred and max - see target_from_bounds), so a house on
# a big plot gets bigger rooms instead of the same ~1,100 sq ft house.
# Bedrooms/bathrooms grow modestly; living/dining/kitchen/master/garage grow
# more. "standard" is a strict no-op (scale 1.0 everywhere, and callers use
# the plain room_specs numbers for it). Staircase/hallway/mechanical never
# scale (pinned / circulation). Mapping decided from the plot bands:
# XS/S/M = standard, L = spacious, XL = estate.
SIZE_TIER_BY_BAND: dict[str, str] = {"XS": "standard", "S": "standard", "M": "standard", "L": "spacious", "XL": "estate"}
SIZE_TIERS = ("standard", "spacious", "estate")

# Per-category multiplier on room_specs.max_area_for_room(). "master" is a
# master bedroom (name-aware). Missing categories scale 1.0.
TIER_MAX_SCALE: dict[str, dict[str, float]] = {
    "standard": {},
    "spacious": {
        "bedroom": 1.15, "master": 1.35, "bathroom": 1.10, "powder": 1.10,
        "living": 1.40, "dining": 1.35, "kitchen": 1.35, "garage": 1.30,
        "family": 1.30, "study": 1.25, "foyer": 1.40, "closet": 1.30,
        "pantry": 1.25, "laundry": 1.25, "storage": 1.25, "dirty_kitchen": 1.25,
        "prayer": 1.20, "porch": 1.50,
    },
    "estate": {
        "bedroom": 1.30, "master": 1.70, "bathroom": 1.20, "powder": 1.20,
        "living": 1.80, "dining": 1.70, "kitchen": 1.70, "garage": 1.60,
        "family": 1.60, "study": 1.50, "foyer": 1.80, "closet": 1.60,
        "pantry": 1.50, "laundry": 1.50, "storage": 1.50, "dirty_kitchen": 1.50,
        "prayer": 1.40, "porch": 2.00,
    },
}

# Scorecard bathroom size cap per tier (sq ft): a modest bathroom growth on
# big plots is part of the tier decision, so the "bathroom <= 80 sq ft" check
# grows with the tier instead of flagging every estate bathroom.
BATH_CAP_SQFT_BY_TIER: dict[str, float] = {"standard": 80.0, "spacious": 90.0, "estate": 100.0}


def size_tier(band_name: str) -> str:
    return SIZE_TIER_BY_BAND.get(band_name, "standard")


def _tier_key(room_name: str) -> str:
    cat = room_specs.classify_room_category(room_name)
    if cat == "bedroom" and "master" in (room_name or "").lower():
        return "master"
    return cat


def tier_scale(room_name: str, tier: str) -> float:
    return TIER_MAX_SCALE.get(tier, {}).get(_tier_key(room_name), 1.0)


def tier_max_area(room_name: str, unit: str, cars=None, tier: str = "standard") -> float:
    """Max area (plot unit squared) for this tier: room_specs' max x the
    tier's category scale, never below preferred. Standard tier == room_specs."""
    base = room_specs.max_area_for_room(room_name, unit, cars)
    if tier == "standard":
        return base
    return max(base * tier_scale(room_name, tier), room_specs.preferred_area_for_room(room_name, unit, cars))


def tier_target_area(room_name: str, unit: str, cars=None, tier: str = "standard") -> float:
    """Target for this tier: halfway between preferred and the tier max, so
    min <= preferred <= target <= max holds in every tier."""
    if tier == "standard":
        return room_specs.target_area_for_room(room_name, unit, cars)
    preferred = room_specs.preferred_area_for_room(room_name, unit, cars)
    return target_from_bounds(preferred, max(tier_max_area(room_name, unit, cars, tier), preferred))


# Bonus rooms per tier (never bedrooms or bathrooms). Same entry shape as
# BONUS_ROOM_PRIORITY; `max_count` is the allowed count of that CATEGORY on
# the floor (per master suite when per_suite). Spacious/estate add study/
# family/storage-type rooms that use spare area without changing the
# bedroom/bathroom program.
_SPACIOUS_EXTRA: tuple[dict, ...] = (
    {"name": "Library", "category": "study", "max_count": 2, "floors": "any", "per_suite": False},
    {"name": "Gym", "category": "family", "max_count": 2, "floors": "any", "per_suite": False},
    {"name": "Storage Room", "category": "storage", "max_count": 2, "floors": "any", "per_suite": False},
)
_ESTATE_EXTRA: tuple[dict, ...] = _SPACIOUS_EXTRA + (
    {"name": "Games Room", "category": "family", "max_count": 3, "floors": "any", "per_suite": False},
    {"name": "Prayer Room", "category": "prayer", "max_count": 1, "floors": "any", "per_suite": False},
)
BONUS_ROOM_PRIORITY_BY_TIER: dict[str, tuple[dict, ...]] = {
    "standard": BONUS_ROOM_PRIORITY,
    "spacious": BONUS_ROOM_PRIORITY + _SPACIOUS_EXTRA,
    "estate": BONUS_ROOM_PRIORITY + _ESTATE_EXTRA,
}

# Minimum TARGET (sq m) a bonus room must have to be added, per tier. The
# standard value is the Phase 2/3 finding (BONUS_ROOM_MIN_TARGET_SQM). Bigger
# tiers carry bigger targets (tier-scaled) and bigger houses, so the gate is
# re-evaluated per tier rather than globally (see the big-plot report).
# Re-evaluated: 3.5 sq m on spacious/estate (lets pantry/laundry/storage/
# prayer in) regressed the gate on 300x300_sparse (new door_too_narrow +
# narrow); kept 6.5 on every tier. Bigger tiers still offer more rooms
# (Library, Gym, Games Room have targets well above 6.5).
BONUS_ROOM_MIN_TARGET_SQM_BY_TIER: dict[str, float] = {"standard": 6.5, "spacious": 6.5, "estate": 6.5}

# Porch / verandah (planner flag, spacious/estate tiers only): when the band
# target footprint is bigger than what the enclosed program can use, the
# ground floor gets a covered-open "Verandah" (porch category, inside the
# footprint so the floor still tiles exactly) that also acts as the ground
# floor's remainder sink - the mirror of the upper floors' Terrace (which
# sits on top of it). Sized at most PORCH_MAX_FRACTION of the enclosed
# footprint and at most the tier's porch max.
GROUND_FLOOR_EXCESS_FALLBACK = "Verandah"
PORCH_TIERS = ("spacious", "estate")
PORCH_MAX_FRACTION = 0.15

# Estate cap (option 4): on L/XL plots, total built area (footprint x
# floors) <= bedrooms x this. Bedroom count comes from the program.
# Tuned in the 1000-1500 range against the planner quality gate: 1000, 1200,
# 1250, 1350-1500 each regressed 150x200_2f (a new `narrow` bedroom, a Living
# room squeezed or an oversized lounge - the mask engine is chaotic in
# footprint size there); 1300 holds every gate metric. Honest caveat: this
# is a tuned value on a sensitive engine, not a robust optimum.
BUILT_SQFT_PER_BEDROOM_CAP = 1300.0
BUILT_AREA_CAP_BANDS = ("L", "XL")

# Same-category balance (planner flag): rooms of one category on a floor
# (bedrooms especially; a master bedroom is its own group) end within this
# area ratio of each other when allocation allows it, never above their max.
# Pinned/circulation categories are exempt.
BALANCE_RATIO = 1.5
BALANCE_EXEMPT_CATEGORIES = ("staircase", "hallway", "garage", "porch", "courtyard")

# Shape / size caps scored under planner checks (opt-in).
BEDROOM_ASPECT_HARD = 2.5  # largest-part long/short above this: hard
BEDROOM_ASPECT_SOFT = 2.0  # above this: soft
STAIR_MAX_AREA_FACTOR = 2.0  # staircase area > this x its minimum: hard


def target_from_bounds(preferred: float, maximum: float) -> float:
    """TARGET sits halfway between preferred and max (equal to both when they
    coincide)."""
    return preferred + 0.5 * max(maximum - preferred, 0.0)


def target_area_sqm(category: str) -> float:
    """Target area (sq m) for a room category, derived from room_specs so it
    can never drift from the preferred/max numbers. Categories without a
    preferred/absolute-max entry (garage, staircase, hallway) fall back to
    their minimum. (A master bedroom is handled by room_specs.
    target_area_for_room, which is name-aware.)"""
    cat = category if category in room_specs.ROOM_SIZE_SPECS_M else "default"
    spec = room_specs.ROOM_SIZE_SPECS_M[cat]
    minimum = spec["min_width"] * spec["min_depth"]
    preferred = max(room_specs.PREFERRED_AREA_SQM.get(cat, minimum), minimum)
    via_multiplier = minimum * room_specs.ROOM_MAX_MULTIPLIER.get(cat, room_specs.ROOM_MAX_MULTIPLIER["default"])
    maximum = min(via_multiplier, room_specs.ABSOLUTE_MAX_AREA_SQM.get(cat, via_multiplier))
    return target_from_bounds(preferred, max(maximum, preferred))


# ---- Front-door room policy (Step 3, moved here as data in Step 3b) ----
# door_graph.front_door_room() only APPLIES these; edit the policy here.


@dataclass(frozen=True)
class FrontDoorTier:
    """One priority tier for the room that takes the front entrance.

    categories   room_specs.classify_room_category() values the tier accepts.
    lounge_hall  None = ignore the lounge/hall test; True = the room must be
                 lounge/hall-like (category in LOUNGE_HALL_CATEGORIES, or its
                 name contains one of LOUNGE_HALL_NAME_WORDS); False = it must
                 NOT be (so a plain "Living Room" is tier 1 but "Family
                 Lounge" / "Sitting Area" / "Hallway" fall to the next tier).
    """

    name: str
    categories: tuple[str, ...]
    lounge_hall: bool | None = None


# Priority order, first tier whose room touches the facing edge wins:
# foyer/entry -> living -> lounge/sitting/family/hall(way) -> dining.
FRONT_DOOR_PRIORITY_TIERS: tuple[FrontDoorTier, ...] = (
    FrontDoorTier("foyer", ("foyer",)),
    FrontDoorTier("living", ("living",), lounge_hall=False),
    FrontDoorTier("lounge_hall", ("living", "hallway", "default", "family"), lounge_hall=True),
    FrontDoorTier("dining", ("dining",)),
)
LOUNGE_HALL_CATEGORIES: tuple[str, ...] = ("hallway",)
LOUNGE_HALL_NAME_WORDS: tuple[str, ...] = ("lounge", "sitting", "family", "hall")

# Categories that may NEVER take the front door, whatever the tiers say:
# kitchens/pantry, utility(laundry)/storage/mechanical/closet, garage,
# staircase, study, porch/courtyard (outdoor), prayer, bedrooms, bathrooms and
# powder rooms. The den/media-room category ("family") is deliberately NOT
# here: it is only accepted through the lounge_hall tier, i.e. when the room
# is actually NAMED family/lounge/sitting/hall.
FRONT_DOOR_EXCLUDED_CATEGORIES: tuple[str, ...] = (
    "kitchen", "dirty_kitchen", "pantry", "laundry", "storage", "mechanical", "closet",
    "garage", "staircase", "study", "porch", "courtyard", "prayer",
    "bedroom", "bathroom", "powder",
)
