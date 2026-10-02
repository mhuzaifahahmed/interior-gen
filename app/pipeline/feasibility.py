"""Feasibility check for a floor's requested room program against its
available building footprint - built out of future-plans/feasibility-checker.md,
which had been scoped but deliberately deferred pending two decisions:
advisory-vs-hard-gate, and input-time-vs-post-generation. Both are now
resolved (explicit user decision, 2026-08-27): HARD GATE, checked right
before layout generation - an infeasible floor is never silently rendered,
it gets a real explanation instead (see app/pipeline/generate_house.py's
wiring).

Pure/deterministic - no I/O, no model calls, nothing to mock. Reuses
app/pipeline/room_specs.py's real-world minimum room sizes so this can
answer "does this actually fit" with the same numbers layout_floor() uses
to guarantee room sizes, not a second, inconsistent set of assumptions.
"""

from app.pipeline.room_specs import (
    EXTERIOR_WALL_THICKNESS_M,
    classify_room_category,
    min_area_for_room,
    preferred_area_for_room,
    to_plot_unit,
)

# 2026-10, Measurements Model refinement: the old flat 20% "circulation
# overhead" conflated TWO genuinely different things - real interior-
# partition wall area, and real hallway/clearance circulation - into one
# unexplained number, and compared against the RAW plot area rather than the
# real NET buildable envelope (after the exterior wall band is reserved -
# see floor_layout.py's own net-envelope reservation, which this now
# mirrors). This was a direct contributor to the "50x50 fits 15 bedrooms"
# bug: the old math added no hallway at all and measured against gross area.
#
# INTERIOR_WALL_AREA_ALLOWANCE_FRACTION intentionally mirrors
# floor_layout.INTERIOR_WALL_AREA_ALLOWANCE_FRACTION (same value, kept as a
# separate constant here rather than imported, to avoid feasibility.py
# depending on floor_layout.py's larger import surface for one number - if
# one changes, change the other to match).
INTERIOR_WALL_AREA_ALLOWANCE_FRACTION = 0.08
# Pure circulation/clearance overhead BEYOND the real hallway room now
# explicitly added below for bedroom-heavy programs (doorway swing
# clearance, minor inefficiency in corner rooms, etc.) - smaller than the
# old flat 20% since a real hallway area is no longer folded into this one
# unexplained fraction.
CIRCULATION_OVERHEAD_FRACTION = 0.12

# Matches floor_layout.MIN_ROOMS_FOR_CORRIDOR / HALLWAY_MIN_WIDTH_M - when
# the private-zone room count reaches this threshold, the REAL layout engine
# builds an actual hallway corridor (see floor_layout.py's module
# docstring), so the capacity math must account for one too, not just sum
# bare room areas with nothing connecting them.
_MIN_ROOMS_FOR_HALLWAY = 3
_HALLWAY_WIDTH_M = 1.2


def _estimated_hallway_area(bedrooms: int, bathrooms: int, unit: str) -> float:
    """A real, if approximate, hallway/corridor area - NOT a bare room-count
    guess. Width matches floor_layout.py's own HALLWAY_MIN_WIDTH_M; length
    approximates how much corridor a real _pack_row() packing would need by
    summing each served room's own real minimum width (each room occupies
    roughly its own min-width span along the corridor, the same way
    floor_layout.py's real packer works) - zero when there aren't enough
    private rooms to warrant a corridor at all (see floor_layout.py's own
    MIN_ROOMS_FOR_CORRIDOR threshold)."""
    if bedrooms + bathrooms < _MIN_ROOMS_FOR_HALLWAY:
        return 0.0
    from app.pipeline.room_specs import min_width_for_room

    corridor_length = sum(min_width_for_room(f"Bedroom {i + 1}", unit) for i in range(bedrooms)) + sum(
        min_width_for_room(f"Bathroom {i + 1}", unit) for i in range(bathrooms)
    )
    return to_plot_unit(_HALLWAY_WIDTH_M, unit) * corridor_length

# Fallback-only staircase overhead, in square meters - a real "Staircase"
# room is now injected into every floor's room list BEFORE this function is
# called (see generate_house.py), where it's counted like any other room via
# min_area_for_room()'s "staircase" category. This flat addition only fires
# when the room list DOESN'T already contain one (e.g. a caller that invokes
# check_feasibility() directly, bypassing the pipeline's injection - see the
# `has_staircase` check below) - a defensive backstop against undercounting,
# not the normal path, so it's never double-counted once the real room
# exists.
STAIRCASE_MIN_AREA_SQM = 4.5


