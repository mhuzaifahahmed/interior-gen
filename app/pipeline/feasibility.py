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
