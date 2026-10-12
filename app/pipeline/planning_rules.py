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