def check_feasibility(
    rooms: list[dict], dimensions: dict, total_floors: int, garage_cars: int | None = None
) -> dict:
    """rooms: [{"name": str, "area": number}, ...] - the same structure
    app/pipeline/floor_layout.py's layout_floor() consumes for one floor.
    dimensions: {"length", "width", "unit"}. garage_cars, when given, sizes
    any room classified as a garage using the real per-car minimum instead
    of the flat single-car default (see room_specs.garage_min_area_sqm()).

    Returns {"verdict": "feasible"|"tight"|"not_feasible", "required_area",
    "available_area", "unit", "explanation"} - explanation is None when
    verdict is "feasible", otherwise a plain-language sentence describing
    the shortfall/tightness, quoting the actual numbers involved (never a
    generic message - the whole point is naming the real conflict).
    """
    unit = dimensions.get("unit") or "ft"
    length = float(dimensions.get("length") or 0)
    width = float(dimensions.get("width") or 0)
    # Real NET buildable envelope (2026-10) - the exterior wall band is real
    # wall, not room area, mirroring floor_layout.py's own net-envelope
    # reservation exactly so this check can never disagree with what the
    # layout engine actually does with the same plot.
    exterior = to_plot_unit(EXTERIOR_WALL_THICKNESS_M, unit)
    available_area = max(length - 2 * exterior, 0.0) * max(width - 2 * exterior, 0.0)

    if not rooms or available_area <= 0:
        return {
            "verdict": "feasible",
            "required_area": 0.0,
            "available_area": available_area,
            "unit": unit,
            "explanation": None,
        }

    required_room_area = sum(min_area_for_room(r.get("name") or "", unit, garage_cars) for r in rooms)
    # Explicit interior-partition wall area + a real hallway (when the
    # private-zone room count warrants one) + residual circulation
    # clearance - replacing the old flat 20% lump that accounted for none
    # of these explicitly.
    bedrooms = sum(1 for r in rooms if classify_room_category(str(r.get("name") or "")) == "bedroom")
    bathrooms = sum(1 for r in rooms if classify_room_category(str(r.get("name") or "")) == "bathroom")
    hallway_area = _estimated_hallway_area(bedrooms, bathrooms, unit)
    required_area = (
        required_room_area * (1 + INTERIOR_WALL_AREA_ALLOWANCE_FRACTION + CIRCULATION_OVERHEAD_FRACTION)
        + hallway_area
    )
    has_staircase = any(classify_room_category(str(r.get("name") or "")) == "staircase" for r in rooms)
    if total_floors and total_floors > 1 and not has_staircase:
        required_area += to_plot_unit(STAIRCASE_MIN_AREA_SQM, unit)

    if required_area > available_area:
        shortfall = required_area - available_area
        explanation = (
            f"The requested room program needs approximately {required_area:.0f} sq {unit} "
            f"(including minimum room sizes, walls/circulation, and staircase space), but the "
            f"available floor area is only {available_area:.0f} sq {unit} - about "
            f"{shortfall:.0f} sq {unit} short. Consider a larger plot, fewer rooms, or fewer floors."
        )
        return {
            "verdict": "not_feasible",
            "required_area": required_area,
            "available_area": available_area,
            "unit": unit,
            "explanation": explanation,
        }

    if required_area > available_area * 0.85:
        explanation = (
            f"The requested room program needs approximately {required_area:.0f} sq {unit} out of "
            f"{available_area:.0f} sq {unit} available - it fits, but rooms will be sized close to "
            f"their practical minimums."
        )
        return {
            "verdict": "tight",
            "required_area": required_area,
            "available_area": available_area,
            "unit": unit,
            "explanation": explanation,
        }

    return {
        "verdict": "feasible",
        "required_area": required_area,
        "available_area": available_area,
        "unit": unit,
        "explanation": None,
    }


