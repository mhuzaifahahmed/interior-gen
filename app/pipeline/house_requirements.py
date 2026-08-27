"""Deterministic parsing of garage/front-yard requirements out of the
composed free-text house requirements string (app/main.py's
`_compose_house_requirements()`, which folds the "extras" field into
HouseProject.prompt).

Why parse this ourselves instead of trusting Gemini's generate_room_layout()
to include a garage room / account for a yard on its own: an LLM's inclusion
of a garage room (or its own accounting for yard space) is probabilistic -
it might do it, might not, might size it arbitrarily. This gives the layout
engine (app/pipeline/floor_layout.py, app/pipeline/feasibility.py) a real,
deterministic signal to act on regardless of what the free-text call
happened to return that run - same "real data, never guess the hard
numbers" pattern already used for materials pricing/room dimensions
elsewhere in this project.

A dedicated Garage/Kitchen-per-floor checkbox pair in the frontend was tried
and dropped once already (see CLAUDE.md/app/main.py's
_compose_house_requirements() docstring) - this deliberately does NOT
reintroduce structured frontend fields; it only parses the SAME free-text
`extras` a user already types, same style as kaggle_autocad.py's
_parse_int_before_word() for bedroom/bathroom counts.

KNOWN LIMITATION, stated plainly: this project has no plot-orientation input
(no "which edge faces the road" field) - a real requirement for genuinely
placing a garage/front-yard on the correct edge. front_yard_depth() reserves
a strip along one edge by a fixed convention (the plot's "width"/y=0 edge,
matching floor_layout.py's own coordinate convention), not a verified
road-facing edge. Treat this as a real footprint reservation (the yard
genuinely isn't available to the building anymore), not as a guarantee of
correct real-world orientation - see future-plans if that's ever needed.
"""

import re

_GARAGE_RE = re.compile(r"garage", re.IGNORECASE)
_GARAGE_CAR_COUNT_RE = re.compile(r"(\d+)[\s-]*(?:car|vehicle)s?\s*garage|garage\s*(?:for)?\s*(\d+)", re.IGNORECASE)
_FRONT_YARD_RE = re.compile(r"front[\s-]*yard", re.IGNORECASE)
# The depth can be stated either before ("15ft front yard") or after
# ("front yard 15ft" / "front yard 5 meters") the phrase - try both orders.
_FRONT_YARD_DEPTH_BEFORE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ft|feet|foot|m|meters?|metres?)?\s*front[\s-]*yard", re.IGNORECASE
)
_FRONT_YARD_DEPTH_AFTER_RE = re.compile(
    r"front[\s-]*yard[^0-9]{0,20}(\d+(?:\.\d+)?)\s*(ft|feet|foot|m|meters?|metres?)?", re.IGNORECASE
)

# Applied only when "front yard" is mentioned with no explicit depth - a
# sensible default per-spec ("calculate a sensible value... rather than a
# universal hardcoded value") would ideally scale with plot size; this stays
# a flat, modest reservation to keep the feature's first pass simple and
# predictable. Expressed in meters, converted via room_specs.to_plot_unit().
DEFAULT_FRONT_YARD_DEPTH_M = 3.0


def mentions_garage(text: str) -> bool:
    return bool(_GARAGE_RE.search(text or ""))


def parse_garage_cars(text: str) -> int:
    """Returns the requested garage car count (default 1 whenever "garage"
    is mentioned at all without an explicit number) - only meaningful when
    mentions_garage() is True."""
    match = _GARAGE_CAR_COUNT_RE.search(text or "")
    if match:
        count_str = match.group(1) or match.group(2)
        if count_str:
            return max(1, int(count_str))
    return 1


def mentions_front_yard(text: str) -> bool:
    return bool(_FRONT_YARD_RE.search(text or ""))


def parse_front_yard_depth(text: str, unit: str) -> float | None:
    """Returns the front-yard depth in the plot's own unit - an explicit
    number if the user stated one (e.g. "15ft front yard", "front yard 4m"),
    otherwise DEFAULT_FRONT_YARD_DEPTH_M converted to that unit whenever
    "front yard" was mentioned at all. Returns None only when front yard
    wasn't mentioned at all - callers should check mentions_front_yard()
    separately if they need to distinguish "not mentioned" from "mentioned,
    using the default", though in practice this function already covers
    both real cases a caller needs."""
    from app.pipeline.room_specs import to_plot_unit

    if not mentions_front_yard(text):
        return None

    match = _FRONT_YARD_DEPTH_BEFORE_RE.search(text or "") or _FRONT_YARD_DEPTH_AFTER_RE.search(text or "")
    if match and match.group(1):
        value = float(match.group(1))
        stated_unit = (match.group(2) or "").lower()
        if stated_unit.startswith("f"):
            return value
        if stated_unit.startswith("m"):
            return to_plot_unit(value, unit) if unit.lower().startswith("f") else value
        # No unit stated with the number - assume the plot's own unit.
        return value

    return to_plot_unit(DEFAULT_FRONT_YARD_DEPTH_M, unit)
