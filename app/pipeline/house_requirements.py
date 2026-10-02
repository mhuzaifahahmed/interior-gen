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

from app.pipeline.room_specs import _CATEGORY_KEYWORDS

_GARAGE_RE = re.compile(r"garage", re.IGNORECASE)
_UTILITY_RE = re.compile(r"utility|laundry", re.IGNORECASE)
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


def mentions_utility(text: str) -> bool:
    """Same "real data, never guess" gate as mentions_garage() - whether the
    user's own requirements text mentions a utility/laundry room at all.
    (2026-09-19) Previously a utility/laundry room appeared purely at
    Gemini's own probabilistic discretion (see generate_house.py's use of
    this alongside classify_room_category("laundry")) - inconsistent, and
    the room's own minimum size was too cramped to be useful when it did
    show up. This gate mirrors the garage strip/inject pattern exactly:
    strip an unrequested one, guarantee a requested one exists."""
    return bool(_UTILITY_RE.search(text or ""))


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


# Real, explicit per-floor room requests (2026-09-30, real user report: "you
# are hardcoding everything, make the pipeline... not hallucinate when I
# tell it to add a kitchen on 2nd floor or a dining room on 3rd floor").
# ROOM_LAYOUT_PROMPT_TEMPLATE (app/providers/gemini.py) has a strong
# baked-in "ground floor = public rooms, upper floors = bedrooms only"
# convention bias - it used to flatly say "NEVER place a garage or a
# kitchen on any floor above the ground floor," which silently overrode
# even an explicit user request, a real, confirmed source of "hallucinated
# away" rooms, not a one-off fluke. Same "real data, never guess" reasoning
# as mentions_garage()/parse_garage_cars() above, extended to arbitrary room
# types on arbitrary floors - this is the ACTUAL fix for the hallucination
# complaint: generate_house.py's _ensure_requested_rooms_by_floor()
# deterministically GUARANTEES each parsed (category, floor) pair exists,
# regardless of what Gemini's own response happened to include.
#
# Deliberately scoped to "amenity" room types a user might reasonably want
# ADDED to a specific floor beyond the default set - NOT bedroom/bathroom
# (already fully owned by the per-floor bedroom/bathroom COUNT system - see
# generate_house.py's _enforce_room_counts()) and NOT garage/staircase/foyer
# (already have their own dedicated, more specific deterministic handling
# elsewhere - a "garage on floor 2" request would be architecturally
# nonsensical and is intentionally not supported here). Expanded 2026-10
# (Measurements Model refinement, Part 1b's taxonomy) to cover the new
# room_specs.py categories a user might reasonably name per floor - dirty
# kitchen, pantry, prayer room, and the new "family" leisure category
# (den/media/game/gym/bar/studio...).
_EXTRA_ROOM_CATEGORIES = {
    "kitchen", "dining", "living", "study", "laundry", "closet",
    "dirty_kitchen", "pantry", "prayer", "family", "powder", "storage",
}
_EXTRA_ROOM_LABELS = {
    "kitchen": "Kitchen",
    "dining": "Dining Room",
    "living": "Living Room",
    "study": "Study",
    "laundry": "Laundry Room",
    "closet": "Closet",
    "dirty_kitchen": "Dirty Kitchen",
    "pantry": "Pantry",
    "prayer": "Prayer Room",
    "family": "Family Room",
    "powder": "Powder Room",
    "storage": "Storage Room",
}
_FLOOR_REF_RE = re.compile(r"floor\s*(\d+)|(\d+)\s*(?:st|nd|rd|th)\s*floor", re.IGNORECASE)
# Splits free text into clauses at common separators (comma/period/
# semicolon/"and") - a room keyword is paired with a floor reference only
# when BOTH appear in the SAME clause. Clause-based, not a sliding character
# window: a real gap found via a direct test showed a pure character-
# distance window picking the WRONG floor for "kitchen on floor 2, dining
# room on floor 3" (the "floor 2" sitting right before the comma was
# textually closer to "dining" than "floor 3" was, despite "dining room on
# floor 3" being the one clause that's actually about dining) - splitting on
# the comma first avoids this entirely.
_CLAUSE_SPLIT_RE = re.compile(r"[,.;]|\band\b", re.IGNORECASE)


def parse_extra_rooms_by_floor(text: str) -> list[tuple[str, str, int]]:
    """Scans free text for explicit "<room type> on/in floor N" (or "Nth
    floor <room type>") requests - returns deduplicated
    (category, canonical_label, floor_number) tuples, sorted by floor then
    category. Best-effort free-text parsing, not full NLP - only acts on
    requests phrased plainly enough that the room keyword and a floor
    reference share the same comma/period-separated clause; a vaguer
    request (no floor number in the same clause) is left to Gemini's own
    judgement, same as before this function existed."""
    if not text:
        return []
    results: set[tuple[str, str, int]] = set()
    for clause in _CLAUSE_SPLIT_RE.split(text):
        floor_match = _FLOOR_REF_RE.search(clause)
        if not floor_match:
            continue
        floor_number = int(floor_match.group(1) or floor_match.group(2))
        if floor_number <= 0:
            continue
        for category, keywords in _CATEGORY_KEYWORDS:
            if category not in _EXTRA_ROOM_CATEGORIES:
                continue
            label = _EXTRA_ROOM_LABELS[category]
            if any(re.search(r"\b" + re.escape(keyword) + r"\b", clause, re.IGNORECASE) for keyword in keywords):
                results.add((category, label, floor_number))
    return sorted(results, key=lambda r: (r[2], r[0]))


def extra_rooms_from_floor_text(floor_extras: list[str] | None) -> list[tuple[str, str, int]]:
    """Per-floor free-text box backstop (Part 5 of the Measurements Model
    refinement, 2026-10) - a SEPARATE input from the single shared `extras`
    box above: each box's text is already KNOWN to belong to a specific
    floor by its position in the list (index i = floor i+1), so unlike
    parse_extra_rooms_by_floor() this needs no floor-reference regex or
    clause-splitting - just a direct keyword scan per box. Reuses the SAME
    _CATEGORY_KEYWORDS/_EXTRA_ROOM_CATEGORIES/_EXTRA_ROOM_LABELS the shared
    box already uses, so "dirty kitchen" typed in either place resolves
    identically. Returns deduplicated (category, canonical_label,
    floor_number) tuples, the same shape parse_extra_rooms_by_floor()
    returns - both outputs are meant to be unioned and fed into
    generate_house.py's _ensure_requested_rooms_by_floor() together."""
    if not floor_extras:
        return []
    results: set[tuple[str, str, int]] = set()
    for i, text in enumerate(floor_extras):
        if not text:
            continue
        floor_number = i + 1
        for category, keywords in _CATEGORY_KEYWORDS:
            if category not in _EXTRA_ROOM_CATEGORIES:
                continue
            label = _EXTRA_ROOM_LABELS[category]
            if any(re.search(r"\b" + re.escape(keyword) + r"\b", text, re.IGNORECASE) for keyword in keywords):
                results.add((category, label, floor_number))
    return sorted(results, key=lambda r: (r[2], r[0]))