# Real-time "which room counts fit my plot" guidance (2026-09-30, explicit
# user request): rather than only ever telling the user AFTER they submit
# that their room program doesn't fit (check_feasibility()'s existing hard
# gate), GET /api/house-room-requirements (app/main.py) exposes this table
# so the Build a House form can pre-filter the Bedrooms/floor and
# Bathrooms/floor dropdowns to only the counts that actually fit the
# plot the user has typed in, plus a real "you need at least YxY" message -
# same "real data, never guess" principle as every other deterministic
# guarantee in this codebase, just surfaced BEFORE submission instead of
# after.
#
# Deliberately checked against the GROUND FLOOR's room program, not an
# upper floor's - the ground floor carries MORE fixed overhead (Living
# Room + Kitchen + Dining Room, always guaranteed - see generate_house.py's
# _ensure_ground_floor_public_rooms()) than an upper floor (just one
# Sitting Area - see _ensure_upper_floor_lounge()), so for the SAME
# bedroom/bathroom count applied to every floor (the common "Same on every
# floor" input mode), the ground floor is always the binding constraint -
# if it fits there, it fits everywhere.
# 2026-10, Measurements Model refinement: lowered from range(0,16)/range(0,11)
# - the OLD ranges existed purely as a table-size ceiling, completely
# disconnected from whether that many rooms were remotely realistic (the
# real, reported "50x50 fits up to 15 bedrooms" bug - the honest math below
# permits far fewer before the table even reaches its own top row now).
BEDROOM_COUNT_RANGE = range(0, 9)
BATHROOM_COUNT_RANGE = range(0, 7)
# A real bedroom count rarely comes with zero bathrooms in practice - held
# fixed at this baseline while computing the BEDROOM table (and vice versa
# for the BATHROOM table) since the two dropdowns are otherwise independent
# inputs. A documented simplification, not a precise 2D feasibility solve
# across every (bedroom, bathroom) pair - the real, binding guarantee stays
# check_feasibility()'s own hard gate at generation time regardless.
_BASELINE_OTHER_ROOM_COUNT = 1


def min_area_for_room_count(category: str, count: int, other_count: int, total_floors: int, unit: str) -> float:
    """Minimum GROUND FLOOR area (in the plot's unit, squared) needed to
    feasibly and PRACTICALLY (not just barely) fit `count` rooms of
    `category` ("bedroom" or "bathroom") - `other_count` of the OTHER
    category held fixed alongside it - plus the mandatory ground-floor
    public rooms, a real hallway when warranted, and a staircase if
    `total_floors` > 1.

    2026-10 refinement: bedrooms/bathrooms use their real PREFERRED area
    (not the bare practical minimum) - a "fits" number built purely from
    bare minimums is what produced the "50x50 fits 15 bedrooms" bug (room
    after room at its absolute smallest, with no hallway connecting any of
    them). The public rooms (Living/Kitchen/Dining) stay at their own
    minimum - they're a fixed, always-present cost, not the thing being
    sized up by the count being tested. Shares the SAME wall-allowance/
    circulation/hallway math check_feasibility() itself uses (via
    _estimated_hallway_area() and the same overhead fractions), so this can
    never disagree with the real hard gate about what "fits" means."""
    bedrooms = count if category == "bedroom" else other_count
    bathrooms = count if category == "bathroom" else other_count
    fixed_rooms = [{"name": "Living Room"}, {"name": "Kitchen"}, {"name": "Dining Room"}]
    if total_floors and total_floors > 1:
        fixed_rooms.append({"name": "Staircase"})

    required_room_area = sum(min_area_for_room(r["name"], unit) for r in fixed_rooms)
    required_room_area += sum(preferred_area_for_room(f"Bedroom {i + 1}", unit) for i in range(bedrooms))
    required_room_area += sum(preferred_area_for_room(f"Bathroom {i + 1}", unit) for i in range(bathrooms))
    hallway_area = _estimated_hallway_area(bedrooms, bathrooms, unit)
    return required_room_area * (1 + INTERIOR_WALL_AREA_ALLOWANCE_FRACTION + CIRCULATION_OVERHEAD_FRACTION) + hallway_area


def room_count_requirements_table(total_floors: int, unit: str) -> dict:
    """Returns {"bedrooms": [{"count", "min_area", "min_side"}, ...],
    "bathrooms": [...]} across BEDROOM_COUNT_RANGE/BATHROOM_COUNT_RANGE -
    `min_area` is the real minimum ground-floor area needed for that many
    rooms (see min_area_for_room_count()); `min_side` is sqrt(min_area), a
    simple "you need at least a YxY plot" figure assuming a roughly square
    footprint (plots aren't always square, but a single number is far
    easier to communicate in a UI hint than a family of length x width
    pairs that all multiply to the same area)."""
    return {
        "bedrooms": [
            {
                "count": n,
                "min_area": round(
                    min_area_for_room_count("bedroom", n, _BASELINE_OTHER_ROOM_COUNT, total_floors, unit), 1
                ),
                "min_side": round(
                    min_area_for_room_count("bedroom", n, _BASELINE_OTHER_ROOM_COUNT, total_floors, unit) ** 0.5, 1
                ),
            }
            for n in BEDROOM_COUNT_RANGE
        ],
        "bathrooms": [
            {
                "count": n,
                "min_area": round(
                    min_area_for_room_count("bathroom", n, _BASELINE_OTHER_ROOM_COUNT, total_floors, unit), 1
                ),
                "min_side": round(
                    min_area_for_room_count("bathroom", n, _BASELINE_OTHER_ROOM_COUNT, total_floors, unit) ** 0.5, 1
                ),
            }
            for n in BATHROOM_COUNT_RANGE
        ],
    }
