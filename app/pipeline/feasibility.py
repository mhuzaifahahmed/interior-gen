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

from app.pipeline.room_specs import classify_room_category, min_area_for_room

# Rule-of-thumb overhead for interior walls + circulation (hallways,
# clearances) that a pure sum of room areas doesn't account for - real
# buildings never achieve 100% room-area efficiency. A flat fraction, not a
# room-by-room circulation solver (that's the "circulation optimization"
# item in the user's spec - a bigger, separate effort left for later).
CIRCULATION_OVERHEAD_FRACTION = 0.20

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
    available_area = length * width

    if not rooms or available_area <= 0:
        return {
            "verdict": "feasible",
            "required_area": 0.0,
            "available_area": available_area,
            "unit": unit,
            "explanation": None,
        }

    required_room_area = sum(min_area_for_room(r.get("name") or "", unit, garage_cars) for r in rooms)
    required_area = required_room_area * (1 + CIRCULATION_OVERHEAD_FRACTION)
    has_staircase = any(classify_room_category(str(r.get("name") or "")) == "staircase" for r in rooms)
    if total_floors and total_floors > 1 and not has_staircase:
        from app.pipeline.room_specs import to_plot_unit

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
BEDROOM_COUNT_RANGE = range(0, 16)
BATHROOM_COUNT_RANGE = range(0, 11)
# A real bedroom count rarely comes with zero bathrooms in practice - held
# fixed at this baseline while computing the BEDROOM table (and vice versa
# for the BATHROOM table) since the two dropdowns are otherwise independent
# inputs. A documented simplification, not a precise 2D feasibility solve
# across every (bedroom, bathroom) pair - the real, binding guarantee stays
# check_feasibility()'s own hard gate at generation time regardless.
_BASELINE_OTHER_ROOM_COUNT = 1


def min_area_for_room_count(category: str, count: int, other_count: int, total_floors: int, unit: str) -> float:
    """Minimum GROUND FLOOR area (in the plot's unit, squared) needed to
    feasibly fit `count` rooms of `category` ("bedroom" or "bathroom") -
    `other_count` of the OTHER category held fixed alongside it - plus the
    mandatory ground-floor public rooms and a staircase if `total_floors` >
    1. Reuses the exact same min_area_for_room()/CIRCULATION_OVERHEAD_FRACTION
    math check_feasibility() itself uses, so this can never disagree with
    the real hard gate about what "fits" means."""
    bedrooms = count if category == "bedroom" else other_count
    bathrooms = count if category == "bathroom" else other_count
    rooms = [{"name": "Living Room"}, {"name": "Kitchen"}, {"name": "Dining Room"}]
    rooms += [{"name": f"Bedroom {i + 1}"} for i in range(bedrooms)]
    rooms += [{"name": f"Bathroom {i + 1}"} for i in range(bathrooms)]
    if total_floors and total_floors > 1:
        rooms.append({"name": "Staircase"})
    required_room_area = sum(min_area_for_room(r["name"], unit) for r in rooms)
    return required_room_area * (1 + CIRCULATION_OVERHEAD_FRACTION)


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
